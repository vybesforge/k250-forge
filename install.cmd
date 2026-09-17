@echo off
REM k250-forge installer launcher for Windows.
REM
REM Windows' default execution policy refuses to run a .ps1 file at all ("running scripts is
REM disabled on this system"), which is the first thing most people hit here. This runs
REM install.ps1 with the policy bypassed for this one process only: nothing on the machine is
REM changed, no policy is set, nothing persists after the window closes.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
