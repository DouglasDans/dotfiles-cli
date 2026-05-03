# dotfiles-cli

Manage your dotfiles with symlinks and Git. No intermediary tools, no special workflows — edit your files normally and let the sync happen automatically.

## How it works

- Your config files stay where they are, as symlinks pointing into a Git repo
- A background watcher detects changes and pushes them automatically (with debounce)
- On a new machine: clone your dotfiles repo, run `restore`, done

Two separate repositories by design:
- **dotfiles-cli** (this repo): the tool, shareable, no personal config
- **your dotfiles repo**: your actual configs + the `links.toml` manifest

## Requirements

- Python 3.11+
- Git
- systemd (for the background watcher)

## Installation

```bash
git clone https://github.com/your-user/dotfiles-cli.git ~/.dotfiles-cli
~/.dotfiles-cli/install.sh
```

The install script creates a symlink at `~/.local/bin/dotfiles`. Make sure `~/.local/bin` is in your `PATH`.

## Setup

**With an existing local repo:**
```bash
dotfiles init --repo ~/dotfiles
```

**Cloning from GitHub (new machine):**
```bash
dotfiles init --clone git@github.com:your-user/dotfiles.git
```

`init` does three things: saves the config, installs the systemd user service, and starts the watcher. After this you can forget the tool exists.

## Usage

### Track a new file or folder

```bash
dotfiles add ~/.zshrc
dotfiles add ~/.config/nvim
```

The CLI suggests a destination inside the repo based on the name. You can override it interactively.

```
  Source:  ~/.config/nvim
  Target:  ~/dotfiles/nvim/

  Confirm? [Y/n/other path]:
```

What happens: the file moves into the repo, a symlink is created at the original path, and it's registered in `links.toml`.

### Restore all symlinks (new machine)

```bash
dotfiles restore
dotfiles restore --tag editor   # only entries with that tag
dotfiles restore --force        # overwrite existing files (used automatically by init --clone)
```

Idempotent — safe to run multiple times.

### Remove a tracked file

```bash
dotfiles unlink ~/.zshrc
```

Removes the symlink, moves the file back to its original location, removes from manifest.

### Check status

```bash
dotfiles status
```

```
[OK]      ~/.zshrc
[OK]      ~/.config/nvim
[BROKEN]  ~/.config/alacritty → target missing in repo
[DRIFT]   repo has 2 unpushed commits
```

## Configuration

`~/.config/dotfiles-cli/config.toml` (created by `init`):

```toml
repo = "/home/user/dotfiles"
debounce_seconds = 30
```

`debounce_seconds` controls how long the watcher waits after the last file change before committing. Increase it if you use autosave heavily.

## Manifest format

`links.toml` lives inside your dotfiles repo and is versioned with it:

```toml
[[links]]
source = "~/.zshrc"
target = "zsh/.zshrc"
tags = ["shell"]

[[links]]
source = "~/.config/nvim"
target = "nvim/"
tags = ["editor"]
```

## Watcher behavior

The watcher runs as a systemd user service (`dotfiles-watch.service`). It:

- Watches the dotfiles repo directory for any changes
- Waits for inactivity (debounce) before committing
- Commits and pushes automatically
- Logs all activity to the systemd journal

```bash
journalctl --user -u dotfiles-watch -f
```

Push failures (no network, conflicts) are logged but do not crash the watcher. It retries on the next cycle.

## Typical workflow

```
# One-time setup
dotfiles init --repo ~/dotfiles

# Track something new
dotfiles add ~/.config/alacritty

# Edit normally — watcher handles the rest
nvim ~/.config/alacritty/alacritty.toml

# New machine — restore runs automatically after clone
dotfiles init --clone git@github.com:you/dotfiles.git
```

## License

MIT
