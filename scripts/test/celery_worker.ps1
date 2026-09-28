# Compatibility entry point. Windows deployment lives in scripts/windows.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$LogLevel = "info",
    [string]$Queues = "netops.default,netops.firewall,netops.device,netops.health,netops.patrol,netops.ai",
    [string]$Pool = "solo",
    [string]$NodeName = "netops-standalone@%h",
    [int]$DjangoPort = 8001,
    [switch]$KeepExisting
)
& (Join-Path $PSScriptRoot "..\windows\celery_worker.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
