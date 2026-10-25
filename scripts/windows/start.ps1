# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Test env: Docker middleware + Langfuse + local FastAPI / Django / React
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [int]$FastAPIPort = 8000,
    [int]$DjangoPort = 8001,
    [int]$ReactPort = 3000,
    [ValidateRange(1, 12)][int]$PatrolWorkers = 6,
    [switch]$SkipMiddleware,
    [switch]$SkipLangfuse,
    [switch]$AllowNoDocker,
    [switch]$DevMode,
    [string[]]$EnableApps = @(),
    # Changed Apps to redeploy: disable + drain (running or stopped), bootstrap apply, enable.
    [string[]]$UpgradeApps = @(),
    # How long draining waits for in-flight work before finishing anyway.
    [ValidateRange(0, 3600)][int]$DrainGraceSeconds = 60,
    [switch]$CheckOnly,
    # Explicit administrative step: run bootstrap apply (Schema + package registration) first.
    [switch]$ApplyDatabase,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_port_utils.ps1")

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

function Assert-PlatformDatabaseReady {
    $checkLog = Join-Path $logDir 'database-check.log'
    $checkOutput = & $venvPython -m src.platform.database.bootstrap check 2>&1
    $checkExitCode = $LASTEXITCODE
    $checkOutput | Out-File -LiteralPath $checkLog -Encoding utf8
    if ($checkExitCode -ne 0) {
        # Only platform faults land here; App problems are reported, never fatal.
        $checkOutput | ForEach-Object { Write-Host $_ }
        if (($checkOutput | Out-String) -match 'bootstrap apply') {
            Write-ColorOutput '  Pending platform migrations: rerun with -ApplyDatabase.' "Yellow"
        }
        throw "Platform database readiness check failed; existing app processes were left running. Resolve the cause above before retrying. Full log: $checkLog"
    }
    Show-AppIssues $checkOutput
}

function Get-AppState {
    param([string]$AppId)
    $line = & $venvPython web/django_backend/manage.py app_packages status --compact 2>$null |
        Where-Object { $_ -match "^$([regex]::Escape($AppId)) " } | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -split '\s+')[2]
}

function Invoke-AppDisableDrain {
    # Like a Pod's termination: close admission, give in-flight work a grace period,
    # then stop waiting. A stopped deployment has nothing in flight and drains at once.
    # Returns $false (with a warning) when this App cannot be drained; the platform
    # and the other Apps carry on either way.
    param([string]$AppId, [int]$GraceSeconds = $DrainGraceSeconds)
    & $venvPython web/django_backend/manage.py app_packages disable $AppId | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Add-AppWarning "$AppId not upgraded: disable failed (disable dependent Apps first or list them in -UpgradeApps)"
        return $false
    }
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while (($state = Get-AppState -AppId $AppId) -ne 'disabled') {
        if ((Get-Date) -gt $deadline) {
            Write-ColorOutput "  [WARN] $AppId grace period ($GraceSeconds s) expired; finishing the drain" "Yellow"
            & $venvPython web/django_backend/manage.py app_packages drain $AppId --force | Out-Host
            if ((Get-AppState -AppId $AppId) -ne 'disabled') {
                Add-AppWarning "$AppId not upgraded: drain failed (see manage.py app_packages status)"
                return $false
            }
            break
        }
        & $venvPython web/django_backend/manage.py app_packages drain $AppId | Out-Null
        if ((Get-AppState -AppId $AppId) -ne 'disabled') { Start-Sleep -Seconds 5 }
    }
    Write-ColorOutput "  [OK] $AppId disabled and drained" "Green"
    return $true
}

$appWarnings = [System.Collections.Generic.List[string]]::new()
function Add-AppWarning {
    param([string]$Message)
    if ($appWarnings.Contains($Message)) { return }
    $appWarnings.Add($Message)
    Write-ColorOutput "  [WARN] $Message" "Yellow"
}

function Show-AppIssues {
    # Bootstrap reports App-level problems as "[APP] id unavailable: reason" lines.
    param($Output)
    $Output | ForEach-Object { "$_" } | Where-Object { $_ -match '^\[APP\] ' } |
        ForEach-Object { Add-AppWarning ($_ -replace '^\[APP\] ', '') }
}

function Invoke-PlatformDatabaseApply {
    # bootstrap apply takes its own lock and still refuses changed Apps that are enabled.
    # Running rule-governance work holds the same advisory lock in shared mode, so
    # only that contention is retried; every other failure stops immediately.
    $applyLog = Join-Path $logDir 'database-apply.log'
    $deadline = (Get-Date).AddSeconds(120)
    while ($true) {
        $applyOutput = & $venvPython -m src.platform.database.bootstrap apply 2>&1
        $applyExitCode = $LASTEXITCODE
        $busy = ($applyOutput | Out-String) -match 'Database bootstrap is already running'
        if ($applyExitCode -eq 0 -or -not $busy -or (Get-Date) -gt $deadline) { break }
        Write-ColorOutput '  Bootstrap lock busy (running rule-governance work); retrying in 5 s...' "Gray"
        Start-Sleep -Seconds 5
    }
    $applyOutput | Out-File -LiteralPath $applyLog -Encoding utf8
    if ($applyExitCode -ne 0) {
        $applyOutput | ForEach-Object { Write-Host $_ }
        throw "Database bootstrap apply failed; existing app processes were left running. Full log: $applyLog"
    }
    Write-ColorOutput '  [OK] Database bootstrap apply complete' "Green"
    Show-AppIssues $applyOutput
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
Write-ColorOutput "  NetOps Windows - Start" "Cyan"
Write-ColorOutput '  (Middleware Docker + Local App Processes)' "Cyan"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  Project: $ProjectRoot" "Gray"
Write-ColorOutput "  FastAPI: $FastAPIPort | Django: $DjangoPort | React: $ReactPort" "Gray"
Write-ColorOutput "" "Cyan"

Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"

$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-ColorOutput "  [ERROR] venv not found. Run scripts\windows\install.ps1" "Red"
    exit 1
}

$dotEnv = Import-DotEnvFile -Path (Join-Path $ProjectRoot ".env")
if ($DevMode -and ($EnableApps.Count -or $UpgradeApps.Count)) {
    throw "App enablement requires a production build; omit -DevMode."
}
if ($CheckOnly -and ($ApplyDatabase -or $UpgradeApps.Count)) {
    throw "-CheckOnly is read-only; run -ApplyDatabase / -UpgradeApps without it."
}
# All local processes and management commands must share authority and transport.
$env:NETOPS_AUTHORITY_URL = "http://127.0.0.1:$DjangoPort"
$env:FASTAPI_BASE_URL = "http://127.0.0.1:$FastAPIPort"
Initialize-WindowsRuntimeEnvironment -ProjectRoot $ProjectRoot
if (-not $env:NETOPS_APP_INTERNAL_SECRET -or $env:NETOPS_APP_INTERNAL_SECRET.Length -lt 32) {
    throw "Configure NETOPS_APP_INTERNAL_SECRET (at least 32 characters) in .env before starting Apps."
}

Write-ColorOutput 'Check App dependencies before stopping services (including DSH SDK/runtime)...' "Yellow"
$dependencyReport = & $venvPython -m src.platform.kernel.cli check-dependencies
$dependencyReport | ForEach-Object { Write-Host $_ }
# An App with missing dependencies is not loaded; the platform and the rest start.
$dependencyReport | Where-Object { $_ -match '^\[ERROR\] ' } | ForEach-Object {
    Add-AppWarning (($_ -replace '^\[ERROR\] ', '') + ' (run scripts\windows\install.ps1 -SkipDocker -SkipNpm)')
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue) -or
    -not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'web\react_frontend\node_modules\vite\package.json'))) {
    throw "Frontend dependencies are unavailable. Run scripts\windows\install.ps1 -SkipDocker, then retry."
}
if (-not ($SkipMiddleware -and $SkipLangfuse) -and -not $AllowNoDocker) {
    & (Join-Path $PSScriptRoot 'containers.ps1') -ProjectRoot $ProjectRoot -Action check -SkipMiddleware:$SkipMiddleware -WithLangfuse:(-not $SkipLangfuse)
}
if ($CheckOnly) {
    Assert-PlatformDatabaseReady
    Write-ColorOutput '  [OK] Startup checks passed. No services were stopped or started.' "Green"
    return
}

$dockerOk = Test-DockerAvailable -TimeoutSec 5 -Quiet
Write-ColorOutput "[Step 0/7] Check Docker..." "Yellow"
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

Write-ColorOutput "[Step 1/7] Start Docker dependencies..." "Yellow"
if ($dockerOk) {
    & (Join-Path $PSScriptRoot 'containers.ps1') -ProjectRoot $ProjectRoot -Action up -SkipMiddleware:$SkipMiddleware -WithLangfuse:(-not $SkipLangfuse)
} else {
    Write-ColorOutput '  [SKIP] Docker dependencies' 'Gray'
}

Write-ColorOutput "[Step 2/7] Check platform database and App readiness before stopping services..." "Yellow"
# Dependants are listed after their dependencies, so disable in reverse order.
$drained = @()
for ($i = $UpgradeApps.Count - 1; $i -ge 0; $i--) {
    if (Invoke-AppDisableDrain -AppId $UpgradeApps[$i]) { $drained += $UpgradeApps[$i] }
}
# Only drained Apps are upgraded and enabled; the rest stay as they are.
$UpgradeApps = @($UpgradeApps | Where-Object { $drained -contains $_ })
if ($ApplyDatabase -or $UpgradeApps.Count) {
    Invoke-PlatformDatabaseApply
}
Assert-PlatformDatabaseReady

Write-ColorOutput "[Step 3/7] Stop old processes and free ports..." "Yellow"
Stop-ProjectAppProcesses -ProjectRoot $ProjectRoot
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

$pids = @{}
$py = Escape-SingleQuotedPath $venvPython

$jwtSecret = if ($dotEnv.ContainsKey("JWT_SECRET_KEY")) { $dotEnv["JWT_SECRET_KEY"] } elseif ($dotEnv.ContainsKey("SECRET_KEY")) { $dotEnv["SECRET_KEY"] } else { "" }

$fastapiEnv = @{
    DEBUG = $DevMode.IsPresent.ToString().ToLowerInvariant()
    ENFORCE_BFF_ORIGIN = "false"
    POSTGRES_HOST = "127.0.0.1"
    REDIS_HOST = "127.0.0.1"
    MINIO_ENDPOINT = "127.0.0.1:9000"
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
    POSTGRES_HOST = "127.0.0.1"
    REDIS_HOST = "127.0.0.1"
    MINIO_ENDPOINT = "127.0.0.1:9000"
    # Two advisory-lock connections plus business queries fit within five.
    POSTGRES_POOL_SIZE = "3"
    POSTGRES_MAX_OVERFLOW = "2"
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
$coreCeleryEnv["CELERY_WORKER_QUEUES"] = "netops.default,netops.firewall,netops.device,netops.health"
$coreCeleryLog = Join-Path $logDir "celery-core.log"
$coreCeleryLauncher = Join-Path $logDir "celery-core-launcher.ps1"
$coreCeleryCmd = "& '$py' -m celery -A src.core.celery_tasks.celery_app worker --loglevel=info -P solo --concurrency=1 --hostname=netops-core@%h -Q netops.default,netops.firewall,netops.device,netops.health"
New-LauncherScript -Path $coreCeleryLauncher -WorkingDirectory $ProjectRoot -EnvVars $coreCeleryEnv -CommandLine $coreCeleryCmd -LogFile $coreCeleryLog -PreCommands @(
    'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
)
$pids.celeryCore = (Start-LauncherProcess -LauncherPath $coreCeleryLauncher).Id
Write-ColorOutput ("  [OK] Core Worker PID={0} (log: celery-core.log)" -f $pids.celeryCore) "Green"

# Windows solo executes one task per process. Independent patrol workers provide
# concurrency without prefork (unsupported on Windows) or the Python GIL.
for ($workerIndex = 1; $workerIndex -le $PatrolWorkers; $workerIndex++) {
    $patrolEnv = $celeryEnv.Clone()
    $patrolEnv["CELERY_WORKER_ROLE"] = "core"
    $patrolEnv["CELERY_WORKER_QUEUES"] = "netops.patrol"
    $patrolLauncher = Join-Path $logDir "celery-patrol-$workerIndex-launcher.ps1"
    $patrolLog = Join-Path $logDir "celery-patrol-$workerIndex.log"
    $patrolCmd = "& '$py' -m celery -A src.core.celery_tasks.celery_app worker --loglevel=info -P solo --concurrency=1 --hostname=netops-patrol-$workerIndex@%h -Q netops.patrol"
    New-LauncherScript -Path $patrolLauncher -WorkingDirectory $ProjectRoot -EnvVars $patrolEnv -CommandLine $patrolCmd -LogFile $patrolLog -PreCommands @(
        'Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue'
    )
    $pids["celeryPatrol$workerIndex"] = (Start-LauncherProcess -LauncherPath $patrolLauncher).Id
}
Write-ColorOutput "  [OK] Started $PatrolWorkers independent patrol workers (celery-patrol-*.log)" "Green"

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
Write-ColorOutput "        Debug: scripts\windows\celery_worker.ps1" "Gray"
$celeryBeatLog = Join-Path $logDir "celery-beat.log"
$celeryBeatLauncher = Join-Path $logDir "celery-beat-launcher.ps1"
$celeryBeatEntry = Escape-SingleQuotedPath (Join-Path $ProjectRoot 'scripts\windows\celery_beat.ps1')
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
    DJANGO_DEBUG = $DevMode.IsPresent.ToString().ToLowerInvariant()
    FASTAPI_BASE_URL = "http://127.0.0.1:$FastAPIPort"
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
    $env:VITE_DJANGO_BACKEND_URL = "http://127.0.0.1:$DjangoPort"
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
    VITE_DJANGO_BACKEND_URL = "http://127.0.0.1:$DjangoPort"
    # Browser uploads reach MinIO through the workbench port; same address the gateway signs for.
    VITE_OBJECT_STORAGE_URL = "http://$($fastapiEnv.MINIO_ENDPOINT)"
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
    $appProbe = @("--gateway-url", "http://127.0.0.1:$FastAPIPort", "--frontend-url", "http://localhost:$ReactPort", "--wait-seconds", "60")
    foreach ($appId in @(@($UpgradeApps) + @($EnableApps) | Select-Object -Unique)) {
        & $venvPython web/django_backend/manage.py app_packages enable $appId @appProbe
        if ($LASTEXITCODE -ne 0) { Add-AppWarning "$appId not enabled (see manage.py app_packages status)" }
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
Write-ColorOutput "  Windows Environment Ready" "Green"
Write-ColorOutput "============================================================" "Cyan"
Write-ColorOutput "  React:   http://localhost:$ReactPort/chat  $(if ($reactOk) { '[OK]' } else { '[WAIT]' })" "Gray"
# Other computers use the same port: pages, API, WebSocket and file uploads all go through it.
$lanAddresses = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
    ForEach-Object { $_.IPv4Address.IPAddress } | Where-Object { $_ -notlike '169.254.*' })
foreach ($address in $lanAddresses) {
    Write-ColorOutput "  Other computers: http://${address}:$ReactPort/  (allow inbound TCP $ReactPort in Windows Firewall)" "Gray"
}
Write-ColorOutput "  Django:  http://127.0.0.1:$DjangoPort/api/health/  $(if ($bffOk) { '[OK]' } else { '[WAIT]' })" "Gray"
if ($appWarnings.Count) {
    # The platform is up; these Apps stay closed until their problem is resolved.
    Write-ColorOutput "  Apps not available:" "Yellow"
    $appWarnings | ForEach-Object { Write-ColorOutput "    - $_" "Yellow" }
}
Write-ColorOutput "  FastAPI: http://127.0.0.1:$FastAPIPort/health  $(if ($apiOk) { '[OK]' } else { '[WAIT]' })" "Gray"
Write-ColorOutput "  Materials: http://localhost:$ReactPort/apps/materials (publish shared sources; App grant required)" "Gray"
Write-ColorOutput "  Assistant: http://localhost:$ReactPort/chat (DSH runs inside Gateway; App grant required)" "Gray"
Write-ColorOutput '  First enablement: add -EnableApps materials,assistant; existing enablement and grants are preserved.' "Gray"
Write-ColorOutput "  Celery:  celery-patrol-*.log (patrol), celery-core.log (control), celery-ai.log, celery-beat.log" "Gray"
if ($dockerOk -and -not $SkipLangfuse) {
    Write-ColorOutput '  Langfuse: http://localhost:3001' "Gray"
}
Write-ColorOutput "  Logs:    $logDir" "Gray"
Write-ColorOutput "" "Cyan"
Write-ColorOutput 'Tip: scripts\windows\stop.ps1 to stop (add -KeepMiddleware / -KeepLangfuse to keep Docker stacks)' "Yellow"
if (-not $NoBrowser) { Start-Process "http://localhost:$ReactPort/" }

if (-not $bffOk -or -not $apiOk) {
    Write-ColorOutput "  [WARN] Some backend health checks pending; see logs" "Yellow"
}
