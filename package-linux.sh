#!/usr/bin/env bash
# Build + package the Linux binaries into a shareable archive.
# Run ON a Linux machine:
#   ./package-linux.sh                 # build then package
#   ./package-linux.sh --skip-build    # package an existing dist/
#
# No Linux box? Build with Docker instead (local, no cloud):
#   docker build -f Dockerfile.linux -t airthrow-linux .
#   docker run --rm -v "$PWD/dist:/out" airthrow-linux
#
# Output: release/airthrow-v<version>-linux-x64.tar.gz  (the three binaries).
set -euo pipefail

ver=$(python3 -c "import airthrow,sys; sys.stdout.write(airthrow.__version__)")

if [ "${1:-}" != "--skip-build" ]; then
    python3 -m pip install -r requirements.txt pyinstaller
    pyinstaller --clean -y airthrow.spec
fi

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
