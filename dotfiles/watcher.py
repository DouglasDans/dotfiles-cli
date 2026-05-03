import hashlib
import os
import signal
import subprocess
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import tomli_w
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from dotfiles import git, linker

_STATE_DIR = Path("~/.config/dotfiles-cli").expanduser()
_PID_FILE = _STATE_DIR / "watcher.pid"
_STATE_FILE = _STATE_DIR / "state.toml"


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


class _DotfilesEventHandler(FileSystemEventHandler):
    def __init__(self, repo: Path, debounce_seconds: int) -> None:
        self._repo = repo
        self._debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._git_lock = threading.Lock()

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        src = getattr(event, "src_path", None)
        if src is None:
            return
        with self._lock:
            self._pending.add(src)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._flush)
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

        links_toml_hash_after = _links_toml_hash(repo)
        if links_toml_hash_before != links_toml_hash_after:
            linker.restore(repo, force=False)

    def _flush(self) -> None:
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
            self._timer = None

        if not paths:
            return

        repo = self._repo

        if _is_rebase_in_progress(repo):
            _log("rebase in progress — skipping commit cycle")
            return

        links_toml_hash_before = _links_toml_hash(repo)

        with self._git_lock:
            try:
                git.pull(repo)
            except git.GitError as e:
                _log(f"pull failed: {e}")
                _write_state({"last_error": str(e), "last_error_at": _now()})
                return

            links_toml_hash_after = _links_toml_hash(repo)
            links_toml_updated_by_pull = links_toml_hash_before != links_toml_hash_after

            relative_paths: list[str] = []
            for p in paths:
                try:
                    relative_paths.append(str(Path(p).relative_to(repo)))
                except ValueError:
                    pass

            if relative_paths:
                try:
                    git.add(repo, relative_paths)
                    git.commit(repo, f"auto: {', '.join(relative_paths)}")
                    git.push(repo)
                except git.GitError as e:
                    _log(f"commit/push failed: {e}")
                    _write_state({"last_error": str(e), "last_error_at": _now()})
                    return

                try:
                    commit_hash = git.head_hash(repo)
                except git.GitError:
                    commit_hash = "unknown"

                _write_state({"last_commit": commit_hash, "last_commit_at": _now()})
                _log(f"pushed {len(relative_paths)} change(s)")

        links_toml_in_paths = str(repo / "links.toml") in paths
        if links_toml_in_paths or links_toml_updated_by_pull:
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

    handler = _DotfilesEventHandler(repo, cfg.debounce_seconds)

    def _schedule_sync() -> None:
        handler._sync()
        t = threading.Timer(cfg.sync_interval_seconds, _schedule_sync)
        t.daemon = True
        t.start()

    _schedule_sync()

    observer = Observer()
    observer.schedule(handler, str(repo), recursive=True)
    observer.start()

    try:
        observer.join()
    finally:
        _release_pid_lock(pid_file)
