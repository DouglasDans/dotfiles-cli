import pytest
from pathlib import Path
from dotfiles.config import Config, load, save


def test_load_returns_config_with_all_fields(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'repo = "/home/user/dotfiles"\ndebounce_seconds = 60\nsync_interval_seconds = 120\n'
    )

    config = load(config_file)

    assert config.repo == "/home/user/dotfiles"
    assert config.debounce_seconds == 60
    assert config.sync_interval_seconds == 120


def test_load_applies_default_debounce_when_missing(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('repo = "/home/user/dotfiles"\n')

    config = load(config_file)

    assert config.debounce_seconds == 30


def test_load_applies_default_sync_interval_when_missing(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('repo = "/home/user/dotfiles"\n')

    config = load(config_file)

    assert config.sync_interval_seconds == 300


def test_load_raises_with_orientador_message_when_file_not_found(tmp_path):
    config_file = tmp_path / "config.toml"

    with pytest.raises(FileNotFoundError, match="run 'dotfiles init' first"):
        load(config_file)


def test_load_expands_tilde_in_repo(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('repo = "~/dotfiles"\n')

    config = load(config_file)

    assert "~" not in config.repo
    assert config.repo.startswith("/")


def test_save_writes_config_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config = Config(repo="/home/user/dotfiles", debounce_seconds=45, sync_interval_seconds=120)

    save(config, config_file)

    result = load(config_file)
    assert result.repo == "/home/user/dotfiles"
    assert result.debounce_seconds == 45
    assert result.sync_interval_seconds == 120


def test_save_creates_parent_dirs(tmp_path):
    config_file = tmp_path / "nested" / "dir" / "config.toml"
    config = Config(repo="/home/user/dotfiles")

    save(config, config_file)

    assert config_file.exists()
