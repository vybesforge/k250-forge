@echo off
rem k250-forge one-click start. Runs the PS1 setup+launcher with a bypass of
rem the execution policy, so it works even where PowerShell is locked down.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-K250.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup or start failed. See the messages above.
  pause
)