import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip())


def add(repo: Path, paths: list[str]) -> None:
    _run(["git", "add", "--"] + paths, cwd=repo)


def commit(repo: Path, message: str) -> None:
    _run(["git", "commit", "-m", message], cwd=repo)


def push(repo: Path) -> None:
    _run(["git", "push"], cwd=repo)


def pull(repo: Path) -> None:
    _run(["git", "pull", "--rebase"], cwd=repo)


def rm(repo: Path, path: str) -> None:
    _run(["git", "rm", "-r", path], cwd=repo)


def clone(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", url, str(dest)], cwd=dest.parent)


def head_hash(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()
