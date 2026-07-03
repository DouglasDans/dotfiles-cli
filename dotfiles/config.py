import tomllib
import tomli_w
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.config/dotfiles-cli/config.toml").expanduser()


@dataclass
class Config:
    repo: str
    debounce_seconds: int = 30
    sync_interval_seconds: int = 300
    max_batch_seconds: int = 300


def load(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"config not found — run 'dotfiles init' first"
        )

    config = Config(
        repo=str(Path(data["repo"]).expanduser().resolve()),
        debounce_seconds=data.get("debounce_seconds", 30),
        sync_interval_seconds=data.get("sync_interval_seconds", 300),
        max_batch_seconds=data.get("max_batch_seconds", 300),
    )
    if config.max_batch_seconds < config.debounce_seconds:
        raise ValueError(
            "max_batch_seconds must be >= debounce_seconds "
            f"({config.max_batch_seconds} < {config.debounce_seconds})"
        )
    return config


def save(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "repo": config.repo,
        "debounce_seconds": config.debounce_seconds,
        "sync_interval_seconds": config.sync_interval_seconds,
        "max_batch_seconds": config.max_batch_seconds,
    }
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
