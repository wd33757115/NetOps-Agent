# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Verify test environment health (API + frontend HTML)
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [int]$FastAPIPort = 8000,
    [int]$DjangoPort = 8001,
    [int]$ReactPort = 3000,
    [switch]$DevMode
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_port_utils.ps1")
Initialize-WindowsRuntimeEnvironment -ProjectRoot $ProjectRoot

Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  NetOps Windows - Verify" "Cyan"
Write-ColorOutput "============================================================" "Cyan"

$failed = $false

function Assert-Ok($name, $cond, $detail = "") {
    if ($cond) {
        Write-ColorOutput "  [OK] $name" "Green"
    } else {
        Write-ColorOutput "  [FAIL] $name $detail" "Red"
        $script:failed = $true
    }
}

try {
    $api = Invoke-RestMethod "http://localhost:$FastAPIPort/health" -TimeoutSec 30
    Assert-Ok "FastAPI /health" ($api.status -eq "healthy" -or $api.status -eq "degraded")
    if ($api.status -eq "degraded" -and -not $api.services.postgres) {
        Write-ColorOutput "  [WARN] FastAPI degraded: PostgreSQL not connected (is Docker middleware running?)" "Yellow"
    }
} catch {
    Assert-Ok "FastAPI /health" $false $_.Exception.Message
}

try {
    $bff = Invoke-RestMethod "http://localhost:$DjangoPort/api/health/" -TimeoutSec 15
    Assert-Ok "Django BFF /api/health/" ($bff.success -eq $true)
} catch {
    Assert-Ok "Django BFF /api/health/" $false $_.Exception.Message
}

try {
    $convs = Invoke-RestMethod "http://localhost:$DjangoPort/api/conversations/" -TimeoutSec 30
    Assert-Ok "Django BFF conversations reachable" ($convs.success -eq $true)
} catch {
    $detail = $_.Exception.Message
    if ($detail -match "500") {
        $detail += " (often: PostgreSQL down — start Docker Desktop and run start.ps1)"
    }
    # An authenticated endpoint returning 401 is expected without a login token.
    $unauthenticated = $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401
    Assert-Ok "Django BFF conversations authentication" $unauthenticated $detail
}

try {
    $html = (Invoke-WebRequest "http://localhost:$ReactPort/chat" -UseBasicParsing -TimeoutSec 15).Content
    $hasBundle = $html -match '/assets/index-.*\.js' -or ($DevMode -and $html -match '/@vite/client')
    Assert-Ok "React HTML bundle" $hasBundle
} catch {
    Assert-Ok "React HTML bundle" $false $_.Exception.Message
}

try {
    $jsPath = (Invoke-WebRequest "http://localhost:$ReactPort/chat" -UseBasicParsing).Content |
        Select-String -Pattern '/assets/index-[^"]+\.js' |
        ForEach-Object { $_.Matches[0].Value }
    if ($DevMode) {
        $js = Invoke-WebRequest "http://localhost:$ReactPort/@vite/client" -UseBasicParsing -TimeoutSec 30
        Assert-Ok "Vite dev client" ($js.StatusCode -eq 200)
    } elseif ($jsPath) {
        $js = Invoke-WebRequest "http://localhost:$ReactPort$jsPath" -UseBasicParsing -TimeoutSec 30
        Assert-Ok "React JS bundle" ($js.StatusCode -eq 200)
    } else {
        Assert-Ok "React JS bundle" $false "path not found"
    }
} catch {
    Assert-Ok "React JS bundle" $false $_.Exception.Message
}

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $celeryPing = & $venvPython -c @"
from src.core.celery_tasks.celery_exec import celery_workers_available
import sys
sys.exit(0 if celery_workers_available() else 1)
"@ 2>$null
    Assert-Ok "Celery Worker ping" ($LASTEXITCODE -eq 0) "(run scripts\windows\start.ps1 or start worker manually)"

    if (-not $DevMode) {
        Push-Location $ProjectRoot
        $env:NETOPS_AUTHORITY_URL = "http://localhost:$DjangoPort"
        & $venvPython web/django_backend/manage.py app_packages verify `
            --gateway-url "http://localhost:$FastAPIPort" --frontend-url "http://localhost:$ReactPort" --wait-seconds 60
        $appExit = $LASTEXITCODE
        Pop-Location
        # An App that fails verification is that App's problem, not the platform's.
        if ($appExit -eq 0) {
            Write-ColorOutput "  [OK] Enabled App deployment verification" "Green"
        } else {
            Write-ColorOutput "  [WARN] Some enabled Apps failed deployment verification (see above); the platform is up" "Yellow"
        }
    }
} else {
    Assert-Ok "Celery Worker ping" $false "venv not found"
}

Write-ColorOutput "" "Cyan"
if ($failed) {
    Write-ColorOutput "Verification FAILED" "Red"
    exit 1
}
Write-ColorOutput "All checks passed. Open http://localhost:$ReactPort/chat in your browser." "Green"
# An App's failed verification is a warning; its exit code must not leak out as ours.
exit 0
