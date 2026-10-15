@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\github_sync.py %*
) else (
  python scripts\github_sync.py %*
)
exit /b %errorlevel%
