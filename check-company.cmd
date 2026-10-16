@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo 请先运行 setup-company.cmd 完成独立安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\company_setup.py diagnose --online %*
set RESULT=%errorlevel%
pause
exit /b %RESULT%
