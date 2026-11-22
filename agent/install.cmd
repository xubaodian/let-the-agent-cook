@echo off
setlocal DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cook.ps1" setup --launch %*
set "status=%errorlevel%"
if "%~1"=="" if not "%status%"=="0" pause
exit /b %status%
