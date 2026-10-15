. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $BasePython = $null
    $Candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($Candidate) { $BasePython = $Candidate.Source }
    if (-not $BasePython) {
        $Candidate = Get-Command py -ErrorAction SilentlyContinue
        if ($Candidate) { $BasePython = $Candidate.Source }
    }
    if (-not $BasePython) {
        $BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $BundledPython) { $BasePython = $BundledPython }
    }
    if (-not $BasePython) { throw '请从 https://www.python.org/downloads/ 安装 Python 3.12 或更高版本，勾选添加到 PATH 后重试。' }
    & $BasePython -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12 或更高版本。' }
    & $BasePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。' }
}
& $PythonExe -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络。' }
Invoke-Bot -BotArgs @('test')
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host '安装完成。请运行 configure.cmd 配置，然后运行 start.cmd 启动。'
