# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Start a standalone Celery worker. Windows requires the solo pool.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$LogLevel = "info",
    [string]$Queues = "netops.default,netops.firewall,netops.device,netops.health,netops.patrol,netops.ai",
    [string]$Pool = "solo",
    [string]$NodeName = "netops-standalone@%h",
    [int]$DjangoPort = 8001,
    [switch]$KeepExisting
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_port_utils.ps1")
Initialize-WindowsRuntimeEnvironment -ProjectRoot $ProjectRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] venv not found. Run scripts\windows\install.ps1" -ForegroundColor Red
    exit 1
}

if ($Pool -ne "solo" -and $env:OS -match "Windows") {
    Write-Host "[WARN] Windows 上 prefork 易触发 PermissionError WinError 5，已强制 -P solo" -ForegroundColor Yellow
    $Pool = "solo"
}

if (-not $KeepExisting) {
    Write-Host "Stopping existing Celery workers for this project ..." -ForegroundColor Yellow
    Stop-ProjectCeleryWorkers -ProjectRoot $ProjectRoot
    Start-Sleep -Seconds 1
}

Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue
$env:NETOPS_AUTHORITY_URL = "http://localhost:$DjangoPort"

Write-Host "Starting Celery worker (pool=$Pool, queues=$Queues) ..." -ForegroundColor Cyan
$concurrencyArgs = if ($Pool -eq "solo") { @("--concurrency=1") } else { @() }
& $venvPython -m src.platform.kernel.worker `
    --loglevel=$LogLevel `
    -P $Pool `
    @concurrencyArgs `
    --hostname=$NodeName `
    --queues $Queues
