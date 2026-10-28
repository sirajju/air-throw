# Build the Windows executable -> dist\airthrow.exe
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Write-Host "Installing build dependencies..."
python -m pip install -r requirements.txt pyinstaller | Out-Null
Write-Host "Building airthrow.exe (this takes a few minutes)..."
python -m PyInstaller --clean -y airthrow.spec
Write-Host ""
Write-Host "Done. Executable: dist\airthrow.exe"
Write-Host "Try:  .\dist\airthrow.exe info"
