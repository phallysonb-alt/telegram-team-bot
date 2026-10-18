param([switch]$Recovery, [switch]$Resume)
. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw '请先运行 install.cmd 安装依赖。' }
$DataPath = (Invoke-WindowlessPython -PythonArgs @('-m','team_bot','paths') | Select-Object -Last 1)
$LaunchArgs = @("$PSScriptRoot\windows_background.py", 'start', '--data-path', $DataPath)
if ($Recovery) { $LaunchArgs += '--recovery' }
if ($Resume) { $LaunchArgs += '--resume' }
$LaunchResult = Invoke-WindowlessPython -PythonArgs $LaunchArgs -ExplainFailure
Write-Host "机器人已启动，进程编号 $LaunchResult。运行 stop.cmd 可控停止。"
