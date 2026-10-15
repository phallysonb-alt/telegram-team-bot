. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw '请先运行 install.cmd 安装依赖。' }
$DataPath = (Invoke-WindowlessPython -PythonArgs @('-m','team_bot','paths') | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw '配置无效，请运行 configure.cmd 核对。' }
New-Item -ItemType Directory -Path $DataPath -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $DataPath 'autostart.stop') -ErrorAction SilentlyContinue
$PidPath = Join-Path $DataPath 'process.pid'
if (Test-Path -LiteralPath $PidPath) {
    $ExistingPid = [int](Get-Content -LiteralPath $PidPath)
    $ExistingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingPid"
    if (Test-BotProcess $ExistingProcess) { throw '机器人已经在运行。' }
}
$LogPath = Join-Path $DataPath 'logs'
New-Item -ItemType Directory -Path $LogPath -Force | Out-Null
$LaunchResult = Invoke-WindowlessPython -PythonArgs @("$PSScriptRoot\windows_background.py",'start','--data-path',$DataPath)
if ($LASTEXITCODE -ne 0) { throw '启动失败。请查看数据目录 logs/console-error.log，并运行 diagnose.cmd。' }
Write-Host "机器人已启动，进程编号 $LaunchResult。运行 stop.cmd 停止。"
