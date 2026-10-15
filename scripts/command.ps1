param([string]$Command, [string]$Argument)
. "$PSScriptRoot\common.ps1"
$BotArguments = @($Command)
if ($Argument) { $BotArguments += $Argument }
Invoke-Bot -BotArgs $BotArguments
