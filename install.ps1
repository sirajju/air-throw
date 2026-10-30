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

# --- prerequisites: auto-install git + python if missing ---------------------
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Ensure-Tool($cmd, $wingetId, $nice) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { return $true }
    Refresh-Path
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { return $true }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "$nice is not installed and 'winget' isn't available to install it." -ForegroundColor Yellow
        Write-Host "Install $nice, then re-run this command:" -ForegroundColor Yellow
        if ($cmd -eq "git") { Write-Host "  https://git-scm.com/download/win" }
        else { Write-Host "  https://www.python.org/downloads/  (tick 'Add python.exe to PATH')" }
        return $false
    }
    Say "Installing $nice (winget)..."
    winget install --id $wingetId -e --source winget `
        --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

if (-not (Ensure-Tool "git" "Git.Git" "Git")) {
    throw "Git is required and could not be installed automatically."
}
if (-not ((Ensure-Tool "python" "Python.Python.3.12" "Python") -or
          (Get-Command "py" -ErrorAction SilentlyContinue))) {
    throw "Python is required and could not be installed automatically."
}
# Prefer `python`; fall back to the `py` launcher if that's all we have.
$PyExe = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }

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
& $PyExe -m venv .venv
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
