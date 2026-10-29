#!/usr/bin/env bash
# One-shot Linux build: installs OS deps, builds in an isolated venv, and packages
# the binaries into a shareable archive. Just run:
#
#   ./package-linux.sh
#
# Flags:
#   --no-apt       don't try to install system packages (you've handled them)
#   --skip-build   only package an existing dist/ (no build)
#
# It installs into a local .venv/ (never system Python), so it won't hit the
# "externally-managed-environment / will break system packages" error (PEP 668).
# Output: release/airthrow-v<version>-linux-x64.tar.gz  (all three binaries).
set -euo pipefail

NO_APT=0; SKIP_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --no-apt) NO_APT=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        *) echo "Unknown option: $arg"; exit 2 ;;
    esac
done

# --- 1. system packages (venv tooling, PyInstaller/OpenCV libs, xdotool) ------
install_system_deps() {
    [ "$NO_APT" -eq 1 ] && return 0
    local SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    if command -v apt-get >/dev/null 2>&1; then
        echo "Installing system packages (apt)..."
        $SUDO apt-get update -qq || true
        $SUDO apt-get install -y python3-venv binutils libgl1 libglib2.0-0 xdotool
    elif command -v dnf >/dev/null 2>&1; then
        echo "Installing system packages (dnf)..."
        $SUDO dnf install -y python3 binutils mesa-libGL glib2 xdotool
    elif command -v pacman >/dev/null 2>&1; then
        echo "Installing system packages (pacman)..."
        $SUDO pacman -S --needed --noconfirm python binutils mesa glib2 xdotool
    else
        echo "No known package manager (apt/dnf/pacman). Install manually:"
        echo "  a venv tool, binutils, libGL, glib2, xdotool"
    fi
}

# --- 2. build inside a local virtualenv ---------------------------------------
build() {
    if [ ! -d .venv ]; then
        echo "Creating virtualenv (.venv)..."
        python3 -m venv .venv || {
            echo "Couldn't create a virtualenv. Try: sudo apt install python3-venv"
            exit 1
        }
    fi
    local VPY=.venv/bin/python
    echo "Installing Python deps into .venv (not system Python)..."
    "$VPY" -m pip install --upgrade pip
    "$VPY" -m pip install -r requirements.txt pyinstaller
    "$VPY" -m PyInstaller --clean -y airthrow.spec
}

if [ "$SKIP_BUILD" -eq 0 ]; then
    install_system_deps
    build
fi

# --- 3. package ----------------------------------------------------------------
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
ver=$("$PY" -c "import airthrow,sys; sys.stdout.write(airthrow.__version__)")

for b in airthrow airthrow-gui airthrow-tray; do
    [ -f "dist/$b" ] || { echo "Missing dist/$b - build first (omit --skip-build)."; exit 1; }
    chmod +x "dist/$b"
done

mkdir -p release
out="release/airthrow-v${ver}-linux-x64.tar.gz"
tar -czf "$out" -C dist airthrow airthrow-gui airthrow-tray
echo
echo "Packaged -> $out"
echo "Share it; recipients extract and run ./airthrow-gui (no Python needed)."
