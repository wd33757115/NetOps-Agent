# Compatibility entry point. Windows deployment lives in scripts/windows.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [int]$FastAPIPort = 8000,
    [int]$DjangoPort = 8001,
    [int]$ReactPort = 3000,
    [switch]$DevMode
)
& (Join-Path $PSScriptRoot "..\windows\verify.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
