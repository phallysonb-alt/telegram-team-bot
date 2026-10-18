@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Resume
set "LifecycleExit=%errorlevel%"
pause
exit /b %LifecycleExit%
