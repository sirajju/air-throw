#!/usr/bin/env bash
# Build the Linux executable -> dist/airthrow
# Run this ON a Linux machine. (From Windows/macOS, use Docker instead:
#   docker build -f Dockerfile.linux -t airthrow-linux .
#   docker run --rm -v "$PWD/dist:/out" airthrow-linux )
set -euo pipefail
python3 -m pip install -r requirements.txt pyinstaller
pyinstaller --clean -y airthrow.spec
echo
echo "Done. Executable: dist/airthrow"
echo "Try:  ./dist/airthrow info"
