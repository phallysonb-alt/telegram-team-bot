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
if "%RESULT%"=="2" echo 仍有失败或待验证步骤；配置完成不等于服务可启动，请查看上方具体项目。
pause
exit /b %RESULT%
