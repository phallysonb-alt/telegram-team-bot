. "$PSScriptRoot\common.ps1"
$DataPath = (Invoke-WindowlessPython -PythonArgs @('-m','team_bot','paths') | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { exit 1 }
$StopPath = Join-Path $DataPath 'autostart.stop'
$PidPath = Join-Path $DataPath 'process.pid'
Remove-Item -LiteralPath $StopPath -ErrorAction SilentlyContinue
while (-not (Test-Path -LiteralPath $StopPath)) {
    $Running = $false
    if (Test-Path -LiteralPath $PidPath) {
        $SavedBotId = 0
        if ([int]::TryParse((Get-Content -LiteralPath $PidPath | Select-Object -First 1), [ref]$SavedBotId)) {
            $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$SavedBotId" -ErrorAction SilentlyContinue
            $Running = Test-BotProcess $Process
        }
    }
    if (-not $Running) {
        # Run within this console-free watcher, rather than creating another shell.
        # A failed recovery must not terminate the 30-second retry loop.
        try { & "$PSScriptRoot\start.ps1" *> $null } catch { }
    }
    Start-Sleep -Seconds 30
}
