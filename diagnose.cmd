@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\command.ps1" diagnose
pause
