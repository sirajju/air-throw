# Build + package the Windows binaries into a shareable archive.
#   powershell -ExecutionPolicy Bypass -File package.ps1
#   powershell -ExecutionPolicy Bypass -File package.ps1 -SkipBuild   # zip existing dist\
#
# Output: release\airthrow-v<version>-windows-x64.zip  (the three .exe files).
# Build once, keep the zip, reuse it — no need to rebuild every time.
param([switch]$SkipBuild)
$ErrorActionPreference = "Stop"

$ver = (python -c "import airthrow,sys; sys.stdout.write(airthrow.__version__)")

if (-not $SkipBuild) {
    Write-Host "Installing build deps..."
    python -m pip install -r requirements.txt pyinstaller | Out-Null
    Write-Host "Building (a few minutes)..."
    python -m PyInstaller --clean -y airthrow.spec
}

$exes = "dist\airthrow.exe", "dist\airthrow-gui.exe", "dist\airthrow-tray.exe"
foreach ($e in $exes) { if (-not (Test-Path $e)) { throw "Missing $e - build first (omit -SkipBuild)." } }

New-Item -ItemType Directory -Force release | Out-Null
$zip = "release\airthrow-v$ver-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $exes -DestinationPath $zip
Write-Host ""
Write-Host "Packaged -> $zip"
Write-Host "Share that zip; recipients unzip and run airthrow-gui.exe (no Python needed)."
