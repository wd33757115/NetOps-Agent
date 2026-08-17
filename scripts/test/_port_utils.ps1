# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Shared helpers for test start/stop scripts (netstat port kill + HTTP wait)

function Write-ColorOutput {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

. (Join-Path (Split-Path $PSScriptRoot -Parent) "_docker_utils.ps1")

function Get-ListeningPidsOnPort {
    param([int]$Port)
    $pids = New-Object System.Collections.Generic.HashSet[int]
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $procId = $_.OwningProcess
            if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
                [void]$pids.Add([int]$procId)
            }
        }
    return @($pids)
}

function Stop-ProjectCeleryWorkers {
    param([string]$ProjectRoot)
    $pattern = [regex]::Escape($ProjectRoot)
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            ($_.CommandLine -match $pattern) -and
            ($_.CommandLine -match 'celery.*celery_app')
        } |
        ForEach-Object {
            Write-ColorOutput "    Stop Celery worker PID $($_.ProcessId)" "Gray"
            cmd /c "taskkill /F /T /PID $($_.ProcessId)" 2>$null | Out-Null
        }
}

function Stop-ProjectAppProcesses {
    param([string]$ProjectRoot)
    $pattern = [regex]::Escape($ProjectRoot)
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $pattern) } |
        ForEach-Object {
            Write-ColorOutput "    Stop project process PID $($_.ProcessId)" "Gray"
            cmd /c "taskkill /F /T /PID $($_.ProcessId)" 2>$null | Out-Null
        }
}

function Kill-ProcessOnPort {
    param([int]$Port)
    $pids = Get-ListeningPidsOnPort -Port $Port
    if ($pids.Count -eq 0) {
        Write-ColorOutput "    Port $Port is free" "Green"
        return $true
    }
    foreach ($portPid in $pids) {
        Write-ColorOutput "    Port $Port used by PID $portPid, killing..." "Yellow"
        cmd /c "taskkill /F /T /PID $portPid" 2>$null | Out-Null
        Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $still = Get-ListeningPidsOnPort -Port $Port
    if ($still.Count -gt 0) {
        Write-ColorOutput ("    Warning: port $Port still used by PIDs " + ($still -join ", ")) "Red"
        return $false
    }
    Write-ColorOutput "    Port $Port is now free" "Green"
    return $true
}

function Stop-ProcessesOnPorts {
    param([int[]]$Ports)
    foreach ($port in $Ports) {
        Kill-ProcessOnPort -Port $port | Out-Null
    }
}

function Test-PortsFree {
    param([int[]]$Ports)
    $busy = @()
    foreach ($port in $Ports) {
        if ((Get-ListeningPidsOnPort -Port $port).Count -gt 0) { $busy += $port }
    }
    return $busy
}

function Test-PortBindable {
    param(
        [int]$Port,
        [string]$PythonExe = "python"
    )
    # Port already in use by a live listener — not bindable for a new process
    if ((Get-ListeningPidsOnPort -Port $Port).Count -gt 0) {
        return $false
    }
    $script = @"
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('0.0.0.0', $Port))
    print('ok')
finally:
    s.close()
"@
    $tmp = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $tmp -Value $script -Encoding UTF8
    $out = & $PythonExe $tmp 2>&1
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    return ($out -match 'ok')
}

function Test-ProjectServiceHealthy {
    param(
        [int]$Port,
        [string]$HealthUrl
    )
    if ((Get-ListeningPidsOnPort -Port $Port).Count -eq 0) { return $false }
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Wait-ReactReady {
    param(
        [int]$Port = 3000,
        [int]$TimeoutSec = 90
    )
    $result = Wait-ServicesReady -ReactPort $Port -TimeoutSec $TimeoutSec
    return $result.React
}

function Wait-ServicesReady {
    param(
        [int]$ReactPort = 3000,
        [int]$DjangoPort = 8001,
        [int]$FastAPIPort = 8000,
        [int]$TimeoutSec = 300,
        [int]$IntervalSec = 3,
        [switch]$PreviewMode
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $reactUrl = "http://localhost:$ReactPort/"
    $bffUrl = "http://localhost:$DjangoPort/api/health/"
    $apiUrl = "http://localhost:$FastAPIPort/health"
    $reactOk = $false
    $bffOk = $false
    $apiOk = $false
    $lastStatus = ""

    while ((Get-Date) -lt $deadline) {
        if (-not $reactOk) {
            try {
                $html = (Invoke-WebRequest -Uri $reactUrl -UseBasicParsing -TimeoutSec 8).Content
                $isDev = ($html -match '@vite/client') -and ($html -match '/src/main\.tsx' -or $html -match 'html-proxy')
                $isPreview = ($html -match '/assets/index-[^"]+\.js')
                if ($isDev -or $isPreview) { $reactOk = $true }
            } catch { }
        }
        if (-not $bffOk) {
            try {
                $r = Invoke-WebRequest -Uri $bffUrl -UseBasicParsing -TimeoutSec 8
                if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $bffOk = $true }
            } catch { }
        }
        if (-not $apiOk) {
            try {
                $r = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 8
                if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $apiOk = $true }
            } catch { }
        }

        $reactState = if ($reactOk) { "OK" } else { "..." }
        $bffState = if ($bffOk) { "OK" } else { "..." }
        $apiState = if ($apiOk) { "OK" } else { "..." }
        $status = "React=$reactState BFF=$bffState FastAPI=$apiState"
        if ($status -ne $lastStatus) {
            Write-ColorOutput "  $status" "Gray"
            $lastStatus = $status
        }
        if ($reactOk -and $bffOk -and $apiOk) {
            return @{ React = $true; Bff = $true; Api = $true }
        }
        Start-Sleep -Seconds $IntervalSec
    }
    return @{ React = $reactOk; Bff = $bffOk; Api = $apiOk }
}

function Stop-DockerAppContainers {
    param(
        [switch]$SkipAvailabilityCheck,
        [int]$TimeoutSec = 10
    )

    if (-not $SkipAvailabilityCheck -and -not (Test-DockerAvailable -TimeoutSec ([Math]::Min($TimeoutSec, 6)) -Quiet)) {
        Write-ColorOutput "  [WARN] Docker is unavailable; skipped Docker app-container cleanup" "Yellow"
        return $false
    }

    $appContainers = @("netops-react", "netops-django", "netops-fastapi")
    foreach ($name in $appContainers) {
        $stopResult = Invoke-DockerCommand -Arguments @("stop", $name) -TimeoutSec $TimeoutSec -Quiet
        if ($stopResult.TimedOut) {
            Write-ColorOutput "  [WARN] Docker app-container cleanup timed out" "Yellow"
            return $false
        }
        $removeResult = Invoke-DockerCommand -Arguments @("rm", $name) -TimeoutSec $TimeoutSec -Quiet
        if ($removeResult.TimedOut) {
            Write-ColorOutput "  [WARN] Docker app-container cleanup timed out" "Yellow"
            return $false
        }
        if ($removeResult.Succeeded) {
            Write-ColorOutput "    Removed Docker app container: $name" "Gray"
        }
    }
    $fullCompose = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "deployment\docker-compose.yml"
    if (Test-Path $fullCompose) {
        $composeResult = Invoke-DockerCommand -Arguments @("compose", "-f", $fullCompose, "stop", "react", "django", "fastapi") -TimeoutSec ([Math]::Max($TimeoutSec, 20)) -Quiet
        if ($composeResult.TimedOut) {
            Write-ColorOutput "  [WARN] Docker app-service cleanup timed out" "Yellow"
            return $false
        }
    }
    return $true
}

function Ensure-DockerReady {
    param(
        [string]$ComposeFile,
        [switch]$SkipAvailabilityCheck
    )
    if (-not $SkipAvailabilityCheck -and -not (Test-DockerAvailable -TimeoutSec 8)) {
        return $false
    }
    return (Invoke-DockerComposeUp -ComposeFile $ComposeFile -Label "middleware")
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSec = 60,
        [int]$IntervalSec = 3
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch {
            # retry
        }
        Start-Sleep -Seconds $IntervalSec
    }
    return $false
}

function Invoke-DockerComposeUp {
    param(
        [string]$ComposeFile,
        [string]$Label = "compose",
        [int]$TimeoutSec = 120
    )
    if (-not (Test-Path $ComposeFile)) {
        Write-ColorOutput "  [SKIP] $Label compose not found: $ComposeFile" "Gray"
        return $false
    }
    Write-ColorOutput "  Starting $Label..." "Gray"
    $result = Invoke-DockerCommand -Arguments @("compose", "-f", $ComposeFile, "up", "-d", "--remove-orphans") -TimeoutSec $TimeoutSec -ShowOutput -ProgressLabel "Starting $Label"
    if (-not $result.Succeeded) {
        Write-ColorOutput "  [ERROR] $Label docker compose up failed" "Red"
        return $false
    }
    return $true
}

function Invoke-DockerComposeDown {
    param(
        [string]$ComposeFile,
        [string]$Label = "compose",
        [int]$TimeoutSec = 45
    )
    if (-not (Test-Path $ComposeFile)) {
        return $true
    }
    $result = Invoke-DockerCommand -Arguments @("compose", "-f", $ComposeFile, "down") -TimeoutSec $TimeoutSec -Quiet
    if ($result.Succeeded) {
        Write-ColorOutput "  [OK] $Label stopped" "Green"
        return $true
    }
    if ($result.TimedOut) {
        Write-ColorOutput "  [WARN] $Label stop timed out after ${TimeoutSec}s" "Yellow"
        return $false
    }
    Write-ColorOutput "  [WARN] $Label docker compose down failed" "Yellow"
    return $false
}

function Wait-TcpPortReady {
    param(
        [int]$Port,
        [string]$Label,
        [int]$TimeoutSec = 90,
        [int]$IntervalSec = 2
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ListeningPidsOnPort -Port $Port).Count -gt 0) {
            Write-ColorOutput "  [OK] $Label port $Port is listening" "Green"
            return $true
        }
        Start-Sleep -Seconds $IntervalSec
    }
    Write-ColorOutput "  [ERROR] $Label port $Port not ready after ${TimeoutSec}s" "Red"
    return $false
}

function Get-DockerContainersRunning {
    param([string[]]$Names)
    $running = @()
    foreach ($name in $Names) {
        $result = Invoke-DockerCommand -Arguments @("ps", "--filter", "name=$name", "--filter", "status=running", "--format", "{{.Names}}") -TimeoutSec 6 -Quiet
        if ($result.TimedOut) {
            break
        }
        if ($result.Succeeded -and $result.StdOut.Trim() -eq $name) {
            $running += $name
        }
    }
    return $running
}
