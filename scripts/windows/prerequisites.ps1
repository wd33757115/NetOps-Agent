# Explicit, optional host prerequisite installation. Run in an elevated terminal.
param()
$ErrorActionPreference = 'Stop'
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'Install Microsoft App Installer (winget), then retry. See docs/deployment-windows.md.'
}
$packages = @(
    @{ Command = 'python'; Id = 'Python.Python.3.13' },
    @{ Command = 'node'; Id = 'OpenJS.NodeJS.LTS' },
    @{ Command = 'docker'; Id = 'Docker.DockerDesktop' }
)
foreach ($package in $packages) {
    if (Get-Command $package.Command -ErrorAction SilentlyContinue) {
        Write-Host "[KEEP] $($package.Command) is already installed."
        continue
    }
    & winget install --exact --id $package.Id --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installation failed: $($package.Id)" }
}
Write-Host 'Reopen PowerShell; launch Docker Desktop and select Linux containers (WSL 2). Restart Windows if the installer requests it.'
