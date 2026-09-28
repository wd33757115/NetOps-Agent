# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Validate PowerShell script syntax under scripts/ (test + prod)

param(

    [string]$ScriptsRoot = $PSScriptRoot

)

$ErrorActionPreference = "Stop"

$ScriptsRoot = (Resolve-Path $ScriptsRoot).Path

$files = @(

    Get-ChildItem -Path (Join-Path $ScriptsRoot "windows") -Filter "*.ps1" -ErrorAction SilentlyContinue

    Get-ChildItem -Path (Join-Path $ScriptsRoot "test") -Filter "*.ps1" -ErrorAction SilentlyContinue

    Get-ChildItem -Path (Join-Path $ScriptsRoot "prod") -Filter "*.ps1" -ErrorAction SilentlyContinue

    Get-Item -Path (Join-Path $ScriptsRoot "validate_ps1.ps1") -ErrorAction SilentlyContinue

) | Sort-Object FullName -Unique

$failed = @()

Write-Host "Validate PS1 syntax: $ScriptsRoot" -ForegroundColor Cyan

foreach ($file in $files) {

    $tokens = $null

    $errors = $null

    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)

    if ($errors -and $errors.Count -gt 0) {

        $failed += $file.FullName

        Write-Host "  [FAIL] $($file.FullName)" -ForegroundColor Red

        foreach ($err in $errors) {

            Write-Host "         $($err.Message)" -ForegroundColor Red

        }

    } else {

        Write-Host "  [OK]   $($file.FullName)" -ForegroundColor Green

    }

}

if ($failed.Count -gt 0) {

    Write-Host ""

    Write-Host "Failed: $($failed.Count) file(s)" -ForegroundColor Red

    exit 1

}

Write-Host ""

Write-Host "All $($files.Count) script(s) OK" -ForegroundColor Green

