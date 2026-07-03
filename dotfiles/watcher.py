import hashlib
import os
import signal
import subprocess
import threading
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import tomli_w
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from dotfiles import git, linker

_STATE_DIR = Path("~/.config/dotfiles-cli").expanduser()
_PID_FILE = _STATE_DIR / "watcher.pid"
_STATE_FILE = _STATE_DIR / "state.toml"

# Whitelist of event types allowed into the observer queue. Read-only events
# (opened, closed_no_write) are emitted by watchdog >= 2.3 for every file READ;
# since every git subprocess reads the repo's own tracked gitconfig, letting
# them through creates a self-sustaining feedback loop that grows the queue
# without bound (observed: 9.6GB RSS, OOM kill).
_EVENT_FILTER = [
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
]

_COMMIT_MESSAGE_MAX_PATHS = 5


def _log(msg: str) -> None:
    subprocess.run(["logger", "-t", "dotfiles-cli", msg], capture_output=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_state(updates: dict[str, str]) -> None:
    state: dict = {}
    try:
        with open(_STATE_FILE, "rb") as f:
            state = tomllib.load(f)
    except FileNotFoundError:
        pass
    state.update(updates)
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "wb") as f:
        tomli_w.dump(state, f)


def _is_nothing_to_commit(message: str) -> bool:
    return "nothing to commit" in message


def _is_in_git_dir(repo: Path, src: str) -> bool:
    try:
        rel = Path(src).relative_to(repo)
    except ValueError:
        return False
    return rel.parts[:1] == (".git",)


def _is_rebase_in_progress(repo: Path) -> bool:
    return (
        (repo / ".git" / "rebase-merge").exists()
        or (repo / ".git" / "rebase-apply").exists()
    )


def _acquire_pid_lock(pid_file: Path) -> None:
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            raise RuntimeError(f"watcher already running (PID {pid})")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def _release_pid_lock(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def _links_toml_hash(repo: Path) -> bytes:
    try:
        return hashlib.md5((repo / "links.toml").read_bytes()).digest()
    except FileNotFoundError:
        return b""


def _commit_message(changed: list[str]) -> str:
    if len(changed) <= _COMMIT_MESSAGE_MAX_PATHS:
        return f"auto: {', '.join(changed)}"
    return f"auto: {len(changed)} files changed"


class _DotfilesEventHandler(FileSystemEventHandler):
    def __init__(self, repo: Path, debounce_seconds: int, max_batch_seconds: int = 300) -> None:
        self._repo = repo
        self._debounce_seconds = debounce_seconds
        self._max_batch_seconds = max_batch_seconds
        self._timer: threading.Timer | None = None
        self._dirty = False
        self._batch_started_at: float | None = None
        self._lock = threading.Lock()
        self._git_lock = threading.Lock()

    def on_any_event(self, event) -> None:
        paths = [
            p
            for p in (getattr(event, "src_path", None), getattr(event, "dest_path", None))
            if p
        ]
        if not paths:
            return
        if all(_is_in_git_dir(self._repo, p) for p in paths):
            return

        with self._lock:
            self._dirty = True
            now = time.monotonic()
            if self._batch_started_at is None:
                self._batch_started_at = now
            # cap the debounce so a continuous event stream can never
            # postpone the flush past max_batch_seconds
            remaining = self._max_batch_seconds - (now - self._batch_started_at)
            interval = max(0.0, min(float(self._debounce_seconds), remaining))
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(interval, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _sync(self) -> None:
        repo = self._repo
        if _is_rebase_in_progress(repo):
            _log("rebase in progress — skipping sync")
            return

        links_toml_hash_before = _links_toml_hash(repo)

        with self._git_lock:
            try:
                git.pull(repo)
            except git.GitError as e:
                _log(f"sync pull failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            # also retries any commit left unpushed by an earlier failed cycle
            try:
                git.push(repo)
            except git.GitError as e:
                _log(f"sync push failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})

        links_toml_hash_after = _links_toml_hash(repo)
        if links_toml_hash_before != links_toml_hash_after:
            linker.restore(repo, force=False)

    def _flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            self._batch_started_at = None
            self._timer = None

        repo = self._repo

        if _is_rebase_in_progress(repo):
            _log("rebase in progress — skipping commit cycle")
            return

        with self._git_lock:
            # git decides what changed — event paths are just the trigger
            try:
                changed = git.status_porcelain(repo)
            except git.GitError as e:
                _log(f"status failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            if not changed:
                return

            try:
                git.add_all(repo)
                git.commit(repo, _commit_message(changed))
            except git.GitError as e:
                if _is_nothing_to_commit(str(e)):
                    _log("nothing to commit")
                    return
                _log(f"commit failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            links_toml_hash_before = _links_toml_hash(repo)

            try:
                git.pull(repo)
            except git.GitError as e:
                _log(f"pull failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            try:
                git.push(repo)
            except git.GitError as e:
                _log(f"push failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            links_toml_hash_after = _links_toml_hash(repo)
            links_toml_updated_by_pull = links_toml_hash_before != links_toml_hash_after

            try:
                commit_hash = git.head_hash(repo)
            except git.GitError:
                commit_hash = "unknown"

            _write_state({"last_commit": commit_hash, "last_commit_at": _now()})
            _log(f"pushed {len(changed)} change(s)")

        if "links.toml" in changed or links_toml_updated_by_pull:
            linker.restore(repo, force=False)


def start(cfg) -> None:
    repo = Path(cfg.repo)
    pid_file = _PID_FILE

    _acquire_pid_lock(pid_file)

    def _cleanup(*_):
        _release_pid_lock(pid_file)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    handler = _DotfilesEventHandler(
        repo, cfg.debounce_seconds, max_batch_seconds=cfg.max_batch_seconds
    )

    def _schedule_sync() -> None:
        handler._sync()
        t = threading.Timer(cfg.sync_interval_seconds, _schedule_sync)
        t.daemon = True
        t.start()

    _schedule_sync()

    observer = Observer()
    observer.schedule(handler, str(repo), recursive=True, event_filter=_EVENT_FILTER)
    observer.start()

    try:
        observer.join()
    finally:
        _release_pid_lock(pid_file)
