# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Production smoke tests: BFF health, auth, diagnostics, workflows, and RBAC.
param(
    [string]$BffBase = "http://127.0.0.1:8001",
    [string]$AdminUser = "admin",
    [string]$AdminPassword = "admin123",
    [string]$ViewerUser = "viewer",
    [string]$ViewerPassword = "viewer123",
    [switch]$SkipViewerTest,
    [switch]$SkipDockerCheck
)

$ErrorActionPreference = "Continue"
$failed = $false

function Assert-Ok($name, $cond, [string]$detail = "") {
    if ($cond) {
        Write-Host "  [OK] $name" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $name $(if ($detail) { $detail })" -ForegroundColor Red
        $script:failed = $true
    }
}

function Invoke-BffJson {
    param(
        [string]$Method = "GET",
        [string]$Path,
        [hashtable]$Headers = @{},
        $Body = $null
    )
    $uri = "$BffBase$Path"
    $params = @{
        Method      = $Method
        Uri         = $uri
        Headers     = $Headers
        TimeoutSec  = 60
    }
    if ($Body -ne $null) {
        $params.Body = ($Body | ConvertTo-Json -Compress)
        $params.ContentType = "application/json"
    }
    return Invoke-RestMethod @params
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NetOps Production - Smoke Test" -ForegroundColor Cyan
Write-Host "  BFF: $BffBase" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan

# Docker containers (optional).
if (-not $SkipDockerCheck) {
    try {
        $ps = docker compose -f (Join-Path $PSScriptRoot "../../deployment/docker-compose.yml") ps --format json 2>$null | ConvertFrom-Json
        $names = @($ps | ForEach-Object { $_.Name })
        foreach ($svc in @(
            "netops-celery-patrol",
            "netops-celery-control",
            "netops-celery-beat",
            "netops-fastapi",
            "netops-django",
            "netops-react"
        )) {
            $up = $names -contains $svc -and ($ps | Where-Object { $_.Name -eq $svc }).State -match "running"
            Assert-Ok "Docker $svc running" $up
        }
    } catch {
        Write-Host "  [SKIP] Docker compose ps ($($_.Exception.Message))" -ForegroundColor Yellow
    }
}

# 1. Health
try {
    $h = Invoke-BffJson -Path "/api/health/"
    Assert-Ok "BFF /api/health/" ($h.success -eq $true -and ($h.data.status -eq "healthy" -or $h.data.status -eq "degraded"))
} catch {
    Assert-Ok "BFF /api/health/" $false $_.Exception.Message
}

# 2. Diagnostics, including Celery workers and broker.
try {
    $d = Invoke-BffJson -Path "/api/health/diagnostics/"
    Assert-Ok "BFF /api/health/diagnostics/" ($d.success -eq $true)
    $checks = $d.data.checks
    if ($checks) {
        $celeryW = $checks | Where-Object { $_.id -eq "celery_worker" } | Select-Object -First 1
        if ($celeryW) {
            Assert-Ok "Celery Worker (diagnostics)" ($celeryW.status -eq "ok") $celeryW.message
        } else {
            Assert-Ok "Celery Worker (diagnostics)" $false "check missing"
        }
        $celeryBeat = $checks | Where-Object { $_.id -eq "celery_beat" } | Select-Object -First 1
        if ($celeryBeat) {
            Assert-Ok "Celery Beat (diagnostics)" ($celeryBeat.status -eq "ok") $celeryBeat.message
        } else {
            Assert-Ok "Celery Beat (diagnostics)" $false "check missing"
        }
        $pg = $checks | Where-Object { $_.id -eq "postgres" } | Select-Object -First 1
        if ($pg) {
            Assert-Ok "PostgreSQL (diagnostics)" ($pg.status -eq "ok") $pg.message
        }
    }
} catch {
    Assert-Ok "BFF /api/health/diagnostics/" $false $_.Exception.Message
}

# 3. Admin login
$token = $null
try {
    $login = Invoke-BffJson -Method POST -Path "/api/auth/login/" -Body @{
        username = $AdminUser
        password = $AdminPassword
    }
    $token = $login.data.access
    Assert-Ok "Admin login" ([bool]$token)
} catch {
    Assert-Ok "Admin login" $false $_.Exception.Message
}

if ($token) {
    $auth = @{ Authorization = "Bearer $token" }

    try {
        $me = Invoke-BffJson -Path "/api/auth/me/" -Headers $auth
        Assert-Ok "GET /api/auth/me/" ($me.data.username -eq $AdminUser)
    } catch {
        Assert-Ok "GET /api/auth/me/" $false $_.Exception.Message
    }

    try {
        $tpl = Invoke-BffJson -Path "/api/workflows/templates/" -Headers $auth
        $hasItsm = $false
        if ($tpl.data) {
            $hasItsm = @($tpl.data | ForEach-Object { $_.name }) -contains "itsm-firewall-change"
        }
        Assert-Ok "Workflow templates API" ($tpl.success -eq $true) 
        Assert-Ok "Template itsm-firewall-change" $hasItsm
    } catch {
        Assert-Ok "Workflow templates API" $false $_.Exception.Message
    }

    try {
        $skills = Invoke-BffJson -Path "/api/skills/" -Headers $auth
        Assert-Ok "Skills list API" ($skills.success -eq $true -or ($skills.data -ne $null))
    } catch {
        Assert-Ok "Skills list API" $false $_.Exception.Message
    }

    try {
        $readiness = Invoke-BffJson -Path "/api/devices/patrol/readiness/" -Headers $auth
        $detail = if ($readiness.data.blockers) {
            ($readiness.data.blockers | ForEach-Object { $_.message }) -join "; "
        } else { "" }
        Assert-Ok "Formal patrol production readiness" (
            $readiness.success -eq $true -and $readiness.data.can_launch -eq $true
        ) $detail
    } catch {
        Assert-Ok "Formal patrol production readiness" $false $_.Exception.Message
    }
}

# 4. Viewer RBAC: privileged writes are denied.
if (-not $SkipViewerTest) {
    try {
        $vLogin = Invoke-BffJson -Method POST -Path "/api/auth/login/" -Body @{
            username = $ViewerUser
            password = $ViewerPassword
        }
        $vToken = $vLogin.data.access
        Assert-Ok "Viewer login" ([bool]$vToken)
        if ($vToken) {
            $vAuth = @{ Authorization = "Bearer $vToken" }
            try {
                Invoke-BffJson -Method POST -Path "/api/skills/reload-all/" -Headers $vAuth | Out-Null
                Assert-Ok "Viewer denied skill reload" $false "expected 403"
            } catch {
                $code = $_.Exception.Response.StatusCode.value__
                Assert-Ok "Viewer denied skill reload (403)" ($code -eq 403) "status=$code"
            }
        }
    } catch {
        Assert-Ok "Viewer login" $false $_.Exception.Message
    }
}

# 5. React static application.
try {
    $webBase = $BffBase -replace ":8001", ":3000"
    if ($webBase -eq $BffBase) { $webBase = "http://127.0.0.1:3000" }
    $html = (Invoke-WebRequest "$webBase/" -UseBasicParsing -TimeoutSec 15).Content
    Assert-Ok "React index" ($html -match "root")
} catch {
    Assert-Ok "React index" $false $_.Exception.Message
}

Write-Host ""
if ($failed) {
    Write-Host "Smoke test FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "Smoke test PASSED" -ForegroundColor Green
exit 0
