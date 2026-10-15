$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $ProjectRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PythonWindowless = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$env:PYTHONUTF8 = '1'
function Invoke-WindowlessPython {
    param([string[]]$PythonArgs)
    # Windows PowerShell native-command invocation may allocate a console even
    # when its own parent has none. Explicitly suppress it for startup helpers.
    $QuotedArgs = foreach ($Argument in $PythonArgs) {
        if ($Argument.Contains('"')) { throw '启动参数含无效引号。' }
        '"' + ($Argument -replace '(\\+)$', '$1$1') + '"'
    }
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    # The Windows venv console launcher may create a console for its interpreter
    # child despite CreateNoWindow on the launcher. Use its GUI counterpart too.
    $Info.FileName = $PythonWindowless
    $Info.Arguments = $QuotedArgs -join ' '
    $Info.WorkingDirectory = $ProjectRoot
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $Info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $Child = New-Object System.Diagnostics.Process
    $Child.StartInfo = $Info
    try {
        [void]$Child.Start()
        $OutputTask = $Child.StandardOutput.ReadToEndAsync()
        $ErrorTask = $Child.StandardError.ReadToEndAsync()
        $Child.WaitForExit()
        $OutputText = $OutputTask.Result
        $ErrorText = $ErrorTask.Result
        $global:LASTEXITCODE = $Child.ExitCode
        if ($Child.ExitCode -ne 0) { throw "后台启动辅助程序失败（退出码 $($Child.ExitCode)），请检查配置及数据目录日志。" }
        if ($OutputText) { $OutputText.TrimEnd([char[]]"`r`n") -split '\r?\n' }
    } finally { $Child.Dispose() }
}
function Test-BotProcess {
    param($Candidate)
    return ($Candidate -and $Candidate.ExecutablePath -in @($PythonExe, $PythonWindowless) -and $Candidate.CommandLine -like '*team_bot run*')
}
function Invoke-Bot {
    param([string[]]$BotArgs)
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw '请先运行 install.cmd 安装依赖。' }
    & $PythonExe -m team_bot @BotArgs
    if ($LASTEXITCODE -ne 0) { throw "操作失败（退出码 $LASTEXITCODE）。" }
}
