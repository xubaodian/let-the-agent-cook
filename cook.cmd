@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cook.ps1" %*
exit /b %errorlevel%
