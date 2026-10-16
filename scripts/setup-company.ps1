param([string]$Target, [switch]$InstallMissing, [switch]$Offline)
$ErrorActionPreference = 'Stop'
$SourceRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path
}
function Find-Python {
    foreach ($candidate in @('python','py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $result = & $cmd.Source -c "import sys; print(sys.executable if sys.version_info >= (3,12) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) { return [string]$result }
        }
    }
    foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}
function Find-Node {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) {
        & $cmd.Source -e "process.exit(Number(process.versions.node.split('.')[0])>=22?0:1)"
        if ($LASTEXITCODE -eq 0) { return $cmd }
    }
    return $null
}
function Install-Runtime([string]$Id, [string]$Label, [string]$Url) {
    if (-not $InstallMissing) {
        Write-Host "缺少 $Label。官方安装地址：$Url"
        if ((Read-Host '是否使用 winget 官方软件包安装？输入 y 同意，其余退出') -ne 'y') { throw "安装 $Label 后重新运行即可继续。" }
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "没有 winget；请从 $Url 安装 $Label，重新打开安装入口。" }
    & winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "$Label 安装未成功，请使用上述官方网站安装后继续。" }
    Refresh-Path
}
try {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw '仅支持 Windows。' }
    Write-Host '新公司独立安装；不会读取原公司的 .env、数据库或启动其机器人。'
    $Python = Find-Python
    if (-not $Python) {
        Install-Runtime 'Python.Python.3.12' 'Python 3.12+' 'https://www.python.org/downloads/windows/'
        $Python = Find-Python
    }
    if (-not $Python) { throw '仍未找到 Python 3.12+；请重新打开终端后继续。' }
    $Node = Find-Node
    if (-not $Node) {
        Install-Runtime 'OpenJS.NodeJS.LTS' 'Node.js 22+' 'https://nodejs.org/en/download'
        $Node = Find-Node
    }
    if (-not $Node) { throw '仍未找到 Node.js；重新打开安装入口后继续。' }
    & $Node.Source -e "process.exit(Number(process.versions.node.split('.')[0])>=22?0:1)"
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 需要 22 或更新版本。' }
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Npm) { throw '缺少 npm.cmd；请修复 Node.js 安装。' }
    if (-not $Target -and (Test-Path -LiteralPath (Join-Path $SourceRoot '.company-install.json'))) { $Target = $SourceRoot }
    if (-not $Target) {
        $DefaultTarget = Join-Path $env:LOCALAPPDATA 'TelegramTeamBot\company'
        $Target = Read-Host "独立运行目录（留空使用 $DefaultTarget）"
        if (-not $Target) { $Target = $DefaultTarget }
    }
    $Target = [IO.Path]::GetFullPath($Target)
    if ($Target -ne $SourceRoot) {
        & $Python "$PSScriptRoot\company_setup.py" prepare --target $Target
        if ($LASTEXITCODE -ne 0) { throw '独立目录准备未完成。' }
    } elseif (-not (Test-Path -LiteralPath (Join-Path $Target '.company-install.json'))) { throw '不能在下载源码目录中直接运行公司业务。' }
    $MutexName = 'Local\TelegramCompanySetup-' + ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Target.ToLowerInvariant())))).Replace('-','').Substring(0,20)
    $InstallMutex = New-Object Threading.Mutex($false, $MutexName)
    try { $Acquired = $InstallMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $Acquired = $true }
    if (-not $Acquired) { throw '此运行目录已有安装程序，请等待其结束。' }
    $VenvPython = Join-Path $Target '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python -m venv (Join-Path $Target '.venv')
        if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。' }
    }
    & $VenvPython -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
    if ($LASTEXITCODE -ne 0) { throw '目标虚拟环境Python版本不足3.12；请保留数据并修复本公司.venv。' }
    # Always use the target's interpreter and lockfile, never the source runtime.
    & $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $Target 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python依赖安装失败；检查网络/代理，重新运行本入口继续。' }
    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Python依赖冲突；请检查虚拟环境。' }
    Push-Location (Join-Path $Target 'deploy')
    try {
        & $Npm.Source ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Node依赖安装失败；检查网络后重新运行，不会清空数据。' }
    } finally { Pop-Location }
    $Arguments = @("$Target\scripts\company_setup.py", 'wizard')
    if ($Offline) { $Arguments += '--offline' }
    & $VenvPython @Arguments
    exit $LASTEXITCODE
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($Acquired) { $InstallMutex.ReleaseMutex() }
    if ($InstallMutex) { $InstallMutex.Dispose() }
}
