#!/usr/bin/env bash
# Build + package the Linux binaries into a shareable archive.
# Run ON a Linux machine:
#   ./package-linux.sh                 # build then package
#   ./package-linux.sh --skip-build    # package an existing dist/
#
# Everything installs into a local .venv/ (never system Python), so it won't trip
# the "externally-managed-environment / will break system packages" error (PEP 668)
# on Debian/Ubuntu/Fedora. First time you may need:
#   Debian/Ubuntu:  sudo apt install python3-venv binutils libgl1 libglib2.0-0
#   Fedora:         sudo dnf install python3 binutils mesa-libGL glib2
#
# No Linux box? Build with Docker instead (local, no cloud):
#   docker build -f Dockerfile.linux -t airthrow-linux .
#   docker run --rm -v "$PWD/dist:/out" airthrow-linux
#
# Output: release/airthrow-v<version>-linux-x64.tar.gz  (the three binaries).
set -euo pipefail

if [ "${1:-}" != "--skip-build" ]; then
    if [ ! -d .venv ]; then
        echo "Creating virtualenv (.venv)..."
        python3 -m venv .venv || {
            echo "Couldn't create a virtualenv. Install it, then retry:"
            echo "  Debian/Ubuntu:  sudo apt install python3-venv"
            exit 1
        }
    fi
    VPY=.venv/bin/python
    echo "Installing build dependencies into .venv (not system Python)..."
    "$VPY" -m pip install --upgrade pip
    "$VPY" -m pip install -r requirements.txt pyinstaller
    "$VPY" -m PyInstaller --clean -y airthrow.spec
fi

# Read the version with the venv python if present, else system python.
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
