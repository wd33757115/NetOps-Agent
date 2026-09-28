# Database administration only. start.ps1 performs read-only readiness checks.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('apply', 'check')][string]$Action = 'check',
    [switch]$AdoptExisting
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_port_utils.ps1')
Set-Location $ProjectRoot
Initialize-WindowsRuntimeEnvironment -ProjectRoot $ProjectRoot
$env:PYTHONUTF8 = '1'
$arguments = @('-m', 'src.platform.database.bootstrap', $Action)
if ($AdoptExisting) { $arguments += '--adopt-existing' }
& (Join-Path $ProjectRoot 'venv\Scripts\python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Platform database bootstrap failed. Resolve the error before starting services.' }
