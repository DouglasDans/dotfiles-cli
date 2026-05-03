import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotfiles import manifest


@dataclass
class RestoreResult:
    ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    file_conflicts: list[str] = field(default_factory=list)
    dir_conflicts: list[str] = field(default_factory=list)


def suggest_target(source: str) -> str:
    return Path(source).expanduser().name


def add_link(source: str, repo: Path, target: str, tags: list[str] | None = None) -> None:
    source_path = Path(source).expanduser()
    target_path = Path(repo) / target

    if not source_path.exists() and not source_path.is_symlink():
        raise FileNotFoundError(f"{source!r} does not exist")

    if source_path.is_symlink():
        raise ValueError(
            f"{source!r} is already a symlink — use 'dotfiles restore' if it's broken"
        )

    links = manifest.load(repo)
    if any(lnk.source == str(source_path) for lnk in links):
        raise ValueError(f"{source!r} is already in the manifest")

    if target_path.exists():
        raise ValueError(f"target {target!r} already exists in repo")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(target_path))

    try:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.symlink_to(target_path)
    except Exception:
        shutil.move(str(target_path), str(source_path))
        raise

    manifest.add(repo, manifest.Link(source=str(source_path), target=target, tags=tags or []))


def remove_link(source: str, repo: Path) -> tuple[str, bool]:
    source_path = Path(source).expanduser()
    normalized = str(source_path)

    links = manifest.load(repo)
    link = next((lnk for lnk in links if lnk.source == normalized), None)
    if link is None:
        raise ValueError(f"{source!r} is not a managed symlink")

    if source_path.exists() and not source_path.is_symlink():
        raise ValueError(
            f"{source!r} exists but is not a symlink — manual intervention required"
        )

    target_path = Path(repo) / link.target
    target_existed = target_path.exists()

    if source_path.is_symlink():
        source_path.unlink()

    if target_existed:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_path), str(source_path))

    manifest.remove(repo, normalized)
    return link.target, target_existed


def restore(
    repo: Path, tags: list[str] | None = None, force: bool = False
) -> RestoreResult:
    links = manifest.load(repo)
    if tags:
        links = [lnk for lnk in links if any(t in lnk.tags for t in tags)]

    result = RestoreResult()

    for link in links:
        source_path = Path(link.source)
        target_path = Path(repo) / link.target

        if not target_path.exists():
            result.missing.append(link.source)
            continue

        if source_path.is_symlink() and source_path.resolve() == target_path.resolve():
            result.ok.append(link.source)
            continue

        if source_path.is_dir() and not source_path.is_symlink():
            result.dir_conflicts.append(link.source)
            continue

        if source_path.exists() or source_path.is_symlink():
            if not force:
                result.file_conflicts.append(link.source)
                continue
            source_path.unlink()

        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.symlink_to(target_path)
        result.created.append(link.source)

    return result
