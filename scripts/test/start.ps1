# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Test env: Docker middleware + Langfuse + local FastAPI / Django / React
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [int]$FastAPIPort = 8000,
    [int]$DjangoPort = 8001,
    [int]$ReactPort = 3000,
    [switch]$SkipMiddleware,
    [switch]$SkipLangfuse,
    [switch]$AllowNoDocker,
    [switch]$UseSupervisorV1,
    [switch]$DevMode
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_port_utils.ps1")

function Import-DotEnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $vars[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
    }
    return $vars
}

function Escape-SingleQuotedPath([string]$Path) {
    return $Path.Replace("'", "''")
}

function New-LauncherScript {
    param(
        [string]$Path,
        [string]$WorkingDirectory,
        [hashtable]$EnvVars,
        [string]$CommandLine,
        [string]$LogFile,
        [string[]]$PreCommands = @()
    )
    $lines = New-Object System.Collections.Generic.List[string]
    [void]$lines.Add('$ErrorActionPreference = "Continue"')
    [void]$lines.Add("Set-Location -LiteralPath '$(Escape-SingleQuotedPath $WorkingDirectory)'")
    foreach ($cmd in $PreCommands) {
        [void]$lines.Add($cmd)
    }
    foreach ($key in ($EnvVars.Keys | Sort-Object)) {
        $val = Escape-SingleQuotedPath ($EnvVars[$key].ToString())
        [void]$lines.Add("`$env:$key = '$val'")
    }
    [void]$lines.Add("$CommandLine *>> '$(Escape-SingleQuotedPath $LogFile)'")
    $lines | Set-Content -Path $Path -Encoding UTF8
}

function Start-LauncherProcess {
    param([string]$LauncherPath)
    return Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $LauncherPath
    ) -PassThru -WindowStyle Minimized
}

function Wait-ProjectPythonCommand {
    param(
        [string]$ProjectRoot,
        [string]$CommandPattern,
        [int]$TimeoutSec = 15
    )
    $rootPattern = [regex]::Escape($ProjectRoot)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $match = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                ($_.CommandLine -match $rootPattern) -and
                ($_.CommandLine -match $CommandPattern)
            } |
            Select-Object -First 1
        if ($match) { return $true }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

# 3001 留给 Langfuse；React 固定 3000（vite strictPort），3002 为偶发备用端口清理
$AppPorts = @($FastAPIPort, $DjangoPort, $ReactPort, 3002)
$runtimeDir = Join-Path $ProjectRoot ".runtime\test"
$pidFile = Join-Path $runtimeDir "pids.json"
$logDir = Join-Path $ProjectRoot ".runtime\test\logs"
New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null

Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  NetOps Test Environment - Start" "Cyan"
Write-ColorOutput '  (Middleware Docker + Local App Processes)' "Cyan"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  Project: $ProjectRoot" "Gray"
Write-ColorOutput "  FastAPI: $FastAPIPort | Django: $DjangoPort | React: $ReactPort" "Gray"
Write-ColorOutput "" "Cyan"

Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-ColorOutput "  [ERROR] venv not found. Run scripts\test\install.ps1" "Red"
    exit 1
}

Write-ColorOutput "[Step 0/7] Stop old processes and free ports..." "Yellow"
Stop-ProjectAppProcesses -ProjectRoot $ProjectRoot
Stop-DockerAppContainers
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -Raw | ConvertFrom-Json
    foreach ($name in $old.PSObject.Properties.Name) {
        Stop-Process -Id ([int]$old.$name) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
# Celery Beat writes its own PID file. A forced Windows process-tree stop cannot
# remove that file, so a clean restart must discard the now-stale runtime marker.
$celeryBeatRuntimePid = Join-Path $runtimeDir "celery-beat.pid"
Remove-Item $celeryBeatRuntimePid -Force -ErrorAction SilentlyContinue
foreach ($round in 1..2) {
    foreach ($p in $AppPorts) { Kill-ProcessOnPort -Port $p | Out-Null }
    $busy = @()
    foreach ($port in @($FastAPIPort, $DjangoPort, $ReactPort)) {
        if (-not (Test-PortBindable -Port $port -PythonExe $venvPython)) { $busy += $port }
    }
    if ($busy.Count -eq 0) { break }
    if ($round -eq 2) {
        Write-ColorOutput ("  [ERROR] Ports not bindable: " + ($busy -join ", ")) "Red"
        Write-ColorOutput "  Hint: close old PowerShell launcher windows, run stop.ps1, or end python/node in Task Manager" "Yellow"
        exit 1
    }
    Start-Sleep -Seconds 2
}
Write-ColorOutput "  [OK] Ports cleared" "Green"

$dotEnv = Import-DotEnvFile -Path (Join-Path $ProjectRoot ".env")

Write-ColorOutput "[Step 1/7] Check Docker..." "Yellow"
$needDocker = (-not $SkipMiddleware) -or (-not $SkipLangfuse)
$dockerOk = $false
if ($SkipMiddleware -and $SkipLangfuse) {
    Write-ColorOutput '  [SKIP] SkipMiddleware + SkipLangfuse (no Docker stacks)' "Gray"
} elseif (Test-DockerAvailable) {
    Write-ColorOutput "  [OK] Docker is available" "Green"
    $dockerOk = $true
} elseif ($AllowNoDocker) {
    Write-ColorOutput '  [WARN] Docker not available; continuing (AllowNoDocker)' "Yellow"
} else {
    Write-ColorOutput "  [ERROR] Docker Desktop is not running." "Red"
    Write-ColorOutput "  Start Docker Desktop, wait until Ready, then run start.ps1 again." "Yellow"
    Write-ColorOutput '  Or use -SkipMiddleware -SkipLangfuse -AllowNoDocker for UI-only (backend will fail).' "Gray"
    exit 1
}

Write-ColorOutput "[Step 2/7] Start Docker dependencies..." "Yellow"
$middlewareCompose = Join-Path $ProjectRoot "deployment\docker-compose.middleware.yml"
$langfuseCompose = Join-Path $ProjectRoot "docker\langfuse\docker-compose.yml"
if ($dockerOk) {
    if ($SkipMiddleware) {
        Write-ColorOutput '  [SKIP] Middleware (SkipMiddleware)' "Gray"
    } else {
        if (-not (Ensure-DockerReady -ComposeFile $middlewareCompose)) {
            Write-ColorOutput "  [ERROR] middleware docker compose up failed" "Red"
            exit 1
        }
        $middlewarePorts = @(
            @{ Port = 5432; Label = "PostgreSQL" },
            @{ Port = 6379; Label = "Redis" },
            @{ Port = 5672; Label = "RabbitMQ" }
        )
        foreach ($item in $middlewarePorts) {
            if (-not (Wait-TcpPortReady -Port $item.Port -Label $item.Label -TimeoutSec 90)) {
                Write-ColorOutput "  [ERROR] Middleware not ready. Check: docker ps" "Red"
                exit 1
            }
        }
        $middleware = @("netops-postgres", "netops-redis", "netops-rabbitmq", "netops-minio", "netops-qdrant")
        foreach ($svc in $middleware) {
            $running = Get-DockerContainersRunning -Names @($svc)
            if ($running -contains $svc) {
                Write-ColorOutput "  [OK] $svc is running" "Green"
            } else {
                Write-ColorOutput "  [WAIT] $svc is starting..." "Yellow"
            }
        }
    }

    if ($SkipLangfuse) {
        Write-ColorOutput '  [SKIP] Langfuse (SkipLangfuse)' "Gray"
    } else {
        if (-not (Invoke-DockerComposeUp -ComposeFile $langfuseCompose -Label "Langfuse")) {
            Write-ColorOutput "  [ERROR] Langfuse docker compose up failed" "Red"
            exit 1
        }
        $langfuseContainers = @("netops-langfuse-standalone", "netops-langfuse-db")
        foreach ($svc in $langfuseContainers) {
            $running = Get-DockerContainersRunning -Names @($svc)
            if ($running -contains $svc) {
                Write-ColorOutput "  [OK] $svc is running" "Green"
            } else {
                Write-ColorOutput "  [WAIT] $svc is starting..." "Yellow"
            }
        }
        $langfuseOk = Wait-HttpOk -Url "http://localhost:3001/api/public/health" -TimeoutSec 60 -IntervalSec 3
        if ($langfuseOk) {
            Write-ColorOutput "  [OK] Langfuse http://localhost:3001" "Green"
        } else {
            Write-ColorOutput "  [WARN] Langfuse health check pending; see docker logs netops-langfuse-standalone" "Yellow"
        }
    }
} else {
    Write-ColorOutput '  [SKIP] Docker not available (middleware + Langfuse)' "Gray"
}

Write-ColorOutput "[Step 3/7] PostgreSQL + Django migrations..." "Yellow"
& $venvPython scripts/migrate_db.py *>&1 | Out-File (Join-Path $logDir "postgres-migrate.log") -Encoding utf8
if ($LASTEXITCODE -ne 0) {
    Write-ColorOutput "  [ERROR] PostgreSQL migration failed (log: postgres-migrate.log)" "Red"
    exit 1
}
Write-ColorOutput '  [OK] PostgreSQL migration done' "Green"
Push-Location (Join-Path $ProjectRoot "web\django_backend")
$env:DJANGO_SETTINGS_MODULE = "django_backend.settings"
$migrateLog = Join-Path $logDir "django-migrate.log"
& $venvPython manage.py migrate --noinput *>&1 | Out-File $migrateLog -Encoding utf8
Pop-Location
Write-ColorOutput '  [OK] migrate done (log: django-migrate.log)' "Green"

$useV2 = if ($UseSupervisorV1) { "false" } else { "true" }
$pids = @{}
$py = Escape-SingleQuotedPath $venvPython

$jwtSecret = if ($dotEnv.ContainsKey("JWT_SECRET_KEY")) { $dotEnv["JWT_SECRET_KEY"] } elseif ($dotEnv.ContainsKey("SECRET_KEY")) { $dotEnv["SECRET_KEY"] } else { "" }

$fastapiEnv = @{
    DEBUG = "true"
    ENFORCE_BFF_ORIGIN = "false"
    USE_SUPERVISOR_V2 = $useV2
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"
    QDRANT_HOST = "localhost"
    MINIO_ENDPOINT = "localhost:9000"
}
foreach ($k in @("DEEPSEEK_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PORT")) {
    if ($dotEnv.ContainsKey($k)) { $fastapiEnv[$k] = $dotEnv[$k] }
}
if ($jwtSecret) { $fastapiEnv["JWT_SECRET_KEY"] = $jwtSecret }
foreach ($k in @("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")) {
    if ($dotEnv.ContainsKey($k)) { $fastapiEnv[$k] = $dotEnv[$k] }
}
if ($dotEnv.ContainsKey("JWT_ALGORITHM")) { $fastapiEnv["JWT_ALGORITHM"] = $dotEnv["JWT_ALGORITHM"] }
if ($dotEnv.ContainsKey("BFF_REQUIRE_AUTH")) { $fastapiEnv["BFF_REQUIRE_AUTH"] = $dotEnv["BFF_REQUIRE_AUTH"] }

Write-ColorOutput '[Step 4/7] Start Celery Worker + Beat (Windows) ...' "Yellow"
$celeryLog = Join-Path $logDir "celery.log"
$celeryLauncher = Join-Path $logDir "celery-launcher.ps1"
$celeryEnv = @{
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"
}
foreach ($k in @("DEEPSEEK_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PORT", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")) {
    if ($dotEnv.ContainsKey($k)) { $celeryEnv[$k] = $dotEnv[$k] }
}
if ($dockerOk -and -not $SkipMiddleware) {
    if (-not $celeryEnv.ContainsKey("CELERY_BROKER_URL")) {
        $celeryEnv["CELERY_BROKER_URL"] = "amqp://guest:guest@localhost:5672//"
    }
    if (-not $celeryEnv.ContainsKey("CELERY_RESULT_BACKEND")) {
        $celeryEnv["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/0"
    }
}
if ($jwtSecret) { $celeryEnv["JWT_SECRET_KEY"] = $jwtSecret }
foreach ($k in @("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")) {
    if ($dotEnv.ContainsKey($k)) { $celeryEnv[$k] = $dotEnv[$k] }
}
$celeryCmd = "& '$py' -m celery -A src.core.celery_tasks.celery_app worker --loglevel=info -P solo -Q netops.default,netops.firewall,netops.device,netops.health,netops.patrol,netops.ai"
New-LauncherScript -Path $celeryLauncher -WorkingDirectory $ProjectRoot -EnvVars $celeryEnv -CommandLine $celeryCmd -LogFile $celeryLog -PreCommands @(
    'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
)
$pids.celery = (Start-LauncherProcess -LauncherPath $celeryLauncher).Id
Write-ColorOutput ("  [OK] Celery PID={0} (log: celery.log)" -f $pids.celery) "Green"
Write-ColorOutput "  [TIP] Celery worker running; do not start a second worker (pidbox conflict)" "Gray"
Write-ColorOutput "        Debug: scripts\test\celery_worker.ps1" "Gray"
$celeryBeatLog = Join-Path $logDir "celery-beat.log"
$celeryBeatLauncher = Join-Path $logDir "celery-beat-launcher.ps1"
$celeryBeatPid = Join-Path $runtimeDir "celery-beat.pid"
$celeryBeatSchedule = Join-Path $runtimeDir "celery-beat-schedule"
$celeryBeatCmd = "& '$py' -m celery -A src.core.celery_tasks.celery_app beat --loglevel=info --pidfile='$celeryBeatPid' --schedule='$celeryBeatSchedule'"
New-LauncherScript -Path $celeryBeatLauncher -WorkingDirectory $ProjectRoot -EnvVars $celeryEnv -CommandLine $celeryBeatCmd -LogFile $celeryBeatLog -PreCommands @(
    'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
)
$pids.celeryBeat = (Start-LauncherProcess -LauncherPath $celeryBeatLauncher).Id
if (-not (Wait-ProjectPythonCommand -ProjectRoot $ProjectRoot -CommandPattern '(?i)-m\s+celery\b.*\bbeat\b')) {
    Write-ColorOutput "  [ERROR] Celery Beat did not start. See celery-beat.log" "Red"
    exit 1
}
Write-ColorOutput ("  [OK] Celery Beat launcher PID={0} (log: celery-beat.log)" -f $pids.celeryBeat) "Green"
if ($dockerOk -and -not $SkipMiddleware) {
    Start-Sleep -Seconds 8
}

Write-ColorOutput "[Step 5/7] Start FastAPI :$FastAPIPort ..." "Yellow"
$fastapiLog = Join-Path $logDir "fastapi.log"
$fastapiLauncher = Join-Path $logDir "fastapi-launcher.ps1"
if ($DevMode) {
    # 开发模式才热重载；保存代码时会短暂停服，BFF 易出现 502
    $fastapiCmd = "& '$py' -m uvicorn src.gateway.main:app --host 0.0.0.0 --port $FastAPIPort --reload --reload-exclude '.runtime' --reload-exclude 'templates' --reload-exclude 'web/react_frontend'"
    Write-ColorOutput '  Mode: uvicorn --reload (DevMode; saves may cause brief 502)' "Gray"
} else {
    $fastapiCmd = "& '$py' -m uvicorn src.gateway.main:app --host 0.0.0.0 --port $FastAPIPort"
    Write-ColorOutput '  Mode: uvicorn stable (no auto-reload; recommended for daily use)' "Gray"
}
New-LauncherScript -Path $fastapiLauncher -WorkingDirectory $ProjectRoot -EnvVars $fastapiEnv -CommandLine $fastapiCmd -LogFile $fastapiLog
$pids.fastapi = (Start-LauncherProcess -LauncherPath $fastapiLauncher).Id
Write-ColorOutput "  [OK] FastAPI PID=$($pids.fastapi) USE_SUPERVISOR_V2=$useV2" "Green"

Write-ColorOutput "[Step 6/7] Start Django BFF :$DjangoPort ..." "Yellow"
$djangoLog = Join-Path $logDir "django.log"
$djangoDir = Join-Path $ProjectRoot "web\django_backend"
$djangoLauncher = Join-Path $logDir "django-launcher.ps1"
$djangoCmd = "& '$py' -m daphne -b 0.0.0.0 -p $DjangoPort django_backend.asgi:application"
$djangoEnv = @{
    DJANGO_DEBUG = "true"
    FASTAPI_BASE_URL = "http://localhost:$FastAPIPort"
}
if ($dotEnv.ContainsKey("SECRET_KEY")) { $djangoEnv["SECRET_KEY"] = $dotEnv["SECRET_KEY"] }
if ($dotEnv.ContainsKey("JWT_SECRET_KEY")) { $djangoEnv["JWT_SECRET_KEY"] = $dotEnv["JWT_SECRET_KEY"] }
elseif ($jwtSecret) { $djangoEnv["JWT_SECRET_KEY"] = $jwtSecret }
if ($dotEnv.ContainsKey("JWT_ALGORITHM")) { $djangoEnv["JWT_ALGORITHM"] = $dotEnv["JWT_ALGORITHM"] }
if (-not $djangoEnv.ContainsKey("SECRET_KEY") -and $jwtSecret) { $djangoEnv["SECRET_KEY"] = $jwtSecret }
New-LauncherScript -Path $djangoLauncher -WorkingDirectory $djangoDir -EnvVars $djangoEnv -CommandLine $djangoCmd -LogFile $djangoLog
$pids.django = (Start-LauncherProcess -LauncherPath $djangoLauncher).Id
Write-ColorOutput "  [OK] Django PID=$($pids.django)" "Green"

Write-ColorOutput "[Step 7/7] Start React :$ReactPort ..." "Yellow"
Kill-ProcessOnPort -Port $ReactPort | Out-Null
Kill-ProcessOnPort -Port 3002 | Out-Null
$reactLog = Join-Path $logDir "react.log"
$reactDir = Join-Path $ProjectRoot "web\react_frontend"
$reactLauncher = Join-Path $logDir "react-launcher.ps1"
if ($DevMode) {
    $viteCache = Join-Path $reactDir "node_modules\.vite"
    if (Test-Path $viteCache) {
        Remove-Item $viteCache -Recurse -Force -ErrorAction SilentlyContinue
        Write-ColorOutput '  Cleared Vite dep cache (dev mode)' "Gray"
    }
    $reactCmd = "npm run dev -- --host 0.0.0.0 --port $ReactPort --strictPort"
    Write-ColorOutput '  Mode: Vite dev (pass -DevMode explicitly)' "Gray"
} else {
    Write-ColorOutput '  Building React (preview mode, may take 1-3 min)...' "Gray"
    Push-Location $reactDir
    $env:VITE_DJANGO_BACKEND_URL = "http://localhost:$DjangoPort"
    npm run build 2>&1 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-ColorOutput "  [ERROR] npm run build failed. See output above." "Red"
        exit 1
    }
    Pop-Location
    $reactCmd = "npm run preview -- --host 0.0.0.0 --port $ReactPort --strictPort"
    Write-ColorOutput '  Mode: Vite preview (production build, stable UI)' "Gray"
}
New-LauncherScript -Path $reactLauncher -WorkingDirectory $reactDir -EnvVars @{
    VITE_DJANGO_BACKEND_URL = "http://localhost:$DjangoPort"
} -CommandLine $reactCmd -LogFile $reactLog
$pids.react = (Start-LauncherProcess -LauncherPath $reactLauncher).Id
Write-ColorOutput "  [OK] React PID=$($pids.react)" "Green"

$pids | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8

Write-ColorOutput 'Waiting for services (parallel, up to 300s)...' "Yellow"
$ready = Wait-ServicesReady -ReactPort $ReactPort -DjangoPort $DjangoPort -FastAPIPort $FastAPIPort -TimeoutSec 300
$reactOk = $ready.React
$bffOk = $ready.Bff
$apiOk = $ready.Api

if (-not $reactOk) {
    Write-ColorOutput "  [ERROR] React not ready. Check $reactLog" "Red"
    Write-ColorOutput "  Hint: use -DevMode for faster Vite dev server" "Yellow"
    exit 1
}
if (-not $apiOk) {
    Write-ColorOutput "  [ERROR] FastAPI not ready. Check $logDir\fastapi.log" "Red"
    Write-ColorOutput "  Hint: look for ImportError / PydanticUserError at startup" "Yellow"
    exit 1
}
if (-not $bffOk) {
    Write-ColorOutput "  [ERROR] Django BFF not ready. Check $logDir\django.log" "Red"
    exit 1
}

Write-ColorOutput "" "Cyan"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  Test Environment Ready" "Green"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  React:   http://localhost:$ReactPort/chat  $(if ($reactOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  Django:  http://localhost:$DjangoPort/api/health/  $(if ($bffOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  FastAPI: http://localhost:$FastAPIPort/health  $(if ($apiOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput ("  Celery:  see {0}\celery.log - required for firewall/backup/patrol Skills" -f $logDir) "Gray"
if ($dockerOk -and -not $SkipLangfuse) {
    Write-ColorOutput '  Langfuse: http://localhost:3001  (admin@netops.local / netops-langfuse-admin)' "Gray"
}
Write-ColorOutput "  Logs:    $logDir" "Gray"
Write-ColorOutput "" "Cyan"
Write-ColorOutput 'Tip: scripts\test\stop.ps1 to stop (add -KeepMiddleware / -KeepLangfuse to keep Docker stacks)' "Yellow"
Start-Process "http://localhost:$ReactPort/chat"

if (-not $bffOk -or -not $apiOk) {
    Write-ColorOutput "  [WARN] Some backend health checks pending; see logs" "Yellow"
}

Write-ColorOutput "Running verify.ps1..." "Yellow"
& (Join-Path $PSScriptRoot "verify.ps1") -ProjectRoot $ProjectRoot -FastAPIPort $FastAPIPort -DjangoPort $DjangoPort -ReactPort $ReactPort
if ($LASTEXITCODE -ne 0) { exit 1 }
