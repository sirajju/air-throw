#!/usr/bin/env bash
# One-command AirThrow installer for Linux/macOS. Clones the repo, builds an
# isolated venv, and drops an `airthrow` launcher in ~/.local/bin.
#
#   curl -fsSL https://raw.githubusercontent.com/sirajju/air-throw/main/install.sh | bash
#
# Override the install location with AIRTHROW_DIR=/path curl ... | bash
set -euo pipefail

REPO="https://github.com/sirajju/air-throw"
DIR="${AIRTHROW_DIR:-$HOME/AirThrow}"
say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

# --- prerequisites: install git + python (+ venv, OpenCV libs, xdotool) -------
# Auto-installs via whatever package manager is present so nobody has to set up
# git/python by hand. Best-effort: a missing package just prints "(skipped …)".
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
install_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        say "Installing system packages (apt)..."
        local glib="libglib2.0-0"
        apt-cache show libglib2.0-0t64 >/dev/null 2>&1 && glib="libglib2.0-0t64"
        $SUDO apt-get update -qq || true
        for p in git python3 python3-venv libgl1 "$glib" xdotool; do
            $SUDO apt-get install -y "$p" || echo "  (skipped $p)"
        done
    elif command -v dnf >/dev/null 2>&1; then
        say "Installing system packages (dnf)..."
        for p in git python3 python3-pip mesa-libGL glib2 xdotool; do
            $SUDO dnf install -y "$p" || echo "  (skipped $p)"
        done
    elif command -v pacman >/dev/null 2>&1; then
        say "Installing system packages (pacman)..."
        $SUDO pacman -Sy --needed --noconfirm git python python-pip mesa glib2 xdotool || true
    elif command -v zypper >/dev/null 2>&1; then
        say "Installing system packages (zypper)..."
        for p in git python3 python3-pip Mesa-libGL1 libglib-2_0-0 xdotool; do
            $SUDO zypper --non-interactive install "$p" || echo "  (skipped $p)"
        done
    elif command -v apk >/dev/null 2>&1; then
        say "Installing system packages (apk)..."
        for p in git python3 py3-pip mesa-gl glib xdotool; do
            $SUDO apk add "$p" || echo "  (skipped $p)"
        done
    elif command -v brew >/dev/null 2>&1; then
        say "Installing packages (Homebrew)..."
        for p in git python; do
            brew list "$p" >/dev/null 2>&1 || brew install "$p" || echo "  (skipped $p)"
        done
    else
        echo "No supported package manager (apt/dnf/pacman/zypper/apk/brew) found."
        if [ "$(uname)" = "Darwin" ]; then
            echo "Install Homebrew (https://brew.sh) — or run 'xcode-select --install'"
            echo "for git — then re-run this installer."
        else
            echo "Please install 'git' and 'python3' (with the venv module), then re-run."
        fi
    fi
}
install_deps

command -v git >/dev/null 2>&1 || {
    echo "git is still missing. Install it and re-run this installer."; exit 1; }
command -v python3 >/dev/null 2>&1 || {
    echo "python3 is still missing. Install it and re-run this installer."; exit 1; }

# --- clone or update ---------------------------------------------------------
if [ -d "$DIR/.git" ]; then
    say "Updating $DIR"
    git -C "$DIR" pull --ff-only || true
else
    say "Cloning into $DIR"
    git clone --depth 1 "$REPO" "$DIR"
fi

# --- venv + deps -------------------------------------------------------------
cd "$DIR"
say "Setting up virtualenv (.venv) and installing dependencies..."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# --- launcher ----------------------------------------------------------------
mkdir -p "$HOME/.local/bin"
LAUNCHER="$HOME/.local/bin/airthrow"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# PYTHONPATH points at the repo so 'python -m airthrow' finds the package
# from any working directory (it's run from source, not pip-installed).
exec env PYTHONPATH="$DIR" "$DIR/.venv/bin/python" -m airthrow "\$@"
EOF
chmod +x "$LAUNCHER"

say "Installed to $DIR"
say "Launcher: $LAUNCHER"

# --- background service (auto-start on login) --------------------------------
# On by default; set AIRTHROW_NO_SERVICE=1 to skip.
if [ -z "${AIRTHROW_NO_SERVICE:-}" ]; then
    say "Enabling background service (auto-start on login)..."
    "$LAUNCHER" service install || \
        echo "  (couldn't enable the service — run 'airthrow service install' later)"
fi

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "   Add this to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo
echo "Next:  airthrow info   |   airthrow gui   |   airthrow run"
echo "Background service:  airthrow service status | uninstall"
echo "Update later: re-run this installer, or  git -C \"$DIR\" pull"
