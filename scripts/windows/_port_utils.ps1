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
            ($_.CommandLine -match '(celery.*celery_app|src\.platform\.kernel\.worker)')
        } |
        ForEach-Object {
            Write-ColorOutput "    Stop Celery worker PID $($_.ProcessId)" "Gray"
            cmd /c "taskkill /F /T /PID $($_.ProcessId)" 2>$null | Out-Null
        }
}

function Test-ProjectAppProcess {
    param($Process, [string]$ProjectRoot)
    $line = $Process.CommandLine
    if (-not $line) { return $false }
    $rootPattern = [regex]::Escape($ProjectRoot.TrimEnd('\', '/')) + '(?:[\\/]|["\s])'
    if ($line -notmatch $rootPattern) { return $false }
    if ($Process.Name -eq 'python.exe') {
        # Match only service entry points, never every process using the project venv.
        $entry = '^\s*(?:"[^"]*python\.exe"|\S*python\.exe)\s+-m\s+'
        $service = '(?:uvicorn\s+src\.gateway\.main:app(?:\s|$)|daphne\b.*\bdjango_backend\.asgi:application(?:\s|$)|src\.platform\.kernel\.worker(?:\s|$)|celery\s+-A\s+src\.core\.celery_tasks\.celery_app\s+(?:worker|beat)(?:\s|$))'
        return $line -match ($entry + $service)
    }
    if ($Process.Name -eq 'node.exe') {
        $vite = Join-Path $ProjectRoot 'web\react_frontend\node_modules\vite\bin\vite.js'
        foreach ($match in [regex]::Matches($line, '"([^"]+)"|(\S+)')) {
            $argument = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
            if ($argument -notmatch 'vite\.js$') { continue }
            try {
                # npm invokes .bin\..\vite on Windows; compare the resolved path.
                if ([IO.Path]::GetFullPath($argument) -eq $vite) { return $true }
            } catch { }
        }
        return $false
    }
    return $false
}

function Clear-StaleProjectBeatPid {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $root = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path.TrimEnd('\', '/')
    $marker = Join-Path $root '.runtime\test\celery-beat.pid'
    # Query failures must not be interpreted as an empty process list.
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop)
    $running = @($processes | Where-Object {
        (Test-ProjectAppProcess -Process $_ -ProjectRoot $root) -and
        $_.CommandLine -match '-m\s+celery\s+-A\s+src\.core\.celery_tasks\.celery_app\s+beat(?:\s|$)'
    })
    if ($running.Count) { throw 'Project Celery Beat is still running; keep its PID file and do not start another scheduler.' }
    if (-not (Test-Path -LiteralPath $marker -ErrorAction Stop)) { return }
    foreach ($path in @((Join-Path $root '.runtime'), (Join-Path $root '.runtime\test'), $marker)) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to clean a Beat PID file through a filesystem link.'
        }
    }
    $content = [IO.File]::ReadAllText($marker)
    $recordedId = 0
    if (-not [int]::TryParse($content.Trim(), [ref]$recordedId) -or $recordedId -le 0) {
        throw 'Invalid Celery Beat PID file; inspect it before restarting the scheduler.'
    }
    $owner = @(Get-CimInstance Win32_Process -Filter "ProcessId=$recordedId" -ErrorAction Stop)
    if ($owner.Count) { throw "Beat PID file refers to live PID $recordedId; it has been preserved." }
    if ([IO.File]::ReadAllText($marker) -ne $content) { throw 'Beat PID file changed during inspection; retry after checking the scheduler.' }
    Remove-Item -LiteralPath $marker -Force -ErrorAction Stop
    Write-ColorOutput "  [OK] Removed stale Celery Beat PID $recordedId" 'Gray'
}

function Test-ProjectLauncherProcess {
    param($Process, [string]$ProjectRoot)
    if (-not $Process -or $Process.Name -notin @('powershell.exe', 'pwsh.exe')) { return $false }
    $directory = [regex]::Escape((Join-Path $ProjectRoot '.runtime\test\logs'))
    return $Process.CommandLine -match ('\s-File\s+"?' + $directory + '\\[a-z0-9-]+-launcher\.ps1"?(?:\s|$)')
}

function Stop-RecordedProjectLaunchers {
    param($Records, [string]$ProjectRoot)
    foreach ($property in $Records.PSObject.Properties) {
        $procId = [int]$property.Value
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if (Test-ProjectLauncherProcess -Process $process -ProjectRoot $ProjectRoot) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } else {
            Write-ColorOutput "    Skip stale/unrelated launcher PID $procId" 'Gray'
        }
    }
}

function Stop-ProjectAppProcesses {
    param([string]$ProjectRoot)
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { Test-ProjectAppProcess -Process $_ -ProjectRoot $ProjectRoot } |
        ForEach-Object {
            Write-ColorOutput "    Stop project service PID $($_.ProcessId)" "Gray"
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

    # Switching from Compose to local processes must not leave a second Beat or
    # old queue consumers alive against the same broker.
    $appContainers = @(
        "netops-celery-beat", "netops-celery-patrol", "netops-celery-control", "netops-celery-ai",
        "netops-react", "netops-django", "netops-fastapi"
    )
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
    return $true
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

function Import-DotEnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $vars[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    }
    return $vars
}

function Initialize-WindowsRuntimeEnvironment {
    param([string]$ProjectRoot)
    $dotEnv = Import-DotEnvFile -Path (Join-Path $ProjectRoot ".env")
    $env:POSTGRES_HOST = "127.0.0.1"
    $env:REDIS_HOST = "127.0.0.1"
    foreach ($key in @("NETOPS_APP_INTERNAL_SECRET", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")) {
        if (-not [Environment]::GetEnvironmentVariable($key) -and $dotEnv.ContainsKey($key)) {
            [Environment]::SetEnvironmentVariable($key, $dotEnv[$key], "Process")
        }
    }
    if (-not $env:CELERY_BROKER_URL) { $env:CELERY_BROKER_URL = "amqp://guest:guest@localhost:5672//" }
    if (-not $env:CELERY_RESULT_BACKEND) { $env:CELERY_RESULT_BACKEND = "redis://localhost:6379/0" }
    # Match Docker's IPv4-only host bindings; localhost may try an unresponsive IPv6 proxy.
    $env:MINIO_ENDPOINT = '127.0.0.1:9000'
    $env:CELERY_BROKER_URL = $env:CELERY_BROKER_URL.Replace('@localhost:', '@127.0.0.1:').Replace('://localhost:', '://127.0.0.1:')
    $env:CELERY_RESULT_BACKEND = $env:CELERY_RESULT_BACKEND.Replace('://localhost:', '://127.0.0.1:')
    $env:PGCONNECT_TIMEOUT = '10'
}
