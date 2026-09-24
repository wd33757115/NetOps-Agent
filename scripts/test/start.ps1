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
    [switch]$DevMode,
    [string[]]$EnableApps = @(),
    [switch]$CheckOnly,
    [switch]$NoBrowser
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
        $vars[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
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
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$LauncherPath`""
    ) -PassThru -WindowStyle Hidden
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
$env:PYTHONUTF8 = "1"

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-ColorOutput "  [ERROR] venv not found. Run scripts\test\install.ps1" "Red"
    exit 1
}

$dotEnv = Import-DotEnvFile -Path (Join-Path $ProjectRoot ".env")
if ($DevMode -and $EnableApps.Count) {
    throw "App enablement requires a production build; omit -DevMode."
}
# All local processes and management commands must share authority and transport.
$env:NETOPS_AUTHORITY_URL = "http://localhost:$DjangoPort"
$env:FASTAPI_BASE_URL = "http://localhost:$FastAPIPort"
$env:POSTGRES_HOST = "localhost"
$env:REDIS_HOST = "localhost"
foreach ($key in @("NETOPS_APP_INTERNAL_SECRET", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")) {
    if (-not [Environment]::GetEnvironmentVariable($key) -and $dotEnv.ContainsKey($key)) {
        [Environment]::SetEnvironmentVariable($key, $dotEnv[$key], "Process")
    }
}
if (-not $env:CELERY_BROKER_URL) { $env:CELERY_BROKER_URL = "amqp://guest:guest@localhost:5672//" }
if (-not $env:CELERY_RESULT_BACKEND) { $env:CELERY_RESULT_BACKEND = "redis://localhost:6379/0" }
if (-not $env:NETOPS_APP_INTERNAL_SECRET -or $env:NETOPS_APP_INTERNAL_SECRET.Length -lt 32) {
    throw "Configure NETOPS_APP_INTERNAL_SECRET (at least 32 characters) in .env before starting Apps."
}

Write-ColorOutput 'Check App dependencies before stopping services (including DSH SDK/runtime)...' "Yellow"
& $venvPython -m src.platform.kernel.cli check-dependencies
if ($LASTEXITCODE -ne 0) {
    throw "App dependencies are missing or mismatched. Run scripts\test\install.ps1 -SkipDocker -SkipNpm, then retry."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue) -or
    -not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'web\react_frontend\node_modules\vite\package.json'))) {
    throw "Frontend dependencies are unavailable. Run scripts\test\install.ps1 -SkipDocker, then retry."
}
if ($CheckOnly) {
    Write-ColorOutput '  [OK] Startup checks passed. No services were stopped or started.' "Green"
    return
}

Write-ColorOutput "[Step 0/7] Stop old processes and free ports..." "Yellow"
Stop-ProjectAppProcesses -ProjectRoot $ProjectRoot
$dockerOk = Test-DockerAvailable -TimeoutSec 5 -Quiet
if ($dockerOk) {
    $dockerOk = [bool](Stop-DockerAppContainers -SkipAvailabilityCheck)
    if (-not $dockerOk) {
        Write-ColorOutput "  [WARN] Docker stopped responding during app-container cleanup" "Yellow"
    }
} else {
    Write-ColorOutput "  [WARN] Docker is not responding; skipped old app-container cleanup" "Yellow"
}
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -Raw | ConvertFrom-Json
    Stop-RecordedProjectLaunchers -Records $old -ProjectRoot $ProjectRoot
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
# Forced Windows stops leave PID files behind. Never remove a live scheduler's lock.
Clear-StaleProjectBeatPid -ProjectRoot $ProjectRoot
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

Write-ColorOutput "[Step 1/7] Check Docker..." "Yellow"
if ($SkipMiddleware -and $SkipLangfuse) {
    Write-ColorOutput '  [SKIP] SkipMiddleware + SkipLangfuse (no Docker stacks)' "Gray"
} elseif ($dockerOk) {
    Write-ColorOutput "  [OK] Docker is available" "Green"
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
        if (-not (Ensure-DockerReady -ComposeFile $middlewareCompose -SkipAvailabilityCheck)) {
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
        $middleware = @("netops-postgres", "netops-redis", "netops-rabbitmq", "netops-minio")
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
$migrationExit = $LASTEXITCODE
Pop-Location
if ($migrationExit -ne 0) { throw "Django migration failed; see $migrateLog" }
Write-ColorOutput '  [OK] migrate done (log: django-migrate.log)' "Green"
& $venvPython web/django_backend/manage.py app_packages prepare
if ($LASTEXITCODE -ne 0) { throw "App preparation failed; resolve the reported package before starting." }

$pids = @{}
$py = Escape-SingleQuotedPath $venvPython

$jwtSecret = if ($dotEnv.ContainsKey("JWT_SECRET_KEY")) { $dotEnv["JWT_SECRET_KEY"] } elseif ($dotEnv.ContainsKey("SECRET_KEY")) { $dotEnv["SECRET_KEY"] } else { "" }

$fastapiEnv = @{
    DEBUG = "true"
    ENFORCE_BFF_ORIGIN = "false"
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"
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

Write-ColorOutput '[Step 4/7] Start Celery Core/AI Workers + Beat (Windows) ...' "Yellow"
$celeryEnv = @{
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"
    MINIO_ENDPOINT = "localhost:9000"
    CELERY_BROKER_URL = $env:CELERY_BROKER_URL
    CELERY_RESULT_BACKEND = $env:CELERY_RESULT_BACKEND
}
foreach ($k in @("DEEPSEEK_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PORT")) {
    if ($dotEnv.ContainsKey($k)) { $celeryEnv[$k] = $dotEnv[$k] }
}
if ($jwtSecret) { $celeryEnv["JWT_SECRET_KEY"] = $jwtSecret }
foreach ($k in @("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")) {
    if ($dotEnv.ContainsKey($k)) { $celeryEnv[$k] = $dotEnv[$k] }
}
$coreCeleryEnv = $celeryEnv.Clone()
$coreCeleryEnv["CELERY_WORKER_ROLE"] = "core"
$coreCeleryEnv["CELERY_WORKER_QUEUES"] = "netops.default,netops.firewall,netops.device,netops.health,netops.patrol"
$coreCeleryLog = Join-Path $logDir "celery-core.log"
$coreCeleryLauncher = Join-Path $logDir "celery-core-launcher.ps1"
$coreCeleryCmd = "& '$py' -m celery -A src.core.celery_tasks.celery_app worker --loglevel=info -P solo --concurrency=1 --hostname=netops-core@%h -Q netops.default,netops.firewall,netops.device,netops.health,netops.patrol"
New-LauncherScript -Path $coreCeleryLauncher -WorkingDirectory $ProjectRoot -EnvVars $coreCeleryEnv -CommandLine $coreCeleryCmd -LogFile $coreCeleryLog -PreCommands @(
    'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
)
$pids.celeryCore = (Start-LauncherProcess -LauncherPath $coreCeleryLauncher).Id
Write-ColorOutput ("  [OK] Core Worker PID={0} (log: celery-core.log)" -f $pids.celeryCore) "Green"

$aiCeleryEnv = $celeryEnv.Clone()
$aiCeleryEnv["CELERY_WORKER_ROLE"] = "ai"
$aiCeleryEnv["CELERY_WORKER_QUEUES"] = "netops.ai"
$aiCeleryLog = Join-Path $logDir "celery-ai.log"
$aiCeleryLauncher = Join-Path $logDir "celery-ai-launcher.ps1"
$aiCeleryCmd = "& '$py' -m src.platform.kernel.worker --loglevel=info -P solo --concurrency=1 --hostname=netops-ai@%h --queues netops.ai --exclude-queues netops.patrol"
New-LauncherScript -Path $aiCeleryLauncher -WorkingDirectory $ProjectRoot -EnvVars $aiCeleryEnv -CommandLine $aiCeleryCmd -LogFile $aiCeleryLog -PreCommands @(
    'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
)
$pids.celeryAi = (Start-LauncherProcess -LauncherPath $aiCeleryLauncher).Id
Write-ColorOutput ("  [OK] AI Worker PID={0} (log: celery-ai.log)" -f $pids.celeryAi) "Green"
Write-ColorOutput "  [TIP] Long LLM calls are isolated from patrol, health and report queues" "Gray"
Write-ColorOutput "        Debug: scripts\test\celery_worker.ps1" "Gray"
$celeryBeatLog = Join-Path $logDir "celery-beat.log"
$celeryBeatLauncher = Join-Path $logDir "celery-beat-launcher.ps1"
$celeryBeatEntry = Escape-SingleQuotedPath (Join-Path $ProjectRoot 'scripts\test\celery_beat.ps1')
$celeryBeatCmd = "& '$celeryBeatEntry' -ProjectRoot '$(Escape-SingleQuotedPath $ProjectRoot)'"
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
Write-ColorOutput "  [OK] FastAPI PID=$($pids.fastapi)" "Green"

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
    $viteCache = [IO.Path]::GetFullPath((Join-Path $reactDir "node_modules\.vite"))
    $workspacePrefix = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    if (-not $viteCache.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Vite cache path escapes the project"
    }
    if (Test-Path $viteCache) {
        Remove-Item -LiteralPath $viteCache -Recurse -Force -ErrorAction SilentlyContinue
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

if (-not $DevMode) {
    Write-ColorOutput 'Verify enabled App deployments (Gateway / frontend / Workers / Beat)...' "Yellow"
    $appProbe = @("--gateway-url", "http://localhost:$FastAPIPort", "--frontend-url", "http://localhost:$ReactPort", "--wait-seconds", "60")
    foreach ($appId in $EnableApps) {
        & $venvPython web/django_backend/manage.py app_packages enable $appId @appProbe
        if ($LASTEXITCODE -ne 0) { throw "App enablement failed: $appId" }
    }
} else {
    Write-ColorOutput '  [DEV] App deployment verification requires the default production preview.' "Yellow"
}
& $venvPython web/django_backend/manage.py app_packages status --compact

Write-ColorOutput "Running verify.ps1..." "Yellow"
& (Join-Path $PSScriptRoot "verify.ps1") -ProjectRoot $ProjectRoot -FastAPIPort $FastAPIPort -DjangoPort $DjangoPort -ReactPort $ReactPort -DevMode:$DevMode
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-ColorOutput "" "Cyan"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  Test Environment Ready" "Green"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  React:   http://localhost:$ReactPort/chat  $(if ($reactOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  Django:  http://localhost:$DjangoPort/api/health/  $(if ($bffOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  FastAPI: http://localhost:$FastAPIPort/health  $(if ($apiOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  Materials: http://localhost:$ReactPort/apps/materials (publish shared sources; App grant required)" "Gray"
Write-ColorOutput "  Assistant: http://localhost:$ReactPort/chat (DSH runs inside Gateway; App grant required)" "Gray"
Write-ColorOutput '  First enablement: add -EnableApps materials,assistant; existing enablement and grants are preserved.' "Gray"
Write-ColorOutput "  Celery:  celery-core.log (patrol), celery-ai.log (discovered App queues), celery-beat.log" "Gray"
if ($dockerOk -and -not $SkipLangfuse) {
    Write-ColorOutput '  Langfuse: http://localhost:3001  (admin@netops.local / netops-langfuse-admin)' "Gray"
}
Write-ColorOutput "  Logs:    $logDir" "Gray"
Write-ColorOutput "" "Cyan"
Write-ColorOutput 'Tip: scripts\test\stop.ps1 to stop (add -KeepMiddleware / -KeepLangfuse to keep Docker stacks)' "Yellow"
if (-not $NoBrowser) { Start-Process "http://localhost:$ReactPort/" }

if (-not $bffOk -or -not $apiOk) {
    Write-ColorOutput "  [WARN] Some backend health checks pending; see logs" "Yellow"
}
