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
    mock_add.assert_called_once_with("~/.zshrc", Path("/repo"), ".zshrc", tags=[])


def test_add_uses_user_provided_target():
    with patch("dotfiles.cli.config.load", return_value=Config(repo="/repo")), \
         patch("dotfiles.cli.linker.add_link") as mock_add, \
         patch("dotfiles.cli.git.add"), \
         patch("dotfiles.cli.git.commit"), \
         patch("dotfiles.cli.git.push"), \
         patch("builtins.input", return_value="shell/zshrc"):
        run(["add", "~/.zshrc"])
    mock_add.assert_called_once_with("~/.zshrc", Path("/repo"), "shell/zshrc", tags=[])


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


# --- init --repo ---

def test_init_repo_path_not_exist_exits_1(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(["init", "--repo", str(tmp_path / "nonexistent")])
    assert exc.value.code == 1


def test_init_repo_not_a_git_repo_exits_1(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(["init", "--repo", str(tmp_path)])
    assert exc.value.code == 1


def test_init_repo_saves_config(tmp_path):
    repo = tmp_path / "dotfiles"
    (repo / ".git").mkdir(parents=True)
    with patch("dotfiles.cli.config.save") as mock_save, \
         patch("dotfiles.cli._install_service"):
        run(["init", "--repo", str(repo)])
    mock_save.assert_called_once()
    assert str(repo) in mock_save.call_args[0][0].repo


def test_init_repo_installs_service(tmp_path):
    repo = tmp_path / "dotfiles"
    (repo / ".git").mkdir(parents=True)
    with patch("dotfiles.cli.config.save"), \
         patch("dotfiles.cli._install_service") as mock_install:
        run(["init", "--repo", str(repo)])
    mock_install.assert_called_once()


def test_init_repo_install_failure_exits_1(tmp_path, capsys):
    repo = tmp_path / "dotfiles"
    (repo / ".git").mkdir(parents=True)
    with patch("dotfiles.cli.config.save"), \
         patch("dotfiles.cli._install_service", side_effect=RuntimeError("systemctl not found")):
        with pytest.raises(SystemExit) as exc:
            run(["init", "--repo", str(repo)])
    assert exc.value.code == 1
    assert "systemctl not found" in capsys.readouterr().err


# --- init --clone ---

def test_init_clone_calls_git_clone(tmp_path):
    dest = tmp_path / "dotfiles"
    with patch("dotfiles.cli.git.clone") as mock_clone, \
         patch("dotfiles.cli.config.save"), \
         patch("dotfiles.cli._install_service"), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult()), \
         patch("builtins.input", return_value=str(dest)):
        run(["init", "--clone", "git@github.com:user/dotfiles.git"])
    mock_clone.assert_called_once_with("git@github.com:user/dotfiles.git", dest)


def test_init_clone_default_destination():
    default_dest = Path("~/dotfiles").expanduser()
    with patch("dotfiles.cli.git.clone") as mock_clone, \
         patch("dotfiles.cli.config.save"), \
         patch("dotfiles.cli._install_service"), \
         patch("dotfiles.cli.linker.restore", return_value=RestoreResult()), \
         patch("builtins.input", return_value=""):
        run(["init", "--clone", "git@github.com:user/dotfiles.git"])
    mock_clone.assert_called_once_with("git@github.com:user/dotfiles.git", default_dest)


def test_init_clone_runs_restore_force(tmp_path):
    dest = tmp_path / "dotfiles"
    with patch("dotfiles.cli.git.clone"), \
         patch("dotfiles.cli.config.save"), \
         patch("dotfiles.cli._install_service"), \
         patch("dotfiles.cli.linker.restore") as mock_restore, \
         patch("builtins.input", return_value=str(dest)):
        mock_restore.return_value = RestoreResult()
        run(["init", "--clone", "git@github.com:user/dotfiles.git"])
    mock_restore.assert_called_once_with(dest, force=True)


def test_init_clone_git_error_exits_1(tmp_path, capsys):
    dest = tmp_path / "dotfiles"
    with patch("dotfiles.cli.git.clone", side_effect=GitError("authentication failed")), \
         patch("builtins.input", return_value=str(dest)):
        with pytest.raises(SystemExit) as exc:
            run(["init", "--clone", "git@github.com:user/dotfiles.git"])
    assert exc.value.code == 1
    assert "authentication failed" in capsys.readouterr().err


# --- _install_service ---

def test_install_service_writes_service_file_with_user_substituted(tmp_path):
    import getpass
    template = tmp_path / "template.service"
    template.write_text("[Service]\nExecStart=/home/{user}/.local/bin/dotfiles watch\n")
    service_dir = tmp_path / "systemd_user"

    with patch("dotfiles.cli._TEMPLATE_PATH", template), \
         patch("dotfiles.cli._SERVICE_DIR", service_dir), \
         patch("subprocess.run"):
        from dotfiles.cli import _install_service
        _install_service()

    service_file = service_dir / "dotfiles-watch.service"
    assert service_file.exists()
    content = service_file.read_text()
    assert "{user}" not in content
    assert getpass.getuser() in content


def test_install_service_calls_systemctl(tmp_path):
    template = tmp_path / "template.service"
    template.write_text("[Service]\nExecStart=/home/{user}/.local/bin/dotfiles watch\n")
    service_dir = tmp_path / "systemd_user"

    with patch("dotfiles.cli._TEMPLATE_PATH", template), \
         patch("dotfiles.cli._SERVICE_DIR", service_dir), \
         patch("subprocess.run") as mock_run:
        from dotfiles.cli import _install_service
        _install_service()

    commands = [c[0][0] for c in mock_run.call_args_list]
    assert any("daemon-reload" in cmd for cmd in commands)
    assert any("enable" in cmd for cmd in commands)
