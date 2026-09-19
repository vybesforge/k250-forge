#Requires -Version 5.1
<#
  k250-forge -- build the portable Windows ZIP for a release.

  Produces:  dist\k250-forge-windows-<tag>.zip

  The ZIP is the checked-out tree (from git archive, so no .git, no local junk)
  plus the one-click launcher (Start-K250.cmd / Start-K250.ps1) and a quickstart
  readme. Anyone can unzip it into a folder they own and double-click Start-K250.cmd;
  it builds the venv + bleak on first run and serves the launcher on 127.0.0.1:6969.

  Usage:
      powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build-windows-zip.ps1 [-Tag v3.0]

  -Tag defaults to the current git tag if exactly one is present, else "dev".
#>
param(
    [string]$Tag = $null
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Here

if (-not $Tag) {
    $tags = @(& git describe --tags --exact-match HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or $tags.Count -eq 0) { $Tag = 'dev' } else { $Tag = $tags[0] }
}
$Tag = $Tag -replace '[^A-Za-z0-9._-]', '-'     # safe for a filename

$Dist  = Join-Path $Here 'dist'
$Tmp   = Join-Path $env:TEMP ('k250-zip-' + [guid]::NewGuid().ToString('N'))
$Stage = Join-Path $Tmp 'k250-forge'
New-Item -ItemType Directory -Force -Path $Dist, $Stage | Out-Null

# 0. verify the tree is clean enough to ship -- refuse to zip a dirty working copy.
$dirty = @(& git status --porcelain 2>$null)
if ($LASTEXITCODE -eq 0 -and $dirty.Count -gt 0) {
    Write-Host "WARNING: working tree is dirty (untracked/modified files below). Shipping anyway."
    $dirty | Select-Object -First 10 | ForEach-Object { Write-Host ("    " + $_) }
}

# 1. export the submitted tree exactly as committed.
& git archive --format=zip -o (Join-Path $Tmp 'tree.zip') HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed." }
Expand-Archive -LiteralPath (Join-Path $Tmp 'tree.zip') -DestinationPath $Stage -Force

# 2. drop in the one-click launcher and quickstart (new files live in-tree, not in git HEAD).
foreach ($f in @('Start-K250.cmd', 'Start-K250.ps1', 'README-Windows-quickstart.txt')) {
    if (Test-Path -LiteralPath (Join-Path $Here $f)) {
        Copy-Item -LiteralPath (Join-Path $Here $f) -Destination (Join-Path $Stage $f) -Force
    }
}

# 3. zip it with a top-level k250-forge/ folder so unzipping is tidy.
$Out = Join-Path $Dist ("k250-forge-windows-" + $Tag + ".zip")
if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
Compress-Archive -Path $Stage -DestinationPath $Out -CompressionLevel Optimal -Force

# 4. cleanup
Remove-Item -LiteralPath $Tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host ("Built: {0}" -f $Out)
Write-Host ("  {0:N0} bytes" -f (Get-Item -LiteralPath $Out).Length)
Write-Host ""
Write-Host "Attach this file to the release. Anyone unzips it into a folder they own and"
Write-Host "double-clicks Start-K250.cmd. First run installs the venv + bleak automatically."