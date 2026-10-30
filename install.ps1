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

function Winget-Install($wingetId, $nice) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Write-Host "$nice isn't installed and 'winget' isn't available to install it." -ForegroundColor Yellow
        if ($wingetId -like "Git*") { Write-Host "  Get Git:    https://git-scm.com/download/win" }
        else { Write-Host "  Get Python: https://www.python.org/downloads/  (tick 'Add python.exe to PATH')" }
        return $false
    }
    Say "Installing $nice (winget)..."
    # --scope user avoids an admin/UAC prompt; ignore winget's exit code and
    # re-check by probing, since it varies across versions.
    try {
        winget install --id $wingetId -e --source winget --scope user `
            --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
    } catch { }
    Refresh-Path
    return $true
}

# Git ------------------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Winget-Install "Git.Git" "Git" | Out-Null
    Refresh-Path
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required and could not be installed automatically. Install it (https://git-scm.com/download/win) and re-run."
}

# Python: find a REAL interpreter, ignoring the Microsoft Store stub
# (…\WindowsApps\python.exe) which otherwise masquerades as installed Python.
function Get-RealPython {
    foreach ($c in @("python", "python3")) {
        $g = Get-Command $c -ErrorAction SilentlyContinue
        if ($g -and $g.Source -notlike "*WindowsApps*") {
            & $c --version *> $null
            if ($LASTEXITCODE -eq 0) { return $c }
        }
    }
    $g = Get-Command py -ErrorAction SilentlyContinue  # the py launcher
    if ($g) { & py -3 --version *> $null; if ($LASTEXITCODE -eq 0) { return "py" } }
    return $null
}

$PyExe = Get-RealPython
if (-not $PyExe) {
    Winget-Install "Python.Python.3.12" "Python" | Out-Null
    $PyExe = Get-RealPython
}
if (-not $PyExe) {
    throw "Python is required and could not be installed automatically. Install Python 3.12 (https://www.python.org/downloads/, tick 'Add python.exe to PATH') and re-run."
}
function Invoke-Py {  # call the chosen interpreter with args (handles the py launcher)
    if ($PyExe -eq "py") { & py -3 @args } else { & $PyExe @args }
}
Say "Using $(Invoke-Py --version)"

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
Invoke-Py -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Could not create the virtualenv with $(Invoke-Py --version)." }
$Py = Join-Path $Dir ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    $ver = (& $Py --version)
    Write-Host ""
    Write-Host "Dependency install failed on $ver." -ForegroundColor Yellow
    Write-Host "mediapipe/opencv may not publish wheels for this Python version yet." -ForegroundColor Yellow
    Write-Host "Fix: install Python 3.12 and re-run, e.g.:" -ForegroundColor Yellow
    Write-Host "  winget install --id Python.Python.3.12 -e"
    Write-Host "  `$env:PATH = [Environment]::GetEnvironmentVariable('Path','User'); irm https://raw.githubusercontent.com/sirajju/air-throw/main/install.ps1 | iex"
    throw "pip install failed (see note above)."
}

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
