#Requires -Version 5.1
<#
  k250-forge one-click start (portable ZIP edition).

  Run by the bundled Start-K250.cmd (which calls this with
  -ExecutionPolicy Bypass, since many Windows boxes refuse scripts by default).

  What it does:
    * refuses to run from a folder you don't own (same preflight as install.ps1)
    * builds the venv + bleak on first run by delegating to the repo's install.ps1
    * leaves your limits file alone (install.ps1's rule: limits.local.json wins)
    * starts the launcher bound to 127.0.0.1:6969 and opens your browser on it
    * Ctrl+C (or closing the window) stops the launcher

  Nothing here writes outside this folder, edits PATH or touches the registry.
#>
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Here
Write-Host "k250-forge launcher -> $Here"
Write-Host ""

# 0. preflight -- must be a folder you own, exactly like install.ps1.
$probe = Join-Path $Here '.k250-write-probe'
try {
    [System.IO.File]::WriteAllText($probe, 'ok')
} catch {
    Write-Host "ERROR: $Here is not writable by $env:USERNAME."
    Write-Host "  The launcher builds a venv next to the code, so it has to be a folder you own."
    Read-Host "Press Enter to close"
    exit 1
} finally {
    if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
}

# 1. one-time setup: venv + bleak. Delegate to the repo's own installer so the
#    wrapper file stays thin and the code path is the tested one.
$VenvPy = Join-Path $Here 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPy)) {
    $Install = Join-Path $Here 'install.ps1'
    if (-not (Test-Path -LiteralPath $Install)) {
        Write-Host "ERROR: install.ps1 is missing next to this script - the ZIP is incomplete."
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "[setup] first run - installing (venv + bleak). This may take a minute..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: setup did not finish cleanly. See the messages above."
        Read-Host "Press Enter to close"
        exit 1
    }
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Write-Host "ERROR: install completed but the venv did not appear."
        Read-Host "Press Enter to close"
        exit 1
    }
}

# 2. launch the engine web app on the loopback port.
$Port = if ($args.Count -ge 1 -and $args[0] -match '^\d+$') { [int]$args[0] } else { 6969 }
Write-Host "[run] starting k250 launcher on http://127.0.0.1:$Port ..."
Write-Host "      The browser opens here in a moment. Close this window (or press Ctrl+C) to stop it."
Write-Host ""
# We open the browser just before blocking on the server below. The HTML server binds
# in well under a second, far faster than the browser boots, so it is ready when the
# window lands. If it is not, the launcher window above still shows the URL to paste.
Start-Process ("http://127.0.0.1:" + $Port)
& $VenvPy (Join-Path $Here 'k250_launcher.py') --port $Port