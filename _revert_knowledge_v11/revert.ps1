# Revert uncommitted Codex knowledge-v1.1 work in netops-agent.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File .\_revert_knowledge_v11\revert.ps1
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here
$Log  = Join-Path $Here ("revert-" + (Get-Date -Format yyyyMMdd-HHmmss) + ".log")
Start-Transcript -Path $Log | Out-Null
Set-Location $Repo

function Step($m) { Write-Host ""; Write-Host "==== $m ====" -ForegroundColor Cyan }

Step "1. Git state"
$branch = (git branch --show-current).Trim()
$head = (git rev-parse HEAD).Trim()
Write-Host "branch=$branch head=$head"
if ($branch -ne "codex/knowledge-v1.1") { throw "Expected branch codex/knowledge-v1.1, got $branch" }
if (-not $head.StartsWith("5d5f5056")) { throw "Unexpected HEAD $head (expected 5d5f505). Stop." }
$changes = git status --porcelain --untracked-files=all -- . ":(exclude)_revert_knowledge_v11"
Write-Host ("uncommitted entries: " + @($changes).Count)

Step "2. Database"
$container = "netops-postgres"
$running = (docker ps --filter "name=^/$container$" --format "{{.Names}}")
if ($running -ne $container) {
  Write-Host "Container $container is not running. Start it (docker compose -f deployment/docker-compose.yml up -d postgres) and re-run." -ForegroundColor Yellow
  throw "postgres container not running"
}
$ver = (docker exec $container psql -U netops -d netops_agent -tAc "SELECT version_num FROM public.alembic_version" | Out-String).Trim()
Write-Host "alembic_version=$ver"
$bff = (docker exec $container psql -U netops -d netops_agent -tAc "SELECT count(*) FROM django.django_migrations WHERE app='bff' AND name<>'0001_desktop_preference'" | Out-String).Trim()
Write-Host "extra bff migrations=$bff"
$knowTables = (docker exec $container psql -U netops -d netops_agent -tAc "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'knowledge\_%' OR tablename LIKE 'bff\_knowledge%'" | Out-String).Trim()
Write-Host "knowledge tables=$knowTables"

if ($ver -match '^01(0[2-9]|1[0-7])_' -or [int]$bff -gt 0 -or [int]$knowTables -gt 0) {
  Step "2a. Backup database"
  $dump = "/tmp/netops_before_knowledge_cleanup.dump"
  docker exec $container pg_dump -U netops -d netops_agent -Fc -f $dump
  if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
  $localDump = Join-Path $Here "netops_before_knowledge_cleanup.dump"
  docker cp "${container}:$dump" $localDump
  if ($LASTEXITCODE -ne 0) { throw "docker cp backup failed" }
  Write-Host ("backup saved: $localDump  (" + (Get-Item $localDump).Length + " bytes)")

  Step "2b. Cleanup SQL"
  docker cp (Join-Path $Here "knowledge_v11_db_cleanup.sql") "${container}:/tmp/knowledge_v11_db_cleanup.sql"
  docker exec $container psql -v ON_ERROR_STOP=1 -U netops -d netops_agent -f /tmp/knowledge_v11_db_cleanup.sql
  if ($LASTEXITCODE -ne 0) { throw "cleanup SQL failed (transaction rolled back, nothing changed)" }
  $ver2 = (docker exec $container psql -U netops -d netops_agent -tAc "SELECT version_num FROM public.alembic_version" | Out-String).Trim()
  Write-Host "alembic_version after=$ver2"
} else {
  Write-Host "No knowledge migrations found in DB; skip DB cleanup."
}

Step "3. Stash code changes (recoverable)"
$msg = "abandoned: codex knowledge-v1.1 " + (Get-Date -Format yyyyMMdd-HHmm)
git stash push --include-untracked -m $msg -- . ":(exclude)_revert_knowledge_v11"
if ($LASTEXITCODE -ne 0) { throw "git stash failed" }
git stash list | Select-Object -First 1

Step "4. Verify"
$left = git status --porcelain --untracked-files=all -- . ":(exclude)_revert_knowledge_v11"
if (@($left).Count -gt 0) { Write-Host "Remaining changes:" -ForegroundColor Yellow; $left } else { Write-Host "Working tree clean." -ForegroundColor Green }
git log --oneline -1

Write-Host ""
Write-Host "DONE. Log: $Log" -ForegroundColor Green
Stop-Transcript | Out-Null
