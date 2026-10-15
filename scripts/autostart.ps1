param([ValidateSet('install','uninstall')][string]$Mode)
. "$PSScriptRoot\common.ps1"
$TaskName = 'TelegramTeamBot-' + ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectRoot))).Replace('-','').Substring(0,8))
if ($Mode -eq 'uninstall') {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host '已卸载登录自启动。'
} else {
    $PythonWindowless = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $PythonWindowless)) { throw '请先运行 install.cmd 安装依赖。' }
    $StartPath = Join-Path $PSScriptRoot 'windows_background.py'
    $Action = New-ScheduledTaskAction -Execute $PythonWindowless -Argument "`"$StartPath`" monitor" -WorkingDirectory $ProjectRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'Telegram团队机器人；登录后启动，掉线自动重连，启动失败30秒后重试；stop.cmd停止并暂停守护。' -Force | Out-Null
    Write-Host '已安装当前用户登录时自动启动。'
}
