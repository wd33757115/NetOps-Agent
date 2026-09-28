# Back up persistent Windows deployment data. Stop app writers before running.
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$OutDir = '',
    [switch]$WithLangfuse
)
$ErrorActionPreference = 'Stop'
if (-not $OutDir) { $OutDir = Join-Path $ProjectRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$python = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'backup.py') --output $OutDir --langfuse ([int]$WithLangfuse.IsPresent)
if ($LASTEXITCODE -ne 0) { throw 'Backup incomplete. Inspect the error before using these files.' }
Write-Host "[OK] Backup: $OutDir"
