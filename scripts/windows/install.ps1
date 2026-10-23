# Windows dependency installation. Does not restart running application processes.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonCommand = 'python',
    [string]$IndexUrl = 'https://pypi.org/simple',
    [switch]$SkipDocker,
    [switch]$SkipNpm,
    [switch]$WithLangfuse
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = '1'
$venvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not (Get-Command $PythonCommand -ErrorAction SilentlyContinue)) {
        throw 'Python not installed. Run scripts\windows\prerequisites.ps1 first, then reopen PowerShell.'
    }
    & $PythonCommand -c 'import sys; sys.exit(not (sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32))'
    if ($LASTEXITCODE -ne 0) { throw 'Use Python 3.13 x64; pass its full executable path with -PythonCommand.' }
    & $PythonCommand -m venv (Join-Path $ProjectRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create venv' }
}
& $venvPython --version
if ($LASTEXITCODE -ne 0) { throw 'Existing venv is unavailable' }
if (-not $SkipNpm) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm missing; run prerequisites.ps1 and reopen PowerShell.' }
    node -e 'if (parseInt(process.versions.node) < 22) process.exit(1)'
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 22 or later required' }
}
& $venvPython (Join-Path $PSScriptRoot 'prepare_env.py') --root $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw 'Environment initialization failed' }
$runtimeDir = Join-Path $ProjectRoot '.runtime\test'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$appRequirements = Join-Path $runtimeDir 'app-requirements.lock'
& $venvPython -m src.platform.kernel.build_constraints --output $appRequirements
if ($LASTEXITCODE -ne 0) { throw 'Conflicting or invalid App locks' }
$pipArgs = @('-m', 'pip', 'install', '--index-url', $IndexUrl, '-r', 'requirements.txt', '-r', $appRequirements)
if ($WithLangfuse) { $pipArgs += @('-r', 'deployment/requirements/observability.txt') }
& $venvPython @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflicts remain in venv. See docs/deployment-windows.md (rebuilding a clean venv).' }
& $venvPython -m src.platform.kernel.cli check-dependencies
if ($LASTEXITCODE -ne 0) { throw 'App dependency validation failed' }
if (-not $SkipNpm) {
    Push-Location (Join-Path $ProjectRoot 'web\react_frontend')
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    } finally { Pop-Location }
}
if (-not $SkipDocker) {
    & (Join-Path $PSScriptRoot 'containers.ps1') -ProjectRoot $ProjectRoot -Action pull -WithLangfuse:$WithLangfuse
    & (Join-Path $PSScriptRoot 'containers.ps1') -ProjectRoot $ProjectRoot -Action up -WithLangfuse:$WithLangfuse
}
Write-Host '[OK] Dependencies installed. Run scripts\windows\bootstrap.ps1 -Action apply, then scripts\windows\start.ps1 -SkipLangfuse.' -ForegroundColor Green
