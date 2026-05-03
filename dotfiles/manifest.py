import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Link:
    source: str
    target: str
    tags: list[str] = field(default_factory=list)


def _manifest_path(repo: Path) -> Path:
    return Path(repo) / "links.toml"


def _to_tilde(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home + "/"):
        return "~/" + path[len(home) + 1:]
    return path


def load(repo: Path) -> list[Link]:
    path = _manifest_path(repo)
    if not path.exists():
        return []

    with open(path, "rb") as f:
        data = tomllib.load(f)

    return [
        Link(
            source=str(Path(entry["source"]).expanduser()),
            target=entry["target"],
            tags=entry.get("tags", []),
        )
        for entry in data.get("links", [])
    ]


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(repo: Path, links: list[Link]) -> None:
    path = _manifest_path(repo)
    blocks = []
    for link in links:
        tags = "[" + ", ".join(_toml_str(t) for t in link.tags) + "]"
        blocks.append(
            f"[[links]]\n"
            f"source = {_toml_str(_to_tilde(link.source))}\n"
            f"target = {_toml_str(link.target)}\n"
            f"tags = {tags}\n"
        )
    with open(path, "w") as f:
        f.write("\n".join(blocks))


def add(repo: Path, link: Link) -> None:
    links = load(repo)
    links.append(link)
    save(repo, links)


def remove(repo: Path, source: str) -> None:
    normalized = str(Path(source).expanduser())
    links = load(repo)
    filtered = [l for l in links if l.source != normalized]
    if len(filtered) == len(links):
        raise ValueError(f"{source!r} not found in manifest")
    save(repo, filtered)
