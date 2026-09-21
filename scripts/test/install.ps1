# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Test env: install dependencies
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$SkipDocker,
    [switch]$SkipNpm
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Test Environment - Install" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Set-Location $ProjectRoot

Write-Step "Python venv"
$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv venv
}
& $venvPython --version
if ($LASTEXITCODE -ne 0) { throw "Python venv is unavailable" }
Write-Ok "venv ready"

Write-Step "pip install"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
$runtimeDir = Join-Path $ProjectRoot '.runtime\test'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$appRequirements = Join-Path $runtimeDir 'app-requirements.lock'
& $venvPython -m src.platform.kernel.build_constraints --output $appRequirements
if ($LASTEXITCODE -ne 0) { throw "App dependency locks conflict or are invalid" }
& $venvPython -m pip install -r requirements.txt -r web\django_backend\requirements.txt -r $appRequirements
if ($LASTEXITCODE -ne 0) { throw "Python/App dependency installation failed" }
& $venvPython -m pip install pytest httpx
if ($LASTEXITCODE -ne 0) { throw "Test dependency installation failed" }
& $venvPython -m src.platform.kernel.cli check-dependencies
if ($LASTEXITCODE -ne 0) { throw "Installed App dependencies do not match their locks" }
Write-Ok "Python deps installed"

Write-Step ".env"
$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) {
    @"
DEBUG=true
ENFORCE_BFF_ORIGIN=false
DEEPSEEK_API_KEY=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=netops_agent
POSTGRES_USER=netops
POSTGRES_PASSWORD=netops123456
REDIS_HOST=localhost
MINIO_ENDPOINT=localhost:9000
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
"@ | Set-Content -Path $envFile -Encoding UTF8
    Write-Warn "Created .env - set DEEPSEEK_API_KEY"
} else {
    Write-Ok ".env exists"
}

if (-not $SkipNpm) {
    Write-Step "npm ci"
    Push-Location (Join-Path $ProjectRoot "web\react_frontend")
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        Write-Ok "npm done"
    } else {
        Write-Warn "npm not found"
    }
    Pop-Location
}

if (-not $SkipDocker) {
    Write-Step "Docker pull middleware images"
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $composeFile = Join-Path $ProjectRoot "deployment\docker-compose.middleware.yml"
        docker compose -f $composeFile pull
        Write-Ok "Images pulled"
    } else {
        Write-Warn "Docker not found"
    }
}

Write-Host "`n[Done] Run scripts\test\start.ps1 next" -ForegroundColor Green
