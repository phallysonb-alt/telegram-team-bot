@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-company.ps1" %*
set RESULT=%errorlevel%
if "%RESULT%"=="2" echo 配置已保留，仍有待验证步骤。重新运行可继续，不表示已允许启动。
if not "%RESULT%"=="0" if not "%RESULT%"=="2" echo 安装尚未完成；按上方提示处理后重新运行本入口即可继续。
pause
exit /b %RESULT%
