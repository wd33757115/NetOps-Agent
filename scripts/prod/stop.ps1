# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Production: stop Docker Compose stack
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$RemoveVolumes,
    [int]$DockerTimeoutSec = 90
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Production - Stop" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Set-Location $ProjectRoot
. (Join-Path $ProjectRoot "scripts\_docker_utils.ps1")
$composeFile = Join-Path $ProjectRoot "deployment\docker-compose.yml"

$probeTimeoutSec = [Math]::Min(8, $DockerTimeoutSec)
if (-not (Test-DockerAvailable -TimeoutSec $probeTimeoutSec)) {
    Write-Host "`n[ERROR] Docker is unavailable; production containers may still be running." -ForegroundColor Red
    exit 1
}

$aiCompose = Join-Path $ProjectRoot "deployment\docker-compose.ai.yml"
$downArgs = @("compose", "-f", $composeFile, "-f", $aiCompose, "--profile", "core", "--profile", "ai", "down")
if ($RemoveVolumes) {
    $downArgs += "-v"
}
$downResult = Invoke-DockerCommand -Arguments $downArgs -TimeoutSec $DockerTimeoutSec -ShowOutput -ProgressLabel "Stopping production containers"
if (-not $downResult.Succeeded) {
    Write-Host "`n[ERROR] Production stop failed or timed out; inspect Docker Desktop." -ForegroundColor Red
    exit 1
}

Write-Host "`n[Done] Production stopped" -ForegroundColor Green
