import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from dotfiles import manifest
from dotfiles.linker import RestoreResult, add_link, remove_link, restore, suggest_target


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "dotfiles"
    r.mkdir()
    return r


@pytest.fixture
def source_file(tmp_path):
    f = tmp_path / "home" / ".zshrc"
    f.parent.mkdir(parents=True)
    f.write_text("# zsh config")
    return f


def _make_entry(repo: Path, source: str, target: str, content: str = "content") -> None:
    target_path = repo / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content)
    manifest.add(repo, manifest.Link(source=source, target=target, tags=[]))


def _setup_managed_link(repo: Path, source_path: Path, target: str) -> None:
    target_file = repo / target
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("content")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.symlink_to(target_file)
    manifest.add(repo, manifest.Link(source=str(source_path), target=target, tags=[]))


# --- suggest_target ---

def test_suggest_target_strips_dot_from_file_at_home():
    assert suggest_target("~/.zshrc") == "zshrc"


def test_suggest_target_strips_dot_from_config_dir():
    assert suggest_target("~/.config/nvim") == "config/nvim"


def test_suggest_target_strips_dot_from_nested_file():
    assert suggest_target("~/.config/starship.toml") == "config/starship.toml"


def test_suggest_target_strips_dot_from_app_dir():
    assert suggest_target("~/.claude/settings.json") == "claude/settings.json"


def test_suggest_target_falls_back_to_basename_outside_home(tmp_path):
    path = tmp_path / ".myconfig"
    assert suggest_target(str(path)) == "myconfig"


# --- add_link ---

def test_add_link_raises_when_source_does_not_exist(repo, tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        add_link(str(tmp_path / "missing.txt"), repo, "missing.txt")


def test_add_link_raises_when_source_is_already_symlink(repo, tmp_path):
    link = tmp_path / "fake_link"
    link.symlink_to(tmp_path)

    with pytest.raises(ValueError, match="already a symlink"):
        add_link(str(link), repo, "fake_link")


def test_add_link_raises_when_already_in_manifest(repo, source_file):
    manifest.add(repo, manifest.Link(source=str(source_file), target="zsh/.zshrc", tags=[]))

    with pytest.raises(ValueError, match="already in the manifest"):
        add_link(str(source_file), repo, "zsh/.zshrc")


def test_add_link_raises_when_target_already_exists_in_repo(repo, source_file):
    (repo / ".zshrc").write_text("existing")

    with pytest.raises(ValueError, match="already exists in repo"):
        add_link(str(source_file), repo, ".zshrc")


def test_add_link_moves_file_to_repo(repo, source_file):
    add_link(str(source_file), repo, "zsh/.zshrc")

    assert (repo / "zsh" / ".zshrc").read_text() == "# zsh config"


def test_add_link_creates_symlink_at_source(repo, source_file):
    add_link(str(source_file), repo, "zsh/.zshrc")

    assert source_file.is_symlink()


def test_add_link_symlink_points_to_target_in_repo(repo, source_file):
    add_link(str(source_file), repo, "zsh/.zshrc")

    assert source_file.resolve() == (repo / "zsh" / ".zshrc").resolve()


def test_add_link_registers_in_manifest(repo, source_file):
    add_link(str(source_file), repo, "zsh/.zshrc")

    links = manifest.load(repo)
    assert len(links) == 1
    assert links[0].target == "zsh/.zshrc"


def test_add_link_stores_tags_in_manifest(repo, source_file):
    add_link(str(source_file), repo, "zsh/.zshrc", tags=["shell", "work"])

    links = manifest.load(repo)
    assert links[0].tags == ["shell", "work"]


def test_add_link_rollback_moves_file_back_on_symlink_failure(repo, source_file):
    with patch.object(Path, "symlink_to", side_effect=OSError("permission denied")):
        with pytest.raises(OSError):
            add_link(str(source_file), repo, "zsh/.zshrc")

    assert source_file.is_file()
    assert not source_file.is_symlink()
    assert not (repo / "zsh" / ".zshrc").exists()


# --- remove_link ---

def test_remove_link_raises_when_not_in_manifest(repo, tmp_path):
    with pytest.raises(ValueError, match="not a managed symlink"):
        remove_link(str(tmp_path / "home" / ".zshrc"), repo)


def test_remove_link_raises_when_source_is_regular_file(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    source.parent.mkdir()
    source.write_text("regular file")
    manifest.add(repo, manifest.Link(source=str(source), target=".zshrc", tags=[]))

    with pytest.raises(ValueError, match="not a symlink"):
        remove_link(str(source), repo)


def test_remove_link_removes_symlink(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _setup_managed_link(repo, source, "zsh/.zshrc")

    remove_link(str(source), repo)

    assert not source.is_symlink()


def test_remove_link_moves_file_back_to_source(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _setup_managed_link(repo, source, "zsh/.zshrc")

    remove_link(str(source), repo)

    assert source.is_file()
    assert source.read_text() == "content"


def test_remove_link_removes_from_manifest(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _setup_managed_link(repo, source, "zsh/.zshrc")

    remove_link(str(source), repo)

    assert manifest.load(repo) == []


def test_remove_link_accepts_target_instead_of_source(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _setup_managed_link(repo, source, "zsh/zshrc")

    target, existed = remove_link("zsh/zshrc", repo)

    assert target == "zsh/zshrc"
    assert existed is True
    assert not source.is_symlink()
    assert source.is_file()


def test_remove_link_returns_target_and_existed_true(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _setup_managed_link(repo, source, "zsh/.zshrc")

    target, existed = remove_link(str(source), repo)

    assert target == "zsh/.zshrc"
    assert existed is True


def test_remove_link_cleans_manifest_when_target_missing(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    source.parent.mkdir(parents=True)
    source.symlink_to(repo / "zsh" / ".zshrc")
    manifest.add(repo, manifest.Link(source=str(source), target="zsh/.zshrc", tags=[]))

    target, existed = remove_link(str(source), repo)

    assert target == "zsh/.zshrc"
    assert existed is False
    assert manifest.load(repo) == []
    assert not source.is_symlink()


# --- restore ---

def test_restore_creates_symlinks(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _make_entry(repo, str(source), "zsh/.zshrc")

    result = restore(repo)

    assert source.is_symlink()
    assert source.resolve() == (repo / "zsh" / ".zshrc").resolve()
    assert result.created == [str(source)]


def test_restore_skips_already_correct_symlink(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _make_entry(repo, str(source), "zsh/.zshrc")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(repo / "zsh" / ".zshrc")

    result = restore(repo)

    assert result.ok == [str(source)]
    assert result.created == []


def test_restore_skips_when_target_missing(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    manifest.add(repo, manifest.Link(source=str(source), target="zsh/.zshrc", tags=[]))

    result = restore(repo)

    assert result.missing == [str(source)]
    assert not source.exists()


def test_restore_skips_file_conflict_without_force(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    source.parent.mkdir(parents=True)
    source.write_text("existing file")
    _make_entry(repo, str(source), "zsh/.zshrc")

    result = restore(repo, force=False)

    assert result.file_conflicts == [str(source)]
    assert source.is_file() and not source.is_symlink()


def test_restore_overwrites_file_with_force(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    source.parent.mkdir(parents=True)
    source.write_text("existing file")
    _make_entry(repo, str(source), "zsh/.zshrc")

    result = restore(repo, force=True)

    assert source.is_symlink()
    assert result.created == [str(source)]


def test_restore_never_deletes_directory_even_with_force(repo, tmp_path):
    source = tmp_path / "home" / ".config" / "nvim"
    source.mkdir(parents=True)
    (source / "init.vim").write_text("config")
    _make_entry(repo, str(source), "nvim")

    result = restore(repo, force=True)

    assert source.is_dir()
    assert result.dir_conflicts == [str(source)]


def test_restore_filters_by_tags(repo, tmp_path):
    source_shell = tmp_path / "home" / ".zshrc"
    source_editor = tmp_path / "home" / ".vimrc"
    source_shell.parent.mkdir(parents=True, exist_ok=True)
    (repo / ".zshrc").write_text("zsh")
    (repo / ".vimrc").write_text("vim")
    manifest.add(repo, manifest.Link(source=str(source_shell), target=".zshrc", tags=["shell"]))
    manifest.add(repo, manifest.Link(source=str(source_editor), target=".vimrc", tags=["editor"]))

    restore(repo, tags=["shell"])

    assert source_shell.is_symlink()
    assert not source_editor.exists()


def test_restore_is_idempotent(repo, tmp_path):
    source = tmp_path / "home" / ".zshrc"
    _make_entry(repo, str(source), "zsh/.zshrc")

    result1 = restore(repo)
    result2 = restore(repo)

    assert result1.created == [str(source)]
    assert result2.ok == [str(source)]
    assert result2.created == []


def test_restore_creates_parent_dirs_for_source(repo, tmp_path):
    source = tmp_path / "deep" / "nested" / "path" / ".zshrc"
    _make_entry(repo, str(source), "zsh/.zshrc")

    restore(repo)

    assert source.is_symlink()


def test_restore_empty_manifest_is_noop(repo):
    result = restore(repo)

    assert result == RestoreResult()
