#Requires -Version 5.1
<#
  k250-forge installer for Windows -- builds the venv, installs bleak, writes you a limits file
  to edit, and proves the engine runs before handing over.

      .\install.ps1

  Nothing here needs admin, and nothing here edits your PATH or the registry. Everything it
  writes lives in this folder; the only file it adds is limits.local.json.

  If PowerShell refuses to run it ("running scripts is disabled on this system"), bypass the
  policy for this one run:

      powershell -ExecutionPolicy Bypass -File .\install.ps1

  Why there is no wrapper step here, unlike install.sh on Linux and macOS: bin/ is bash. On
  Windows you call the engine directly, and it reads limits.json itself, so the ceiling and the
  session budget are enforced exactly as they are through the wrapper.
#>
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Here
Write-Host "k250-forge -> $Here"
Write-Host ""

function Stop-WithError([string]$Message) {
    Write-Host "ERROR: $Message"
    exit 1
}

# 0. preflight -- the failures that otherwise look like mysteries.
#
# The folder has to be one you own. A protected directory fails here first, and in a way that
# looks like three unrelated problems: from the system folder, `git clone` cannot create the
# directory ("could not create work tree dir ... Permission denied"), so `cd k250-forge` then
# fails, so the venv commands fail too. One cause, three errors.
$probe = Join-Path $Here '.k250-write-probe'
try {
    [System.IO.File]::WriteAllText($probe, 'ok')
} catch {
    Write-Host "ERROR: $Here is not writable by $env:USERNAME."
    Write-Host "  The installer builds a venv next to the code, so this has to be a folder you own."
    Write-Host "  A protected directory (the Windows system folder, Program Files, a root of C:)"
    Write-Host "  cannot work -- and git clone fails there first, with the permission message."
    Write-Host ""
    Write-Host "  Move somewhere you own and run it again:"
    Write-Host "    cd `$HOME"
    Write-Host "    git clone https://github.com/vybesforge/k250-forge.git"
    Write-Host "    cd k250-forge"
    Write-Host "    .\install.ps1"
    exit 1
} finally {
    if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
}

# Which Python? `py` is the launcher, which arrives with the python.org installer but NOT with
# the Microsoft Store build of Python -- so on a Store install `py` is missing while `python`
# works. Try the launcher first, then the plain names. No fixed version floor: pip resolves the
# newest bleak that runs on whatever interpreter this turns out to be, exactly as install.sh does.
$Py = $null
$PyLead = @()
foreach ($cand in @(, @('py', '-3')) + @(, @('python')) + @(, @('python3'))) {
    $exe = $cand[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $lead = @()
    if ($cand.Count -gt 1) { $lead = $cand[1..($cand.Count - 1)] }
    $shown = $null
    try {
        $shown = (& $exe @lead --version 2>&1 | Out-String).Trim()
    } catch { continue }
    if ($LASTEXITCODE -eq 0) {
        $Py = $exe
        $PyLead = $lead
        Write-Host "python     : $shown   (via $exe)"
        break
    }
}
if (-not $Py) {
    Write-Host "ERROR: no Python 3 found (tried py, python and python3)."
    Write-Host "  Install Python 3 from https://www.python.org/downloads/windows/ and tick"
    Write-Host "  'Add python.exe to PATH' on the first screen of the installer."
    Write-Host "  Then open a NEW PowerShell window and run this script again."
    Write-Host ""
    Write-Host "  If typing 'python' opens the Microsoft Store instead of an interpreter, that is"
    Write-Host "  Windows' app-execution alias rather than Python -- the python.org installer replaces it."
    exit 1
}

# 1. venv + bleak
$Venv   = Join-Path $Here 'venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
if (Test-Path -LiteralPath $VenvPy) {
    Write-Host "[1/3] venv already present"
} else {
    Write-Host "[1/3] creating venv"
    & $Py @PyLead -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPy)) {
        Write-Host "ERROR: could not create the venv."
        Write-Host "  A full Python 3 from python.org does this out of the box; the Microsoft Store"
        Write-Host "  build is missing pieces venv needs. Reinstall from"
        Write-Host "  https://www.python.org/downloads/windows/ with 'Add python.exe to PATH' ticked."
        exit 1
    }
}

& $VenvPy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Stop-WithError "pip could not upgrade itself (no network, or a blocked proxy?)." }

& $VenvPy -m pip install --quiet bleak
if ($LASTEXITCODE -ne 0) { Stop-WithError "pip could not install bleak -- its own message above says why." }

& $VenvPy -c "import bleak" 2>$null
if ($LASTEXITCODE -ne 0) { Stop-WithError "bleak installed but will not import -- the venv is not usable." }

# Ask the package metadata, never an attribute: bleak stopped exposing a version attribute, and
# printing one turns a successful install into a traceback.
$bleakShown = (& $VenvPy -c "import importlib.metadata as m; print('bleak', m.version('bleak'))" 2>$null | Out-String).Trim()
if (-not $bleakShown) { $bleakShown = 'bleak' }
Write-Host "      $bleakShown installed"
Write-Host ""

# 2. nothing to link
#
# install.sh symlinks k250-scene / k250-stop / k250-status onto your PATH. Those wrappers are
# bash, so there is no Windows equivalent -- and none is needed: the engine and the stop tool
# enforce limits.json themselves.
Write-Host "[2/3] no PATH step -- call the engine directly; it enforces limits.json itself"
Write-Host ""

# 3. your own limits file
Write-Host "[3/3] limits"
$Local = Join-Path $Here 'limits.local.json'
if (Test-Path -LiteralPath $Local) {
    Write-Host "      limits.local.json already exists -- left alone"
} else {
    Copy-Item -LiteralPath (Join-Path $Here 'limits.json') -Destination $Local
    Write-Host "      wrote limits.local.json (a copy of the conservative default)"
}
Write-Host ""

# Smoke test: prove the engine runs in this venv, with these limits, before handing over.
& $VenvPy (Join-Path $Here 'k250_play.py') --limits-show > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "NOTE: the engine did not answer its own --limits-show. See what it says:"
    Write-Host "    .\venv\Scripts\python k250_play.py --limits-show"
    Write-Host ""
}

Write-Host "Done."
Write-Host ""
Write-Host "  .\venv\Scripts\python k250_status.py               see the box: battery, live channels, speed"
Write-Host "  .\venv\Scripts\python k250_play.py --list          all patterns"
Write-Host "  .\venv\Scripts\python k250_play.py --limits-show   your active ceilings"
Write-Host "  .\venv\Scripts\python k250_stop.py                 STOP NOW (kills the pattern, zeroes every channel)"
Write-Host ""
Write-Host "Wake the box before scanning: power on, tap the gear, then the remote-control icon."
Write-Host "Turn Bluetooth on. Never run two patterns at once -- the box takes one BLE connection."
Write-Host "Edit limits.local.json -- or regenerate it at limits-form.html -- before your first run."
Write-Host ""
Write-Host "-- giving this to an AI agent --------------------------------------------"
Write-Host "Point it at this folder (or the repo) and say:"
Write-Host ""
Write-Host "  Read README.md. Follow the Install section for Windows, then the First steps"
Write-Host "  section. Read limits.json before driving anything. On this machine the tools run"
Write-Host "  as .\venv\Scripts\python k250_play.py, k250_status.py and k250_stop.py. The stop"
Write-Host "  word is red. Never exceed the ceiling in limits.json -- it is enforced in the code,"
Write-Host "  but do not try to work around it. If I say I feel nothing, stop and check the pads"
Write-Host "  -- do not add power. One BLE connection at a time."
