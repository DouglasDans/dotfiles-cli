#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
import tomllib
from pathlib import Path

from dotfiles import config, git, linker, manifest

_STATE_DIR = Path("~/.config/dotfiles-cli").expanduser()


def _die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def _load_config() -> config.Config:
    try:
        return config.load()
    except FileNotFoundError as e:
        _die(str(e))


def _watcher_status() -> str:
    pid_file = _STATE_DIR / "watcher.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return f"running (PID {pid})"
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return "stopped"


def _link_status(source_path: Path, repo: Path, target: str) -> str:
    target_path = repo / target
    if source_path.is_symlink():
        if source_path.resolve() == target_path.resolve():
            return "OK"
        return "BROKEN"
    if not source_path.exists():
        return "MISSING"
    return "BROKEN"


def cmd_add(args: argparse.Namespace, cfg: config.Config) -> None:
    repo = Path(cfg.repo)
    suggested = linker.suggest_target(args.path)
    raw = input(f"Target [{suggested}]: ").strip()
    target = raw if raw else suggested

    try:
        linker.add_link(args.path, repo, target)
        git.add(repo, [target, "links.toml"])
        git.commit(repo, f"add: {target}")
        git.push(repo)
    except (FileNotFoundError, ValueError, git.GitError) as e:
        _die(str(e))

    print(f"added: {args.path} → {target}")


def cmd_unlink(args: argparse.Namespace, cfg: config.Config) -> None:
    repo = Path(cfg.repo)
    try:
        target, existed = linker.remove_link(args.path, repo)
    except ValueError as e:
        _die(str(e))

    if not existed:
        print(f"warning: {target} not found in repo — cleaning manifest only")

    try:
        if existed:
            git.rm(repo, target)
        git.add(repo, ["links.toml"])
        git.commit(repo, f"unlink: {target}")
        git.push(repo)
    except git.GitError as e:
        _die(str(e))

    print(f"unlinked: {args.path}")


def cmd_restore(args: argparse.Namespace, cfg: config.Config) -> None:
    repo = Path(cfg.repo)
    tags = args.tag if args.tag else None
    result = linker.restore(repo, tags=tags, force=args.force)

    for source in result.dir_conflicts:
        answer = input(f"{source} is a real directory. Delete and replace with symlink? [y/N] ").strip()
        if answer.lower() == "y":
            shutil.rmtree(source)
            links = manifest.load(repo)
            link = next(lnk for lnk in links if lnk.source == str(Path(source).expanduser()))
            Path(source).symlink_to(repo / link.target)
            result.created.append(source)

    parts = []
    if result.created:
        parts.append(f"{len(result.created)} created")
    if result.ok:
        parts.append(f"{len(result.ok)} ok")
    if result.missing:
        parts.append(f"{len(result.missing)} missing")
    if result.file_conflicts:
        parts.append(f"{len(result.file_conflicts)} conflict(s)")
        for src in result.file_conflicts:
            print(f"  conflict: {src}")

    print(", ".join(parts) if parts else "nothing to do")


def cmd_status(args: argparse.Namespace, cfg: config.Config) -> None:
    repo = Path(cfg.repo)
    links = manifest.load(repo)

    if not links:
        print("no links tracked")
    else:
        for link in links:
            status = _link_status(Path(link.source), repo, link.target)
            print(f"[{status}]  {link.source} → {link.target}")

    state_file = _STATE_DIR / "state.toml"
    try:
        with open(state_file, "rb") as f:
            state = tomllib.load(f)
        if state.get("last_commit"):
            print(f"last commit: {state['last_commit']} ({state.get('last_commit_at', '')})")
        if state.get("last_error"):
            print(f"last error:  {state['last_error']} ({state.get('last_error_at', '')})")
    except FileNotFoundError:
        pass

    print(f"watcher: {_watcher_status()}")


def cmd_watch(_args: argparse.Namespace) -> None:
    print("not implemented")


def cmd_init(args: argparse.Namespace) -> None:
    print("not implemented")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dotfiles", description="Manage dotfiles via symlinks")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a file to the dotfiles repo")
    p_add.add_argument("path")

    p_unlink = sub.add_parser("unlink", help="Remove a file from the dotfiles repo")
    p_unlink.add_argument("path")

    p_restore = sub.add_parser("restore", help="Recreate symlinks from the manifest")
    p_restore.add_argument("--tag", action="append", metavar="TAG")
    p_restore.add_argument("--force", action="store_true")

    sub.add_parser("status", help="Show link status and watcher state")

    sub.add_parser("watch", help="Run the filesystem watcher daemon")

    p_init = sub.add_parser("init", help="Configure and initialize dotfiles")
    init_group = p_init.add_mutually_exclusive_group(required=True)
    init_group.add_argument("--repo", metavar="PATH")
    init_group.add_argument("--clone", metavar="URL")

    parsed = parser.parse_args(argv)

    if parsed.command in ("add", "unlink", "restore", "status"):
        cfg = _load_config()

    if parsed.command == "add":
        cmd_add(parsed, cfg)
    elif parsed.command == "unlink":
        cmd_unlink(parsed, cfg)
    elif parsed.command == "restore":
        cmd_restore(parsed, cfg)
    elif parsed.command == "status":
        cmd_status(parsed, cfg)
    elif parsed.command == "watch":
        cmd_watch(parsed)
    elif parsed.command == "init":
        cmd_init(parsed)


if __name__ == "__main__":
    main()
