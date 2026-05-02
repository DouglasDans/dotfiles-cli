import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

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


# --- _flush ---

class TestFlush:
    def _make_handler(self, repo: Path) -> watcher._DotfilesEventHandler:
        return watcher._DotfilesEventHandler(repo, debounce_seconds=1)

    def test_does_nothing_when_pending_empty(self, tmp_path):
        handler = self._make_handler(tmp_path)
        with patch.object(git, "pull") as mock_pull:
            handler._flush()
        mock_pull.assert_not_called()

    def test_skips_when_rebase_in_progress(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._pending = {str(tmp_path / "zsh" / ".zshrc")}
        with patch.object(watcher, "_is_rebase_in_progress", return_value=True), \
             patch.object(git, "pull") as mock_pull, \
             patch.object(watcher, "_log") as mock_log:
            handler._flush()
        mock_pull.assert_not_called()
        mock_log.assert_called_once_with("rebase in progress — skipping commit cycle")

    def test_writes_error_on_pull_failure(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._pending = {str(tmp_path / "zsh" / ".zshrc")}
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull", side_effect=git.GitError("connection refused")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()
        updates = mock_write.call_args[0][0]
        assert "last_error" in updates
        assert "last_error_at" in updates

    def test_happy_path_commits_and_pushes(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        changed = repo / "zsh" / ".zshrc"
        changed.parent.mkdir()
        changed.write_text("content")

        handler = self._make_handler(repo)
        handler._pending = {str(changed)}

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "add") as mock_add, \
             patch.object(git, "commit") as mock_commit, \
             patch.object(git, "push") as mock_push, \
             patch.object(git, "head_hash", return_value="abc1234"), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()

        mock_add.assert_called_once_with(repo, ["zsh/.zshrc"])
        assert "auto:" in mock_commit.call_args[0][1]
        mock_push.assert_called_once()
        state = mock_write.call_args[0][0]
        assert state["last_commit"] == "abc1234"
        assert "last_commit_at" in state

    def test_skips_commit_when_paths_outside_repo(self, tmp_path):
        handler = self._make_handler(tmp_path)
        handler._pending = {"/some/other/path/file.txt"}
        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "add") as mock_add, \
             patch.object(git, "commit") as mock_commit:
            handler._flush()
        mock_add.assert_not_called()
        mock_commit.assert_not_called()

    def test_writes_error_on_commit_failure(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        changed = repo / "zsh" / ".zshrc"
        changed.parent.mkdir()
        changed.write_text("content")

        handler = self._make_handler(repo)
        handler._pending = {str(changed)}

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "add"), \
             patch.object(git, "commit", side_effect=git.GitError("nothing to commit")), \
             patch.object(watcher, "_write_state") as mock_write, \
             patch.object(watcher, "_log"):
            handler._flush()

        updates = mock_write.call_args[0][0]
        assert "last_error" in updates

    def test_restore_when_links_toml_in_local_changes(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        links_toml = repo / "links.toml"
        links_toml.write_text("")

        handler = self._make_handler(repo)
        handler._pending = {str(links_toml)}

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "add"), \
             patch.object(git, "commit"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()

        mock_restore.assert_called_once_with(repo, force=False)

    def test_restore_when_links_toml_updated_by_pull(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        links_toml = repo / "links.toml"

        changed = repo / "zsh" / ".zshrc"
        changed.parent.mkdir()
        changed.write_text("content")

        handler = self._make_handler(repo)
        handler._pending = {str(changed)}

        def fake_pull(_repo):
            links_toml.write_text("[[links]]")

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull", side_effect=fake_pull), \
             patch.object(git, "add"), \
             patch.object(git, "commit"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()

        mock_restore.assert_called_once_with(repo, force=False)

    def test_no_restore_when_links_toml_unchanged(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        changed = repo / "zsh" / ".zshrc"
        changed.parent.mkdir()
        changed.write_text("content")

        handler = self._make_handler(repo)
        handler._pending = {str(changed)}

        with patch.object(watcher, "_is_rebase_in_progress", return_value=False), \
             patch.object(git, "pull"), \
             patch.object(git, "add"), \
             patch.object(git, "commit"), \
             patch.object(git, "push"), \
             patch.object(git, "head_hash", return_value="abc"), \
             patch.object(watcher, "_write_state"), \
             patch.object(watcher, "_log"), \
             patch("dotfiles.watcher.linker.restore") as mock_restore:
            handler._flush()

        mock_restore.assert_not_called()
