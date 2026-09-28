# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Test env: stop local processes and Docker dependencies (middleware + Langfuse)
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$KeepMiddleware,
    [switch]$KeepLangfuse
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_port_utils.ps1")

Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  NetOps Windows - Stop" "Cyan"
Write-ColorOutput "============================================================" "Cyan"

$pidFile = Join-Path $ProjectRoot ".runtime\test\pids.json"

Write-ColorOutput "[Step 1/3] Stop processes from PID file..." "Yellow"
Stop-ProjectAppProcesses -ProjectRoot $ProjectRoot
$dockerOk = Test-DockerAvailable -TimeoutSec 5 -Quiet
if ($dockerOk) {
    $dockerOk = [bool](Stop-DockerAppContainers -SkipAvailabilityCheck)
    if (-not $dockerOk) {
        Write-ColorOutput "  [WARN] Docker stopped responding during app-container cleanup" "Yellow"
    }
} else {
    Write-ColorOutput "  [WARN] Docker is not responding; Docker cleanup will be skipped" "Yellow"
}
if (Test-Path $pidFile) {
    $pids = Get-Content $pidFile -Raw | ConvertFrom-Json
    Stop-RecordedProjectLaunchers -Records $pids -ProjectRoot $ProjectRoot
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
Clear-StaleProjectBeatPid -ProjectRoot $ProjectRoot

Write-ColorOutput "[Step 2/3] Free ports 8000 / 8001 / 3000..." "Yellow"
# 3001 留给 Langfuse（deployment / docker/langfuse），勿释放以免误杀 Docker 转发进程
# 3002 仅清理 Vite 等偶发占用的备用端口
Stop-ProcessesOnPorts -Ports @(8000, 8001, 3000, 3002)

if (-not $KeepMiddleware -or -not $KeepLangfuse) {
    & (Join-Path $PSScriptRoot 'containers.ps1') -ProjectRoot $ProjectRoot -Action stop -SkipMiddleware:$KeepMiddleware -WithLangfuse:(-not $KeepLangfuse)
}

Write-ColorOutput "" "Cyan"
Write-ColorOutput "[Done] Windows services stopped" "Green"
