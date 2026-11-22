@echo off
setlocal DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-cook.ps1" %*
exit /b %errorlevel%
