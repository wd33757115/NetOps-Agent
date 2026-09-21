# Export the netops-agent git history as a single bundle file for Claude to work on.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here
Set-Location $Repo
$Log = Join-Path $Here "export.log"
Start-Transcript -Path $Log | Out-Null

# keep helper folders out of git status
$exclude = Join-Path $Repo ".git\info\exclude"
foreach ($p in @("_sync/", "_revert_knowledge_v11/")) {
  if (-not (Select-String -Path $exclude -SimpleMatch $p -Quiet -ErrorAction SilentlyContinue)) { Add-Content -Path $exclude -Value $p }
}

git status --short
$branch = (git branch --show-current).Trim()
Write-Host "branch=$branch head=$(git rev-parse HEAD)"
$bundle = Join-Path $Here "netops.bundle"
if (Test-Path $bundle) { Remove-Item $bundle }
git bundle create $bundle --branches --tags
if ($LASTEXITCODE -ne 0) { throw "git bundle failed" }
git bundle verify $bundle
Write-Host ("bundle: " + (Get-Item $bundle).Length + " bytes")
Write-Host "DONE" -ForegroundColor Green
Stop-Transcript | Out-Null
