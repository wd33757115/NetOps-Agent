# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Production startup: Compose, migrations, seed users, and smoke tests.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$Dev,
    [switch]$UseSupervisorV1,
    [switch]$SkipPreflight,
    [switch]$SkipSeed,
    [switch]$SkipSmoke,
    [switch]$SkipBuild,
    [int]$WaitSeconds = 60
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Production - Start" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Set-Location $ProjectRoot

$composeBase = Join-Path $ProjectRoot "deployment\docker-compose.yml"
$composeProd = Join-Path $ProjectRoot "deployment\docker-compose.prod.yml"
$composeArgs = @("-f", $composeBase)
if (-not $Dev -and (Test-Path $composeProd)) {
    $composeArgs += @("-f", $composeProd)
    Write-Host "  Mode: production (127.0.0.1 bind)" -ForegroundColor Gray
} else {
    Write-Host "  Mode: dev (all interfaces)" -ForegroundColor Gray
}

$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) {
    $example = Join-Path $ProjectRoot ".env.example"
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Warn "Copied .env.example to .env; review secrets before deployment."
    } else {
        throw ".env and .env.example are both missing"
    }
}

if (-not $SkipPreflight) {
    Write-Step "Environment preflight"
    & (Join-Path $PSScriptRoot "check_env.ps1") -EnvFile $envFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($UseSupervisorV1) {
    $env:USE_SUPERVISOR_V2 = "false"
} else {
    $env:USE_SUPERVISOR_V2 = "true"
}

Write-Step ("Docker compose up USE_SUPERVISOR_V2=" + $env:USE_SUPERVISOR_V2)
$upArgs = @("compose") + $composeArgs + @("--profile", "core", "up", "-d")
if (-not $SkipBuild) { $upArgs += "--build" }
docker @upArgs
Write-Ok "Containers started"

Write-Step ("Wait $WaitSeconds seconds for healthchecks")
Start-Sleep -Seconds $WaitSeconds

Write-Step "Django migrate"
docker compose @composeArgs exec -T django python manage.py migrate --noinput 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Migration failed; retry after containers become healthy."
} else {
    Write-Ok "migrate done"
}

if (-not $SkipSeed) {
    Write-Step "Seed auth users (admin/viewer demo accounts)"
    docker compose @composeArgs exec -T django python manage.py seed_auth_users 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "seed_auth_users done"
    } else {
        Write-Warn "seed_auth_users skipped or failed; users may already exist."
    }
}

Write-Step "Container status"
docker compose @composeArgs --profile core ps

if (-not $SkipSmoke) {
    Write-Step "Smoke test"
    & (Join-Path $PSScriptRoot "smoke_test.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Smoke tests failed; inspect celery-patrol, celery-control, and celery-beat logs."
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "  React:  http://127.0.0.1:3000" -ForegroundColor Gray
Write-Host "  Django: http://127.0.0.1:8001" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
