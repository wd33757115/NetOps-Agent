# Compatibility entry point. Windows deployment lives in scripts/windows.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$KeepMiddleware,
    [switch]$KeepLangfuse
)
& (Join-Path $PSScriptRoot "..\windows\stop.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
