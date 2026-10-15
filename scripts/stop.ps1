. "$PSScriptRoot\common.ps1"
$DataPath = (& $PythonExe -m team_bot paths | Select-Object -Last 1)
New-Item -ItemType Directory -Path $DataPath -Force | Out-Null
'manual stop' | Set-Content -LiteralPath (Join-Path $DataPath 'autostart.stop')
$PidPath = Join-Path $DataPath 'process.pid'
if (-not (Test-Path -LiteralPath $PidPath)) { Write-Host '未发现运行进程文件。'; exit 0 }
$BotPid = [int](Get-Content -LiteralPath $PidPath)
$BotProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$BotPid"
if ($BotProcess) {
    if ($BotProcess.ExecutablePath -ne $PythonExe -or $BotProcess.CommandLine -notlike '*team_bot run*') { throw '此编号属于其他进程，无法停止，请核对。' }
    # Windows venv launcher may have a Python interpreter child. Stop only this verified process tree.
    $AllProcesses = @(Get-CimInstance Win32_Process)
    $Descendants = @()
    $Parents = @($BotPid)
    while ($Parents.Count -gt 0) {
        $Children = @($AllProcesses | Where-Object { $_.ParentProcessId -in $Parents })
        if ($Children | Where-Object { $_.Name -notmatch '^python(w)?\.exe$' -and -not ($_.Name -eq 'conhost.exe' -and $_.ExecutablePath -eq "$env:windir\system32\conhost.exe") }) { throw '机器人存在未知子进程，请人工检查后停止。' }
        $Descendants += $Children
        $Parents = @($Children | ForEach-Object { [int]$_.ProcessId })
    }
    [array]::Reverse($Descendants)
    foreach ($Child in $Descendants) { Stop-Process -Id $Child.ProcessId -ErrorAction SilentlyContinue }
    # The venv launcher may exit as soon as its interpreter child stops.
    Stop-Process -Id $BotPid -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $PidPath -ErrorAction SilentlyContinue
Write-Host '机器人已停止。数据库和待执行任务在重启后恢复。'
