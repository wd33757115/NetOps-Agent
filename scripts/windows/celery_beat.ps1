# Single-service Beat entry point, also used by the generated launcher.
param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_port_utils.ps1')
Initialize-WindowsRuntimeEnvironment -ProjectRoot $ProjectRoot
Set-Location -LiteralPath $ProjectRoot
Clear-StaleProjectBeatPid -ProjectRoot $ProjectRoot
$runtimeDir = Join-Path $ProjectRoot '.runtime\test'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$python = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
$beatPidFile = Join-Path $runtimeDir 'celery-beat.pid'
$beatSchedule = Join-Path $runtimeDir 'celery-beat-schedule'
# Windows PowerShell 5 wraps native stderr as error records when the launcher
# redirects streams. Celery writes normal startup logs there; only its exit
# code should decide whether startup failed. Keep preparation failures strict.
$ErrorActionPreference = 'Continue'
& $python -m celery -A src.core.celery_tasks.celery_app beat --loglevel=info --pidfile="$beatPidFile" --schedule="$beatSchedule"
exit $LASTEXITCODE
