import os
from pathlib import Path
from unittest.mock import patch, call

import pytest

from dotfiles.config import Config
from dotfiles.manifest import Link
from dotfiles.linker import RestoreResult
from dotfiles.git import GitError


def run(args):
    from dotfiles import cli
    return cli.main(args)


# --- add ---

def test_add_accepts_suggested_target_on_empty_input():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link") as mock_add, \
         patch("dotfiles.cli.git.add"), \
         patch("dotfiles.cli.git.commit"), \
         patch("dotfiles.cli.git.push"), \
         patch("builtins.input", return_value=""):
        run(["add", "~/.zshrc"])
    mock_add.assert_called_once_with("~/.zshrc", Path("/repo"), ".zshrc")


def test_add_uses_user_provided_target():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link") as mock_add, \
         patch("dotfiles.cli.git.add"), \
         patch("dotfiles.cli.git.commit"), \
         patch("dotfiles.cli.git.push"), \
         patch("builtins.input", return_value="shell/zshrc"):
        run(["add", "~/.zshrc"])
    mock_add.assert_called_once_with("~/.zshrc", Path("/repo"), "shell/zshrc")


def test_add_commits_target_and_manifest():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link"), \
         patch("dotfiles.cli.git.add") as mock_git_add, \
         patch("dotfiles.cli.git.commit") as mock_commit, \
         patch("dotfiles.cli.git.push"), \
         patch("builtins.input", return_value=""):
        run(["add", "~/.zshrc"])
    mock_git_add.assert_called_once_with(Path("/repo"), [".zshrc", "links.toml"])
    mock_commit.assert_called_once_with(Path("/repo"), "add: .zshrc")


def test_add_linker_error_exits_1(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link", side_effect=ValueError("already a symlink")), \
         patch("builtins.input", return_value=""):
        with pytest.raises(SystemExit) as exc:
            run(["add", "~/.zshrc"])
    assert exc.value.code == 1
    assert "already a symlink" in capsys.readouterr().err


def test_add_file_not_found_exits_1(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link", side_effect=FileNotFoundError("does not exist")), \
         patch("builtins.input", return_value=""):
        with pytest.raises(SystemExit) as exc:
            run(["add", "~/.zshrc"])
    assert exc.value.code == 1


def test_add_git_error_exits_1(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link"), \
         patch("dotfiles.cli.git.add", side_effect=GitError("network error")), \
         patch("builtins.input", return_value=""):
        with pytest.raises(SystemExit) as exc:
            run(["add", "~/.zshrc"])
    assert exc.value.code == 1


# --- unlink ---

def test_unlink_existed_true_calls_git_rm():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.remove_link", return_value=("zsh/.zshrc", True)), \
         patch("dotfiles.cli.git.rm") as mock_rm, \
         patch("dotfiles.cli.git.add") as mock_git_add, \
         patch("dotfiles.cli.git.commit") as mock_commit, \
         patch("dotfiles.cli.git.push"):
        run(["unlink", "~/.zshrc"])
    mock_rm.assert_called_once_with(Path("/repo"), "zsh/.zshrc")
    mock_git_add.assert_called_once_with(Path("/repo"), ["links.toml"])
    mock_commit.assert_called_once_with(Path("/repo"), "unlink: zsh/.zshrc")


def test_unlink_existed_false_skips_git_rm(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.remove_link", return_value=("zsh/.zshrc", False)), \
         patch("dotfiles.cli.git.rm") as mock_rm, \
         patch("dotfiles.cli.git.add"), \
         patch("dotfiles.cli.git.commit"), \
         patch("dotfiles.cli.git.push"):
        run(["unlink", "~/.zshrc"])
    mock_rm.assert_not_called()
    assert "warning" in capsys.readouterr().out.lower()


def test_unlink_existed_false_still_commits_manifest():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.remove_link", return_value=("zsh/.zshrc", False)), \
         patch("dotfiles.cli.git.rm"), \
         patch("dotfiles.cli.git.add") as mock_git_add, \
         patch("dotfiles.cli.git.commit") as mock_commit, \
         patch("dotfiles.cli.git.push"):
        run(["unlink", "~/.zshrc"])
    mock_git_add.assert_called_once_with(Path("/repo"), ["links.toml"])
    mock_commit.assert_called_once_with(Path("/repo"), "unlink: zsh/.zshrc")


def test_unlink_not_in_manifest_exits_1(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.remove_link", side_effect=ValueError("not a managed symlink")):
        with pytest.raises(SystemExit) as exc:
            run(["unlink", "~/.zshrc"])
    assert exc.value.code == 1


def test_unlink_not_a_symlink_exits_1():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.remove_link", side_effect=ValueError("not a symlink")):
        with pytest.raises(SystemExit) as exc:
            run(["unlink", "~/.zshrc"])
    assert exc.value.code == 1


# --- restore ---

def test_restore_prints_created_and_ok_counts(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult(ok=["a"], created=["b", "c"])):
        run(["restore"])
    out = capsys.readouterr().out
    assert "2" in out
    assert "1" in out


def test_restore_passes_tag_to_linker():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.restore") as mock_restore:
        mock_restore.return_value = RestoreResult()
        run(["restore", "--tag", "shell"])
    mock_restore.assert_called_once_with(Path("/repo"), tags=["shell"], force=False)


def test_restore_passes_force_to_linker():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.restore") as mock_restore:
        mock_restore.return_value = RestoreResult()
        run(["restore", "--force"])
    mock_restore.assert_called_once_with(Path("/repo"), tags=None, force=True)


def test_restore_dir_conflict_confirmed_creates_symlink(tmp_path):
    repo = tmp_path / "repo"
    (repo / "nvim").mkdir(parents=True)
    source = tmp_path / ".config" / "nvim"
    source.mkdir(parents=True)

    with patch("dotfiles.cli.config.load", return_value=Config(repo=str(repo))), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult(dir_conflicts=[str(source)])), \
         patch("dotfiles.cli.manifest.load", return_value=[Link(source=str(source), target="nvim", tags=[])]), \
         patch("builtins.input", return_value="y"):
        run(["restore"])

    assert source.is_symlink()
    assert source.resolve() == (repo / "nvim").resolve()


def test_restore_dir_conflict_refused_leaves_directory(tmp_path):
    source = tmp_path / ".config" / "nvim"
    source.mkdir(parents=True)

    with patch("dotfiles.cli.config.load", return_value=Config(repo=str(tmp_path / "repo"))), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult(dir_conflicts=[str(source)])), \
         patch("dotfiles.cli.manifest.load", return_value=[Link(source=str(source), target="nvim", tags=[])]), \
         patch("builtins.input", return_value="n"):
        run(["restore"])

    assert source.is_dir()
    assert not source.is_symlink()


def test_restore_file_conflicts_shown_in_output(capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult(file_conflicts=["~/.zshrc"])):
        run(["restore"])
    assert "~/.zshrc" in capsys.readouterr().out


# --- status ---

def test_status_empty_manifest(capsys, tmp_path):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.manifest.load", return_value=[]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])
    assert "no links" in capsys.readouterr().out.lower()


def test_status_ok_symlink(tmp_path, capsys):
    repo = tmp_path / "repo"
    target = repo / "zsh" / ".zshrc"
    target.parent.mkdir(parents=True)
    target.touch()
    source = tmp_path / ".zshrc"
    source.symlink_to(target)

    with patch("dotfiles.cli.config.load", return_value=Config(repo=str(repo))), \
         patch("dotfiles.cli.manifest.load", return_value=[Link(source=str(source), target="zsh/.zshrc", tags=[])]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])

    assert "[OK]" in capsys.readouterr().out


def test_status_broken_symlink(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / ".zshrc"
    source.symlink_to("/nonexistent/__dotfiles_test_broken__")

    with patch("dotfiles.cli.config.load", return_value=Config(repo=str(repo))), \
         patch("dotfiles.cli.manifest.load", return_value=[Link(source=str(source), target="zsh/.zshrc", tags=[])]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])

    assert "[BROKEN]" in capsys.readouterr().out


def test_status_missing_link(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("dotfiles.cli.config.load", return_value=Config(repo=str(repo))), \
         patch("dotfiles.cli.manifest.load", return_value=[Link(source=str(tmp_path / ".zshrc"), target="zsh/.zshrc", tags=[])]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])

    assert "[MISSING]" in capsys.readouterr().out


def test_status_watcher_stopped_when_no_pid_file(tmp_path, capsys):
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.manifest.load", return_value=[]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])
    assert "stopped" in capsys.readouterr().out.lower()


def test_status_watcher_running_with_active_pid(tmp_path, capsys):
    (tmp_path / "watcher.pid").write_text(str(os.getpid()))

    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.manifest.load", return_value=[]), \
         patch("dotfiles.cli._STATE_DIR", tmp_path):
        run(["status"])

    assert "running" in capsys.readouterr().out.lower()
