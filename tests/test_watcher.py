import os
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import tomli_w
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)

from dotfiles import git, watcher


# --- PID lock ---

class TestPidLock:
    def test_acquires_lock_when_no_file(self, tmp_path):
        pid_file = tmp_path / "watcher.pid"
        watcher._acquire_pid_lock(pid_file)
        assert pid_file.read_text() == str(os.getpid())

    def test_acquires_lock_when_stale_pid(self, tmp_path):
        pid_file = tmp_path / "watcher.pid"
        pid_file.write_text("99999999")
        watcher._acquire_pid_lock(pid_file)
        assert pid_file.read_text() == str(os.getpid())

    def test_raises_when_active_pid(self, tmp_path):
        pid_file = tmp_path / "watcher.pid"
        pid_file.write_text(str(os.getpid()))
        with pytest.raises(RuntimeError, match="watcher already running"):
            watcher._acquire_pid_lock(pid_file)

    def test_release_removes_file(self, tmp_path):
        pid_file = tmp_path / "watcher.pid"
        pid_file.write_text("123")
        watcher._release_pid_lock(pid_file)
        assert not pid_file.exists()

    def test_release_no_error_when_missing(self, tmp_path):
        watcher._release_pid_lock(tmp_path / "watcher.pid")


# --- rebase detection ---

class TestRebaseDetection:
    def test_no_rebase(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert not watcher._is_rebase_in_progress(tmp_path)

    def test_rebase_merge(self, tmp_path):
        (tmp_path / ".git" / "rebase-merge").mkdir(parents=True)
        assert watcher._is_rebase_in_progress(tmp_path)

    def test_rebase_apply(self, tmp_path):
        (tmp_path / ".git" / "rebase-apply").mkdir(parents=True)
        assert watcher._is_rebase_in_progress(tmp_path)


# --- state write ---

class TestWriteState:
    def test_creates_state_file(self, tmp_path):
        with patch.object(watcher, "_STATE_FILE", tmp_path / "state.toml"):
            watcher._write_state({"last_commit": "abc123"})
            with open(tmp_path / "state.toml", "rb") as f:
                data = tomllib.load(f)
        assert data["last_commit"] == "abc123"

    def test_merges_with_existing_state(self, tmp_path):
        state_file = tmp_path / "state.toml"
        with open(state_file, "wb") as f:
            tomli_w.dump({"last_commit": "old", "last_error": "oops"}, f)
        with patch.object(watcher, "_STATE_FILE", state_file):
            watcher._write_state({"last_commit": "new"})
            with open(state_file, "rb") as f:
                data = tomllib.load(f)
        assert data["last_commit"] == "new"
        assert data["last_error"] == "oops"


# --- .git dir detection ---

class TestIsInGitDir:
    def test_true_for_path_inside_git_dir(self, tmp_path):
        assert watcher._is_in_git_dir(tmp_path, str(tmp_path / ".git" / "index"))

    def test_true_for_nested_path_inside_git_dir(self, tmp_path):
        path = str(tmp_path / ".git" / "refs" / "heads" / "main")
        assert watcher._is_in_git_dir(tmp_path, path)

    def test_false_for_path_outside_git_dir(self, tmp_path):
        path = str(tmp_path / "zsh" / ".zshrc")
        assert not watcher._is_in_git_dir(tmp_path, path)

    def test_false_for_path_outside_repo(self, tmp_path):
        assert not watcher._is_in_git_dir(tmp_path, "/some/other/path/.git/index")


# --- event filter (whitelist passed to observer.schedule) ---

class TestEventFilter:
    def test_excludes_read_only_event_types(self):
        assert FileOpenedEvent not in watcher._EVENT_FILTER
        assert FileClosedNoWriteEvent not in watcher._EVENT_FILTER

    def test_excludes_redundant_event_types(self):
        # closed-after-write always follows a modified event; dir-modified
        # fires for every change inside the dir — both are pure noise here
        assert FileClosedEvent not in watcher._EVENT_FILTER
        assert DirModifiedEvent not in watcher._EVENT_FILTER

    def test_includes_content_mutating_file_events(self):
        assert FileCreatedEvent in watcher._EVENT_FILTER
        assert FileModifiedEvent in watcher._EVENT_FILTER
        assert FileDeletedEvent in watcher._EVENT_FILTER
        assert FileMovedEvent in watcher._EVENT_FILTER

    def test_includes_structural_dir_events(self):
        assert DirCreatedEvent in watcher._EVENT_FILTER
        assert DirDeletedEvent in watcher._EVENT_FILTER
        assert DirMovedEvent in watcher._EVENT_FILTER


# --- on_any_event ---

class TestOnAnyEvent:
    def _make_handler(self, repo: Path, **kwargs) -> watcher._DotfilesEventHandler:
        kwargs.setdefault("debounce_seconds", 1)
        kwargs.setdefault("max_batch_seconds", 300)
        return watcher._DotfilesEventHandler(repo, **kwargs)

    def test_marks_dirty_and_schedules_flush(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(src_path=str(tmp_path / "zsh" / ".zshrc"), dest_path="")
        handler.on_any_event(event)
        assert handler._dirty
        assert handler._timer is not None
        handler._timer.cancel()

    def test_discards_event_inside_git_dir(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(src_path=str(tmp_path / ".git" / "index.lock"), dest_path="")
        handler.on_any_event(event)
        assert not handler._dirty
        assert handler._timer is None

    def test_discards_event_with_no_paths(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(src_path=None, dest_path=None)
        handler.on_any_event(event)
        assert not handler._dirty
        assert handler._timer is None

    def test_moved_event_with_dest_outside_git_dir_marks_dirty(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(
            src_path=str(tmp_path / ".git" / "tmp-object"),
            dest_path=str(tmp_path / "zsh" / ".zshrc"),
        )
        handler.on_any_event(event)
        assert handler._dirty
        handler._timer.cancel()

    def test_moved_event_entirely_inside_git_dir_discarded(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(
            src_path=str(tmp_path / ".git" / "a"),
            dest_path=str(tmp_path / ".git" / "b"),
        )
        handler.on_any_event(event)
        assert not handler._dirty
        assert handler._timer is None

    def test_directory_move_marks_dirty(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(
            src_path=str(tmp_path / "nvim"),
            dest_path=str(tmp_path / "nvim-old"),
        )
        handler.on_any_event(event)
        assert handler._dirty
        handler._timer.cancel()

    def test_spawns_no_subprocess_per_event(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(src_path=str(tmp_path / "zsh" / ".zshrc"), dest_path="")
        with patch("dotfiles.watcher.subprocess.run") as mock_run, \
             patch("dotfiles.git.subprocess.run") as mock_git_run:
            handler.on_any_event(event)
        mock_run.assert_not_called()
        mock_git_run.assert_not_called()
        handler._timer.cancel()

    def test_second_event_replaces_timer(self, tmp_path):
        handler = self._make_handler(tmp_path)
        event = SimpleNamespace(src_path=str(tmp_path / "a"), dest_path="")
        handler.on_any_event(event)
        first_timer = handler._timer
        handler.on_any_event(event)
        assert handler._timer is not first_timer
        handler._timer.cancel()


# --- debounce ceiling ---

class TestDebounceCeiling:
    def _make_handler(self, repo: Path, debounce: int, ceiling: int):
        return watcher._DotfilesEventHandler(
            repo, debounce_seconds=debounce, max_batch_seconds=ceiling
        )

    def test_first_event_uses_debounce_interval(self, tmp_path):
        handler = self._make_handler(tmp_path, debounce=30, ceiling=300)
        event = SimpleNamespace(src_path=str(tmp_path / "a"), dest_path="")
        handler.on_any_event(event)
        assert handler._timer.interval == pytest.approx(30, abs=0.5)
        handler._timer.cancel()

    def test_interval_capped_by_remaining_ceiling(self, tmp_path):
        handler = self._make_handler(tmp_path, debounce=30, ceiling=300)
        handler._batch_started_at = time.monotonic() - 290
        handler._dirty = True
        event = SimpleNamespace(src_path=str(tmp_path / "a"), dest_path="")
        handler.on_any_event(event)
        assert handler._timer.interval == pytest.approx(10, abs=0.5)
        handler._timer.cancel()

    def test_interval_never_negative(self, tmp_path):
        handler = self._make_handler(tmp_path, debounce=30, ceiling=300)
        handler._batch_started_at = time.monotonic() - 400
        handler._dirty = True
        event = SimpleNamespace(src_path=str(tmp_path / "a"), dest_path="")
        handler.on_any_event(event)
        assert handler._timer.interval == 0
        handler._timer.cancel()

    def test_flush_resets_batch_clock(self, tmp_path):
        handler = self._make_handler(tmp_path, debounce=1, ceiling=300)
        handler._dirty = True
        handler._batch_started_at = time.monotonic()
        with patch.object(watcher, "_is_rebase_in_progress", return_value=True), \
             patch.object(watcher, "_log"):
            handler._flush()
        assert handler._batch_started_at is None
        assert not handler._dirty


# --- _flush ---

class TestFlush:
    def _make_handler(self, repo: Path) -> watcher._DotfilesEventHandler:
        return watcher._DotfilesEventHandler(
            repo, debounce_seconds=1, max_batch_seconds=300
        )

    def test_does_nothing_when_not_dirty(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(git, "status_porcelain") as mock_status:
            handler._flush()
        mock_status.assert_not_called()

    def test_skips_when_rebase_in_progress(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=True), \
             patch.object(git, "status_porcelain") as mock_status, \
             patch.object(watcher, "_log") as mock_log:
            handler._flush()
        mock_status.assert_not_called()
        mock_log.assert_called_once_with("rebase in progress — skipping commit cycle")

    def test_skips_silently_when_working_tree_clean(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=[]), \
             patch.object(git, "add_all") as mock_add, \
             patch.object(git, "commit") as mock_commit, \
             patch.object(git, "pull") as mock_pull, \
             patch.object(git, "push") as mock_push, \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        mock_add.assert_not_called()
        mock_commit.assert_not_called()
        mock_pull.assert_not_called()
        mock_push.assert_not_called()
        mock_write.assert_not_called()

    def test_happy_path_commits_and_pushes(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all") as mock_add, \
             patch.object(git, "commit") as mock_commit, \
             patch.object(git, "pull"), \
             patch.object(git, "push") as mock_push, \
             patch.object(git, "head_hash", return_value="abc1234"), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        mock_add.assert_called_once_with(tmp_path)
        message = mock_commit.call_args[0][1]
        assert "auto:" in message
        assert "zsh/.zshrc" in message
        mock_push.assert_called_once()
        state = mock_write.call_args[0][0]
        assert state["last_commit"] == "abc1234"
        assert "last_commit_at" in state

    def test_commits_before_pulling_and_pushes_after(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        call_order = []
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all", side_effect=lambda *a, **k: call_order.append("add")), \
             patch.object(git, "commit", side_effect=lambda *a, **k: call_order.append("commit")), \
             patch.object(git, "pull", side_effect=lambda *a, **k: call_order.append("pull")), \
             patch.object(git, "push", side_effect=lambda *a, **k: call_order.append("push")), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"):
            handler._flush()
        assert call_order == ["add", "commit", "pull", "push"]

    def test_commit_message_summarizes_large_batches(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        changed = [f"file{i}" for i in range(6)]
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=changed), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit") as mock_commit, \
             patch.object(git, "pull"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"):
            handler._flush()
        assert mock_commit.call_args[0][1] == "auto: 6 files changed"

    def test_nothing_to_commit_is_not_an_error(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit", side_effect=git.GitError(
                 "On branch main\nnothing to commit, working tree clean"
             )), \
             patch.object(git, "pull") as mock_pull, \
             patch.object(git, "push") as mock_push, \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        mock_pull.assert_not_called()
        mock_push.assert_not_called()
        mock_write.assert_not_called()

    def test_writes_error_on_status_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", side_effect=git.GitError("not a git repo")), \
             patch.object(git, "add_all") as mock_add, \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        mock_add.assert_not_called()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates

    def test_writes_error_on_commit_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit", side_effect=git.GitError("fatal: unable to write new_index file")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates

    def test_writes_error_on_pull_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull", side_effect=git.GitError("connection refused")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates
        assert "last_error_at" in updates

    def test_writes_error_on_push_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull"), \
             patch.object(git, "push", side_effect=git.GitError("non-fast-forward")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates
        assert "last_error_at" in updates

    def test_restore_when_links_toml_in_local_changes(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["links.toml"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()
        mock_restore.assert_called_once_with(tmp_path, force=False)

    def test_restore_when_links_toml_updated_by_pull(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        links_toml = repo / "links.toml"

        handler = self._make_handler(repo)
        handler._dirty = True

        def fake_pull(_repo):
            links_toml.write_text("[[links]]")

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull", side_effect=fake_pull), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()
        mock_restore.assert_called_once_with(repo, force=False)

    def test_no_restore_when_links_toml_unchanged(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()
        mock_restore.assert_not_called()

    def test_flush_acquires_git_lock(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._dirty = True
        acquired = []

        def fake_pull(r):
            acquired.append(handler._git_lock.locked())

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "status_porcelain", return_value=["zsh/.zshrc"]), \
             patch.object(git, "add_all"), \
             patch.object(git, "commit"), \
             patch.object(git, "pull", side_effect=fake_pull), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"):
            handler._flush()
        assert acquired == [True]


# --- _sync ---

class TestSync:
    def _make_handler(self, repo: Path) -> watcher._DotfilesEventHandler:
        return watcher._DotfilesEventHandler(
            repo, debounce_seconds=1, max_batch_seconds=300
        )

    def test_pulls_and_pushes_on_call(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull") as mock_pull, \
             patch.object(git, "push") as mock_push, \
             patch.object(watcher, "_log"):
            handler._sync()
        mock_pull.assert_called_once_with(tmp_path)
        mock_push.assert_called_once_with(tmp_path)

    def test_skips_when_rebase_in_progress(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=True), \
             patch.object(git, "pull") as mock_pull, \
             patch.object(watcher, "_log") as mock_log:
            handler._sync()
        mock_pull.assert_not_called()
        mock_log.assert_called_once_with("rebase in progress — skipping sync")

    def test_writes_error_on_pull_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull", side_effect=git.GitError("timeout")), \
             patch.object(git, "push") as mock_push, \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._sync()
        mock_push.assert_not_called()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates
        assert "last_error_at" in updates

    def test_writes_error_on_push_failure_without_raising(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "push", side_effect=git.GitError("connection refused")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._sync()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates

    def test_restore_when_links_toml_updated_by_pull(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        links_toml = repo / "links.toml"

        def fake_pull(_repo):
            links_toml.write_text("[[links]]")

        handler = self._make_handler(repo)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull", side_effect=fake_pull), \
             patch.object(git, "push"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._sync()
        mock_restore.assert_called_once_with(repo, force=False)

    def test_no_restore_when_links_toml_unchanged(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        handler = self._make_handler(repo)
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "push"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._sync()
        mock_restore.assert_not_called()

    def test_sync_acquires_git_lock(self, tmp_path):
        handler = self._make_handler(tmp_path)
        acquired = []

        def fake_pull(r):
            acquired.append(handler._git_lock.locked())

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull", side_effect=fake_pull), \
             patch.object(git, "push"), \
             patch.object(watcher, "_log"):
            handler._sync()
        assert acquired == [True]
