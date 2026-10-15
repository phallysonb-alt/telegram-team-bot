. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw '请先运行 install.cmd 安装依赖。' }
$DataPath = (& $PythonExe -m team_bot paths | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw '配置无效，请运行 configure.cmd 核对。' }
New-Item -ItemType Directory -Path $DataPath -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $DataPath 'autostart.stop') -ErrorAction SilentlyContinue
$PidPath = Join-Path $DataPath 'process.pid'
if (Test-Path -LiteralPath $PidPath) {
    $ExistingPid = [int](Get-Content -LiteralPath $PidPath)
    $ExistingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingPid"
    if ($ExistingProcess -and $ExistingProcess.ExecutablePath -eq $PythonExe -and $ExistingProcess.CommandLine -like '*team_bot run*') { throw '机器人已经在运行。' }
}
$LogPath = Join-Path $DataPath 'logs'
New-Item -ItemType Directory -Path $LogPath -Force | Out-Null
$BotProcess = Start-Process -FilePath $PythonExe -ArgumentList @('-m','team_bot','run') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogPath 'console.log') -RedirectStandardError (Join-Path $LogPath 'console-error.log')
$BotProcess.Id | Set-Content -LiteralPath $PidPath
Start-Sleep -Seconds 3
if ($BotProcess.HasExited) { throw '启动失败。请查看数据目录 logs/console-error.log，并运行 diagnose.cmd。' }
Write-Host "机器人已启动，进程编号 $($BotProcess.Id)。运行 stop.cmd 停止。"
