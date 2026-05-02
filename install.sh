#!/bin/sh
set -eu

REPO_URL="${REPO_URL:-https://github.com/spkskx/3dsutil.git}"
INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.local/lib/3dsutil.py}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
PYTHON="${PYTHON:-python3}"

say() {
    printf '%s\n' "$*"
}

ask_yes_no() {
    prompt="$1"
    default="${2:-y}"

    if [ "$default" = "y" ]; then
        suffix="[Y/n]"
    else
        suffix="[y/N]"
    fi

    if [ -r /dev/tty ]; then
        printf '%s %s ' "$prompt" "$suffix" > /dev/tty
        read answer < /dev/tty
    elif [ -t 0 ]; then
        printf '%s %s ' "$prompt" "$suffix"
        read answer
    else
        return 1
    fi

    answer=$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')
    case "$answer" in
        y|yes) return 0 ;;
        n|no) return 1 ;;
        "") [ "$default" = "y" ] ;;
        *) return 1 ;;
    esac
}

run_install_command() {
    command="$1"
    fallback="$2"
    if ask_yes_no "Run '$command' now?" "y"; then
        sh -c "$command"
    else
        say "$fallback"
        exit 1
    fi
}

install_python_hint() {
    if command -v apt-get >/dev/null 2>&1; then
        run_install_command "sudo apt-get update && sudo apt-get install -y python3" "Install Python 3, then rerun this installer."
    elif command -v dnf >/dev/null 2>&1; then
        run_install_command "sudo dnf install -y python3" "Install Python 3, then rerun this installer."
    elif command -v pacman >/dev/null 2>&1; then
        run_install_command "sudo pacman -S --needed python" "Install Python 3, then rerun this installer."
    elif command -v brew >/dev/null 2>&1; then
        run_install_command "brew install python" "Install Python 3, then rerun this installer."
    else
        say "Python 3 is required, but no supported package manager was found."
        say "Install Python 3 manually, then rerun this installer."
        exit 1
    fi
}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    say "Python 3 is required."
    install_python_hint
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    say "Python 3.8 or newer is required."
    install_python_hint
fi

if ! command -v git >/dev/null 2>&1; then
    say "git is required to install from the repository."
    if command -v apt-get >/dev/null 2>&1; then
        run_install_command "sudo apt-get update && sudo apt-get install -y git" "Install git, then rerun this installer."
    elif command -v dnf >/dev/null 2>&1; then
        run_install_command "sudo dnf install -y git" "Install git, then rerun this installer."
    elif command -v pacman >/dev/null 2>&1; then
        run_install_command "sudo pacman -S --needed git" "Install git, then rerun this installer."
    elif command -v brew >/dev/null 2>&1; then
        run_install_command "brew install git" "Install git, then rerun this installer."
    else
        say "Install git manually, then rerun this installer."
        exit 1
    fi
fi

mkdir -p "$BIN_DIR" "$(dirname "$INSTALL_ROOT")"

if [ -d "$INSTALL_ROOT/.git" ]; then
    say "Updating $INSTALL_ROOT"
    git -C "$INSTALL_ROOT" pull --ff-only
else
    rm -rf "$INSTALL_ROOT"
    say "Installing into $INSTALL_ROOT"
    git clone "$REPO_URL" "$INSTALL_ROOT"
fi

cat > "$BIN_DIR/3dsutil" <<EOF
#!/bin/sh
exec "$PYTHON" "$INSTALL_ROOT/3dsutil.py" "\$@"
EOF
chmod +x "$BIN_DIR/3dsutil"

say "Installed 3dsutil to $BIN_DIR/3dsutil"
if ! command -v 3dsutil >/dev/null 2>&1; then
    say "Add $BIN_DIR to PATH if your shell cannot find '3dsutil'."
fi
say "Try: 3dsutil --help"
