@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1" %*
set "LifecycleExit=%errorlevel%"
pause
exit /b %LifecycleExit%
