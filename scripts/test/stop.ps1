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
Write-ColorOutput "  NetOps Test Environment - Stop" "Cyan"
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
    foreach ($name in $pids.PSObject.Properties.Name) {
        Stop-Process -Id ([int]$pids.$name) -Force -ErrorAction SilentlyContinue
        Write-ColorOutput "  [OK] Stopped $name" "Green"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $ProjectRoot ".runtime\test\celery-beat.pid") -Force -ErrorAction SilentlyContinue

Write-ColorOutput "[Step 2/3] Free ports 8000 / 8001 / 3000..." "Yellow"
# 3001 留给 Langfuse（deployment / docker/langfuse），勿释放以免误杀 Docker 转发进程
# 3002 仅清理 Vite 等偶发占用的备用端口
Stop-ProcessesOnPorts -Ports @(8000, 8001, 3000, 3002)

if (-not $KeepMiddleware -or -not $KeepLangfuse) {
    Write-ColorOutput "[Step 3/3] Stop Docker dependencies..." "Yellow"
    $middlewareCompose = Join-Path $ProjectRoot "deployment\docker-compose.middleware.yml"
    $langfuseCompose = Join-Path $ProjectRoot "docker\langfuse\docker-compose.yml"

    if (-not $dockerOk) {
        Write-ColorOutput "  [WARN] Docker dependencies were not stopped because Docker is unavailable" "Yellow"
    } elseif ($KeepMiddleware) {
        Write-ColorOutput "  [SKIP] Middleware (KeepMiddleware)" "Gray"
    } else {
        Invoke-DockerComposeDown -ComposeFile $middlewareCompose -Label "Middleware" | Out-Null
    }

    if (-not $dockerOk) {
        # The warning above covers both dependency stacks.
    } elseif ($KeepLangfuse) {
        Write-ColorOutput "  [SKIP] Langfuse (KeepLangfuse)" "Gray"
    } else {
        Invoke-DockerComposeDown -ComposeFile $langfuseCompose -Label "Langfuse" | Out-Null
    }
}

Write-ColorOutput "" "Cyan"
Write-ColorOutput "[Done] Test environment stopped" "Green"
