# Compatibility entry point. Windows deployment lives in scripts/windows.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$SkipDocker,
    [switch]$SkipNpm
)
& (Join-Path $PSScriptRoot "..\windows\install.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
