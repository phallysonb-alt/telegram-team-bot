. "$PSScriptRoot\common.ps1"
$DataPath = (& $PythonExe -m team_bot paths | Select-Object -Last 1)
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
            $Running = $Process -and $Process.ExecutablePath -eq $PythonExe -and $Process.CommandLine -like '*team_bot run*'
        }
    }
    if (-not $Running) {
        & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$PSScriptRoot\start.ps1" *> $null
    }
    Start-Sleep -Seconds 30
}
