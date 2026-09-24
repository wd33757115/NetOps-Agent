$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\scripts\test\_port_utils.ps1')
$project = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path ('.runtime\beat-pid-tests\' + [guid]::NewGuid().ToString('N'))
$runtime = Join-Path $project '.runtime\test'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$marker = Join-Path $runtime 'celery-beat.pid'
$script:beatProcesses = @()
$script:pidOwners = @()
$script:queryFails = $false
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($script:queryFails) { throw 'Process inspection unavailable' }
    if ($Filter -like 'ProcessId=*') { return $script:pidOwners }
    return $script:beatProcesses
}
function Assert-Refused([string]$Expected) {
    $before = if (Test-Path -LiteralPath $marker) { [IO.File]::ReadAllText($marker) } else { $null }
    $message = ''
    try { Clear-StaleProjectBeatPid -ProjectRoot $project } catch { $message = $_.Exception.Message }
    if ($message -notlike "*$Expected*") { throw "Unexpected refusal: $message" }
    $after = if (Test-Path -LiteralPath $marker) { [IO.File]::ReadAllText($marker) } else { $null }
    if ($before -cne $after) { throw 'PID file changed on a refused operation' }
}
Clear-StaleProjectBeatPid -ProjectRoot $project # Missing PID is safe.
[IO.File]::WriteAllText($marker, "12345`n")
Clear-StaleProjectBeatPid -ProjectRoot $project # Dead PID is removed.
if (Test-Path -LiteralPath $marker) { throw 'Stale PID was not removed' }
[IO.File]::WriteAllText($marker, "12345`n")
$script:pidOwners = @([pscustomobject]@{ ProcessId=12345; Name='unrelated.exe' })
Assert-Refused 'live PID' # PID reuse does not allow killing/removing another owner's lock.
$script:pidOwners = @()
$script:beatProcesses = @([pscustomobject]@{
    Name='python.exe'; ProcessId=67890
    CommandLine=('"' + (Join-Path $project 'venv\Scripts\python.exe') + '" -m celery -A src.core.celery_tasks.celery_app beat')
})
Assert-Refused 'still running' # Actual Beat wins even when the marker is stale.
Remove-Item -LiteralPath $marker
Assert-Refused 'still running' # Missing PID must not permit a duplicate Beat either.
$script:beatProcesses = @()
[IO.File]::WriteAllText($marker, 'partial-pid')
Assert-Refused 'Invalid'
[IO.File]::WriteAllText($marker, "12345`n")
$script:queryFails = $true
Assert-Refused 'inspection unavailable'
$script:queryFails = $false
$script:beatProcesses = @([pscustomobject]@{
    Name='python.exe'; ProcessId=67890
    CommandLine=('"' + (Join-Path ($project + '-other') 'venv\Scripts\python.exe') + '" -m celery -A src.core.celery_tasks.celery_app beat')
})
Clear-StaleProjectBeatPid -ProjectRoot $project
if (Test-Path -LiteralPath $marker) { throw 'Unrelated project incorrectly blocked cleanup' }
Write-Output '8 Beat PID recovery/protection checks passed; no real processes stopped.'
