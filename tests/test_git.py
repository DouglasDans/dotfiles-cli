import subprocess
from pathlib import Path

import pytest

from dotfiles.git import (
    GitError,
    add,
    add_all,
    clone,
    commit,
    push,
    pull,
    rm,
    head_hash,
    status_porcelain,
)


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


# --- head_hash ---

def test_head_hash_returns_short_hash(local_repo):
    _commit_file(local_repo, "foo.txt")

    result = head_hash(local_repo)

    assert len(result) >= 7
    assert result.isalnum()


def test_head_hash_raises_on_empty_repo(tmp_path):
    _setup_repo(tmp_path)

    with pytest.raises(GitError):
        head_hash(tmp_path)


# --- status_porcelain ---

def test_status_porcelain_returns_empty_for_clean_repo(local_repo):
    _commit_file(local_repo, "foo.txt")

    assert status_porcelain(local_repo) == []


def test_status_porcelain_lists_modified_tracked_file(local_repo):
    _commit_file(local_repo, "foo.txt")
    (local_repo / "foo.txt").write_text("changed")

    assert status_porcelain(local_repo) == ["foo.txt"]


def test_status_porcelain_lists_untracked_file(local_repo):
    _commit_file(local_repo, "foo.txt")
    (local_repo / "new.txt").write_text("x")

    assert status_porcelain(local_repo) == ["new.txt"]


def test_status_porcelain_lists_deleted_tracked_file(local_repo):
    _commit_file(local_repo, "foo.txt")
    (local_repo / "foo.txt").unlink()

    assert status_porcelain(local_repo) == ["foo.txt"]


def test_status_porcelain_excludes_gitignored_file(local_repo):
    _commit_file(local_repo, ".gitignore", content="ignored/\n")
    (local_repo / "ignored").mkdir()
    (local_repo / "ignored" / "file.txt").write_text("x")

    assert status_porcelain(local_repo) == []


def test_status_porcelain_handles_path_with_spaces(local_repo):
    _commit_file(local_repo, "foo.txt")
    (local_repo / "my file.txt").write_text("x")

    assert status_porcelain(local_repo) == ["my file.txt"]


def test_status_porcelain_handles_staged_rename(local_repo):
    _commit_file(local_repo, "old.txt")
    subprocess.run(
        ["git", "mv", "old.txt", "new.txt"], cwd=local_repo, check=True, capture_output=True
    )

    result = status_porcelain(local_repo)

    assert result == ["new.txt"]


def test_status_porcelain_raises_outside_git_repo(tmp_path):
    with pytest.raises(GitError):
        status_porcelain(tmp_path)


# --- add_all ---

def test_add_all_stages_new_modified_and_deleted(local_repo):
    _commit_file(local_repo, "keep.txt")
    _commit_file(local_repo, "gone.txt")
    (local_repo / "keep.txt").write_text("changed")
    (local_repo / "gone.txt").unlink()
    # distinct content, or git detects the delete+create pair as a rename
    (local_repo / "new.txt").write_text("something else entirely")

    add_all(local_repo)

    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=local_repo, capture_output=True, text=True
    )
    staged = set(out.stdout.split())
    assert staged == {"keep.txt", "gone.txt", "new.txt"}


def test_add_all_respects_gitignore(local_repo):
    _commit_file(local_repo, ".gitignore", content="ignored/\n")
    (local_repo / "ignored").mkdir()
    (local_repo / "ignored" / "file.txt").write_text("x")

    add_all(local_repo)

    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=local_repo, capture_output=True, text=True
    )
    assert "ignored/file.txt" not in out.stdout


# --- clone ---

def test_clone_creates_local_copy(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=seed, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=seed, check=True, capture_output=True)
    _commit_file(seed, "README")
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=seed, check=True, capture_output=True)

    dest = tmp_path / "cloned"
    clone(str(remote), dest)

    assert dest.is_dir()
    assert (dest / ".git").is_dir()
    assert (dest / "README").exists()


def test_clone_raises_git_error_for_invalid_url(tmp_path):
    with pytest.raises(GitError):
        clone("/nonexistent/path/invalid.git", tmp_path / "dest")
