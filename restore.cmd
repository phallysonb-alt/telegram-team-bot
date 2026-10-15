@echo off
if "%~1"=="" (
  echo Usage: restore.cmd "full-path-to-backup.zip"
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\command.ps1" restore "%~1"
pause
