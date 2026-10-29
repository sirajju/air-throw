# One-command AirThrow installer for Windows. Clones the repo, builds an
# isolated venv, and creates an `airthrow` command on your PATH.
#
#   irm https://raw.githubusercontent.com/sirajju/air-throw/main/install.ps1 | iex
#
# Override the install location by setting $env:AIRTHROW_DIR before running.
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/sirajju/air-throw"
$Dir  = if ($env:AIRTHROW_DIR) { $env:AIRTHROW_DIR } else { Join-Path $HOME "AirThrow" }
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# --- prerequisites -----------------------------------------------------------
foreach ($c in "git", "python") {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) {
        throw "$c is required. Install it first (e.g. 'winget install Git.Git' or 'winget install Python.Python.3.12'), then re-run."
    }
}

# --- clone or update ---------------------------------------------------------
if (Test-Path (Join-Path $Dir ".git")) {
    Say "Updating $Dir"
    try { git -C $Dir pull --ff-only } catch { Write-Host "  (pull skipped)" }
} else {
    Say "Cloning into $Dir"
    git clone --depth 1 $Repo $Dir
}

# --- venv + deps -------------------------------------------------------------
Set-Location $Dir
Say "Setting up virtualenv (.venv) and installing dependencies..."
python -m venv .venv
$Py = Join-Path $Dir ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt

# --- launcher on PATH --------------------------------------------------------
$BinDir = Join-Path $env:LOCALAPPDATA "AirThrow\bin"
New-Item -ItemType Directory -Force $BinDir | Out-Null
$Cmd = Join-Path $BinDir "airthrow.cmd"
# PYTHONPATH points at the repo so 'python -m airthrow' finds the package from
# any directory (run from source, not pip-installed).
"@echo off`r`nset `"PYTHONPATH=$Dir`"`r`n`"$Py`" -m airthrow %*" |
    Set-Content -Encoding ascii $Cmd

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    Say "Added $BinDir to your PATH (open a NEW terminal to pick it up)."
}

Say "Installed to $Dir"

# --- background service (auto-start on login) --------------------------------
# On by default; set $env:AIRTHROW_NO_SERVICE = "1" to skip.
if (-not $env:AIRTHROW_NO_SERVICE) {
    Say "Enabling background service (auto-start on login)..."
    try { & $Cmd service install } catch { Write-Host "  (run 'airthrow service install' later)" }
}

Write-Host ""
Write-Host "Next (in a new terminal):  airthrow info  |  airthrow gui  |  airthrow run"
Write-Host "Background service:  airthrow service status | uninstall"
Write-Host "Update later: re-run this installer, or  git -C `"$Dir`" pull"
