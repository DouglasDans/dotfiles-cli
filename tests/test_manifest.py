import pytest
from pathlib import Path
from dotfiles.manifest import Link, load, save, add, remove


def test_load_returns_empty_list_when_file_missing(tmp_path):
    links = load(tmp_path)

    assert links == []


def test_load_returns_links(tmp_path):
    (tmp_path / "links.toml").write_text(
        '[[links]]\nsource = "~/.zshrc"\ntarget = "zsh/.zshrc"\ntags = ["shell"]\n'
    )

    links = load(tmp_path)

    assert len(links) == 1
    assert links[0].target == "zsh/.zshrc"
    assert links[0].tags == ["shell"]


def test_load_expands_tilde_in_source(tmp_path):
    (tmp_path / "links.toml").write_text(
        '[[links]]\nsource = "~/.zshrc"\ntarget = "zsh/.zshrc"\ntags = []\n'
    )

    links = load(tmp_path)

    assert "~" not in links[0].source
    assert links[0].source.startswith("/")


def test_load_returns_empty_tags_when_missing(tmp_path):
    (tmp_path / "links.toml").write_text(
        '[[links]]\nsource = "~/.zshrc"\ntarget = "zsh/.zshrc"\n'
    )

    links = load(tmp_path)

    assert links[0].tags == []


def test_save_writes_links_toml(tmp_path):
    links = [Link(source="/home/user/.zshrc", target="zsh/.zshrc", tags=["shell"])]

    save(tmp_path, links)

    result = load(tmp_path)
    assert len(result) == 1
    assert result[0].source == "/home/user/.zshrc"
    assert result[0].target == "zsh/.zshrc"
    assert result[0].tags == ["shell"]


def test_save_writes_empty_list(tmp_path):
    save(tmp_path, [])

    result = load(tmp_path)
    assert result == []


def test_add_appends_link(tmp_path):
    link = Link(source="/home/user/.zshrc", target="zsh/.zshrc", tags=["shell"])

    add(tmp_path, link)

    result = load(tmp_path)
    assert len(result) == 1
    assert result[0].source == "/home/user/.zshrc"


def test_add_appends_to_existing(tmp_path):
    first = Link(source="/home/user/.zshrc", target="zsh/.zshrc", tags=[])
    second = Link(source="/home/user/.vimrc", target="vim/.vimrc", tags=[])
    add(tmp_path, first)

    add(tmp_path, second)

    result = load(tmp_path)
    assert len(result) == 2


def test_save_converts_absolute_home_to_tilde(tmp_path):
    home = str(Path.home())
    link = Link(source=f"{home}/.zshrc", target="zsh/.zshrc", tags=[])

    save(tmp_path, [link])

    raw = (tmp_path / "links.toml").read_text()
    assert "~/.zshrc" in raw
    assert home not in raw


def test_remove_removes_link_by_source(tmp_path):
    link = Link(source="/home/user/.zshrc", target="zsh/.zshrc", tags=[])
    add(tmp_path, link)

    remove(tmp_path, "/home/user/.zshrc")

    result = load(tmp_path)
    assert result == []


def test_remove_accepts_tilde_in_source(tmp_path):
    (tmp_path / "links.toml").write_text(
        '[[links]]\nsource = "~/.zshrc"\ntarget = "zsh/.zshrc"\ntags = []\n'
    )

    remove(tmp_path, "~/.zshrc")

    assert load(tmp_path) == []


def test_remove_raises_when_source_not_found(tmp_path):
    with pytest.raises(ValueError, match="not found in manifest"):
        remove(tmp_path, "/home/user/.zshrc")
