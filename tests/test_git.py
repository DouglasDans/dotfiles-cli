import subprocess
from pathlib import Path

import pytest

from dotfiles.git import GitError, add, commit, push, pull, rm


def _setup_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)


def _commit_file(repo: Path, filename: str, content: str = "x", message: str = "init") -> None:
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def local_repo(tmp_path):
    repo = tmp_path / "repo"
    _setup_repo(repo)
    return repo


@pytest.fixture
def repo_with_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(remote), str(local)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=local, check=True, capture_output=True)
    _commit_file(local, "README")
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=local, check=True, capture_output=True)
    return local, remote


@pytest.fixture
def two_repos_with_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    def _clone(name: str) -> Path:
        repo = tmp_path / name
        subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
        return repo

    repo1 = _clone("repo1")
    _commit_file(repo1, "README")
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=repo1, check=True, capture_output=True)
    repo2 = _clone("repo2")
    return repo1, repo2


# --- add ---

def test_add_stages_file(local_repo):
    (local_repo / "foo.txt").write_text("hello")

    add(local_repo, ["foo.txt"])

    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=local_repo, capture_output=True, text=True
    )
    assert "foo.txt" in out.stdout


def test_add_raises_git_error_for_nonexistent_path(local_repo):
    with pytest.raises(GitError):
        add(local_repo, ["nonexistent.txt"])


# --- commit ---

def test_commit_creates_commit(local_repo):
    (local_repo / "foo.txt").write_text("hello")
    subprocess.run(["git", "add", "foo.txt"], cwd=local_repo, check=True, capture_output=True)

    commit(local_repo, "add foo")

    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=local_repo, capture_output=True, text=True
    )
    assert "add foo" in out.stdout


def test_commit_raises_git_error_when_nothing_staged(local_repo):
    with pytest.raises(GitError):
        commit(local_repo, "empty")


# --- push ---

def test_push_sends_commit_to_remote(repo_with_remote):
    local, remote = repo_with_remote
    _commit_file(local, "new.txt", message="add new")

    push(local)

    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=remote, capture_output=True, text=True
    )
    assert "add new" in out.stdout


# --- pull ---

def test_pull_fetches_changes_from_remote(two_repos_with_remote):
    repo1, repo2 = two_repos_with_remote
    _commit_file(repo1, "shared.txt", message="add shared")
    subprocess.run(["git", "push"], cwd=repo1, check=True, capture_output=True)

    pull(repo2)

    assert (repo2 / "shared.txt").exists()


# --- rm ---

def test_rm_removes_file_from_tracking(local_repo):
    _commit_file(local_repo, "foo.txt")

    rm(local_repo, "foo.txt")

    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=local_repo, capture_output=True, text=True
    )
    assert "foo.txt" in out.stdout
    assert not (local_repo / "foo.txt").exists()


def test_rm_raises_git_error_for_untracked_file(local_repo):
    (local_repo / "untracked.txt").write_text("hello")

    with pytest.raises(GitError):
        rm(local_repo, "untracked.txt")
