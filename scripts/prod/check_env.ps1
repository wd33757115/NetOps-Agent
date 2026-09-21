# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Validate production environment variables before Docker startup.
param(
    [string]$EnvFile = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path ".env")
)

$ErrorActionPreference = "Continue"
$failed = $false
$warned = $false

function Fail($Message) {
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
    $script:failed = $true
}

function Warn($Message) {
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
    $script:warned = $true
}

function Ok($Message) {
    Write-Host "  [OK]   $Message" -ForegroundColor Green
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Production - Environment Preflight" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Fail ".env does not exist: $EnvFile (copy .env.example to .env first)"
    exit 1
}

$envMap = @{}
foreach ($line in (Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*#' -or $line -notmatch '=') {
        continue
    }
    $key, $value = $line -split '=', 2
    $envMap[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
}

$weakPatterns = @(
    "change-me", "netops123456", "minioadmin", "django-insecure",
    "itsm-secret-2026", "my-secret-key", "guest", "admin123"
)

function Test-Weak([string]$Name, [string]$Value) {
    if (-not $Value) {
        return
    }
    foreach ($pattern in $weakPatterns) {
        if ($Value -ieq $pattern -or $Value -match [regex]::Escape($pattern)) {
            Warn "$Name still contains a default or weak secret fragment"
            return
        }
    }
}

if (-not $envMap["DEEPSEEK_API_KEY"] -and -not $envMap["LLM_API_KEY"]) {
    Warn "No LLM_API_KEY/DEEPSEEK_API_KEY; model-backed App operations will be unavailable"
} else {
    Ok "DEEPSEEK_API_KEY is configured"
}
if (-not $envMap["NETOPS_APP_INTERNAL_SECRET"] -or $envMap["NETOPS_APP_INTERNAL_SECRET"].Length -lt 32) {
    Fail "NETOPS_APP_INTERNAL_SECRET must contain at least 32 characters for App authorization"
}

foreach ($key in @("SECRET_KEY", "JWT_SECRET_KEY", "POSTGRES_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "ITSM_WEBHOOK_SECRET")) {
    if (-not $envMap[$key]) {
        Warn "$key is not configured"
    } else {
        Test-Weak $key $envMap[$key]
    }
}

if ($envMap["DJANGO_DEBUG"] -ieq "true") {
    Warn "DJANGO_DEBUG=true (set false in production)"
} else {
    Ok "DJANGO_DEBUG is not true"
}

if ($envMap["DEBUG"] -ieq "true") {
    Warn "DEBUG=true (set false in production)"
}

if ($envMap["ENFORCE_BFF_ORIGIN"] -ieq "false") {
    Warn "ENFORCE_BFF_ORIGIN=false (set true in production)"
} else {
    Ok "ENFORCE_BFF_ORIGIN is enabled or not explicitly disabled"
}

if ($envMap["LOG_FORMAT"] -ne "json") {
    Warn "LOG_FORMAT is not json (json is recommended for production log collection)"
} else {
    Ok "LOG_FORMAT=json"
}

if (-not $envMap["CELERY_BROKER_URL"]) {
    Warn "CELERY_BROKER_URL is not configured"
}

Write-Host ""
if ($failed) {
    Write-Host "Preflight failed. Correct .env and retry." -ForegroundColor Red
    exit 1
}
if ($warned) {
    Write-Host "Preflight completed with warnings." -ForegroundColor Yellow
    exit 0
}
Write-Host "Preflight passed." -ForegroundColor Green
exit 0
