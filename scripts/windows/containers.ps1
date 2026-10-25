# All Windows container operations use this one Compose file and stable volumes.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('check', 'pull', 'up', 'stop', 'status')][string]$Action = 'up',
    [switch]$SkipMiddleware,
    [switch]$WithLangfuse
)
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) '_docker_utils.ps1')
$composeFile = Join-Path $ProjectRoot 'deployment\compose.windows.yml'
$envFile = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Run scripts\windows\install.ps1 to create .env first.' }
$services = @()
if (-not $SkipMiddleware) { $services += @('postgres', 'redis', 'rabbitmq', 'minio') }
if ($WithLangfuse) { $services += @('langfuse-db', 'langfuse') }
if (-not $services.Count) { return }
$prefix = @('compose', '--project-name', 'deployment', '--env-file', $envFile, '-f', $composeFile)
if ($WithLangfuse) { $prefix += @('--profile', 'observability') }
function Invoke-CheckedDocker {
    param([string[]]$DockerArgs, [int]$TimeoutSec = 60, [switch]$ShowOutput)
    $result = Invoke-DockerCommand -Arguments $DockerArgs -TimeoutSec $TimeoutSec -ShowOutput:$ShowOutput
    if (-not $result.Succeeded) { throw "Docker operation failed (exit=$($result.ExitCode), timeout=$($result.TimedOut)). $($result.StdErr)" }
    return $result
}
$engine = Invoke-CheckedDocker -DockerArgs @('info', '--format', '{{.OSType}}')
if ($engine.StdOut.Trim() -ne 'linux') { throw 'Docker Desktop must use Linux containers (WSL 2).' }
$null = Invoke-CheckedDocker -DockerArgs ($prefix + @('config', '--quiet'))
if ($Action -eq 'check') {
    Write-Host '[OK] Docker engine and Windows Compose configuration.'
    return
}
if ($Action -eq 'up' -and $WithLangfuse) {
    # Previous versions used a separate project. Recreate only these two known
    # containers, preserving the verified named database volume. Never prune.
    $legacy = @()
    foreach ($name in @('netops-langfuse-standalone', 'netops-langfuse-db')) {
        $inspection = Invoke-DockerCommand -Arguments @('inspect', '--format', '{{json .}}', $name) -TimeoutSec 15 -Quiet
        if (-not $inspection.Succeeded) {
            if ($inspection.StdErr -notmatch 'No such (object|container)') { throw "Cannot inspect $name" }
            continue
        }
        $container = $inspection.StdOut | ConvertFrom-Json
        $owner = $container.Config.Labels.'com.docker.compose.project'
        if ($owner -eq 'deployment') { continue }
        if ($owner -ne 'langfuse') { throw "Container $name belongs to an unexpected project; left unchanged." }
        if ($name -eq 'netops-langfuse-db') {
            $mount = @($container.Mounts | Where-Object { $_.Destination -eq '/var/lib/postgresql/data' })
            if ($mount.Count -ne 1 -or $mount[0].Name -ne 'langfuse_langfuse_pg_data') {
                throw 'Legacy Langfuse database volume is unexpected; left unchanged.'
            }
        }
        $legacy += $name
    }
    if ($legacy.Count) {
        # Ensure images are available before interrupting the old observability UI.
        $null = Invoke-CheckedDocker -DockerArgs ($prefix + @('pull', 'langfuse-db', 'langfuse')) -TimeoutSec 600 -ShowOutput
        $null = Invoke-CheckedDocker -DockerArgs (@('stop') + $legacy) -TimeoutSec 60
        $null = Invoke-CheckedDocker -DockerArgs (@('rm') + $legacy) -TimeoutSec 30
        Write-Host '[OK] Adopted legacy Langfuse containers; database volume preserved.'
    }
}
switch ($Action) {
    'pull' { $operation = @('pull'); $timeout = 900 }
    'up' { $operation = @('up', '-d', '--wait', '--wait-timeout', '180'); $timeout = 240 }
    'stop' { $operation = @('stop'); $timeout = 90 }
    'status' { $operation = @('ps', '-a'); $timeout = 30 }
}
$null = Invoke-CheckedDocker -DockerArgs ($prefix + $operation + $services) -TimeoutSec $timeout -ShowOutput
if ($Action -eq 'up' -and $WithLangfuse) {
    $ready = $false
    $deadline = (Get-Date).AddSeconds(90)
    do {
        try {
            $response = Invoke-WebRequest 'http://localhost:3001/api/public/health' -UseBasicParsing -TimeoutSec 5
            $ready = $response.StatusCode -eq 200
        } catch { }
        if (-not $ready) { Start-Sleep -Seconds 2 }
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready) { throw 'Langfuse health check failed; inspect docker logs netops-langfuse-standalone.' }
}
Write-Host "[OK] Containers: $Action ($($services -join ', '))."
