# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Production: build images and prepare deploy
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Production - Install" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Set-Location $ProjectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is required"
}

$composeFile = Join-Path $ProjectRoot "deployment\docker-compose.yml"
$envFile = Join-Path $ProjectRoot ".env"

Write-Step ".env"
$exampleFile = Join-Path $ProjectRoot ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $exampleFile) {
        Copy-Item $exampleFile $envFile
        Write-Warn "已从 .env.example 复制 .env - 请修改密钥后再上生产"
    } else {
        @"
DEBUG=false
ENFORCE_BFF_ORIGIN=true
DEEPSEEK_API_KEY=
POSTGRES_PASSWORD=netops123456
DJANGO_SECRET_KEY=change-me-in-production
"@ | Set-Content -Path $envFile -Encoding UTF8
        Write-Warn "Created minimal .env - update secrets before production"
    }
} else {
    Write-Ok ".env exists"
}

Write-Step "Docker pull base images"
docker compose -f $composeFile pull postgres redis rabbitmq minio

if (-not $SkipBuild) {
    Write-Step "Build django and react images"
    docker compose -f $composeFile build django react
    Write-Ok "Build done"
}

Write-Host "`n[Done] Run scripts\prod\start.ps1" -ForegroundColor Green
