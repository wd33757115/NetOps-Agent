$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\scripts\windows\_port_utils.ps1')
$project = Join-Path $env:TEMP ('netops-env-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $project | Out-Null
try {
    @'
NETOPS_APP_INTERNAL_SECRET=test-secret
CELERY_BROKER_URL=amqp://user:password@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/1
'@ | Set-Content -LiteralPath (Join-Path $project '.env')
    foreach ($name in @('NETOPS_APP_INTERNAL_SECRET','CELERY_BROKER_URL','CELERY_RESULT_BACKEND')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    Initialize-WindowsRuntimeEnvironment -ProjectRoot $project
    if ($env:POSTGRES_HOST -ne '127.0.0.1') { throw 'Postgres must use IPv4' }
    if ($env:REDIS_HOST -ne '127.0.0.1') { throw 'Redis must use IPv4' }
    if ($env:MINIO_ENDPOINT -ne '127.0.0.1:9000') { throw 'MinIO must use IPv4' }
    if ($env:CELERY_BROKER_URL -ne 'amqp://user:password@127.0.0.1:5672//') { throw 'Broker credentials/path changed' }
    if ($env:CELERY_RESULT_BACKEND -ne 'redis://127.0.0.1:6379/1') { throw 'Result DB changed' }
    if ($env:PGCONNECT_TIMEOUT -ne '10') { throw 'Postgres connection must be bounded' }
    $env:CELERY_BROKER_URL = 'amqp://user:password@broker.internal:5672//'
    Initialize-WindowsRuntimeEnvironment -ProjectRoot $project
    if ($env:CELERY_BROKER_URL -ne 'amqp://user:password@broker.internal:5672//') { throw 'Explicit remote broker must be preserved' }
    Write-Host '7 Windows environment checks passed.'
} finally {
    # Delete only the known file and its empty, uniquely-created test directory.
    Remove-Item -LiteralPath (Join-Path $project '.env') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $project -ErrorAction SilentlyContinue
}
