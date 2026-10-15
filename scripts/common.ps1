$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $ProjectRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
function Invoke-Bot {
    param([string[]]$BotArgs)
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw '请先运行 install.cmd 安装依赖。' }
    & $PythonExe -m team_bot @BotArgs
    if ($LASTEXITCODE -ne 0) { throw "操作失败（退出码 $LASTEXITCODE）。" }
}
