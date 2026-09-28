# Compatibility entry point. Windows deployment lives in scripts/windows.
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
    [switch]$CheckOnly,
    [switch]$NoBrowser
)
& (Join-Path $PSScriptRoot "..\windows\start.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
