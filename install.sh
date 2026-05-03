#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
TARGET="$BIN_DIR/dotfiles"

check_python() {
    if ! command -v python3 &>/dev/null; then
        echo "error: python3 not found" >&2
        exit 1
    fi

    version=$(python3 -c "import sys; print(sys.version_info >= (3, 11))")
    if [[ "$version" != "True" ]]; then
        echo "error: python 3.11+ required (found $(python3 --version))" >&2
        exit 1
    fi
}

check_pip() {
    if ! python3 -m pip --version &>/dev/null; then
        echo "error: pip not found — install it and try again" >&2
        exit 1
    fi
}

install_deps() {
    echo "installing dependencies..."
    python3 -m pip install --user -r "$REPO_DIR/requirements.txt" --quiet
}

setup_binary() {
    chmod +x "$REPO_DIR/dotfiles/cli.py"
    mkdir -p "$BIN_DIR"
    ln -sf "$REPO_DIR/dotfiles/cli.py" "$TARGET"
    echo "installed: $TARGET"
}

check_path() {
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo ""
        echo "warning: $BIN_DIR is not in your PATH"
        echo "add this to your ~/.bashrc or ~/.zshrc:"
        echo ""
        echo '  export PATH="$HOME/.local/bin:$PATH"'
        echo ""
        echo "then run: source ~/.bashrc  (or open a new terminal)"
    fi
}

check_python
check_pip
install_deps
setup_binary
check_path

echo ""
echo "done. to continue:"
echo "  dotfiles init --repo <path>    use an existing local repo"
echo "  dotfiles init --clone <url>    clone and configure a remote repo"
