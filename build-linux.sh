#!/usr/bin/env bash
# Build the Linux executables -> dist/airthrow, dist/airthrow-gui, dist/airthrow-tray
# Run this ON a Linux machine. (From Windows/macOS, use Docker instead:
#   docker build -f Dockerfile.linux -t airthrow-linux .
#   docker run --rm -v "$PWD/dist:/out" airthrow-linux )
#
# Everything installs into a local .venv/ so we NEVER touch system packages — this
# avoids the "externally-managed-environment / will break system packages" error
# (PEP 668) you get from pip on Debian/Ubuntu/Fedora.
#
# First time only, you may need the OS packages that venv + PyInstaller rely on:
#   Debian/Ubuntu:  sudo apt install python3-venv binutils libgl1 libglib2.0-0
#   Fedora:         sudo dnf install python3 binutils mesa-libGL glib2
set -euo pipefail

if [ ! -d .venv ]; then
    echo "Creating virtualenv (.venv)..."
    python3 -m venv .venv || {
        echo
        echo "Couldn't create a virtualenv. Install the venv package, then retry:"
        echo "  Debian/Ubuntu:  sudo apt install python3-venv"
        echo "  Fedora:         sudo dnf install python3"
        exit 1
    }
fi

VPY=.venv/bin/python
echo "Installing build dependencies into .venv (not system Python)..."
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt pyinstaller
"$VPY" -m PyInstaller --clean -y airthrow.spec
echo
echo "Done. Executables in dist/:  airthrow  airthrow-gui  airthrow-tray"
echo "Try:  ./dist/airthrow info"
