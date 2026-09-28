# Compatibility entry point.
param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
& (Join-Path $PSScriptRoot "..\windows\celery_beat.ps1") @PSBoundParameters
if (-not $?) { exit 1 }
