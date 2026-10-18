param([ValidateRange(0,600)][int]$TimeoutSeconds = 60, [switch]$Force)
. "$PSScriptRoot\common.ps1"
$DataPath = (Invoke-WindowlessPython -PythonArgs @('-m','team_bot','paths') | Select-Object -Last 1)
$StopArgs = @("$PSScriptRoot\windows_background.py", 'stop', '--data-path', $DataPath, '--timeout', [string]$TimeoutSeconds)
if ($Force) { $StopArgs += '--force' }
Invoke-WindowlessPython -PythonArgs $StopArgs -ExplainFailure
