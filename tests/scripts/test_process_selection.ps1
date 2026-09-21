$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\scripts\test\_port_utils.ps1')
$taskRoot = 'C:\NetOps Test\project'
$python = '"C:\NetOps Test\project\venv\Scripts\python.exe"'
$cases = @(
    @('python.exe', "$python -m uvicorn src.gateway.main:app --port 8000", $true),
    @('python.exe', "$python -m daphne -p 8001 django_backend.asgi:application", $true),
    @('python.exe', "$python -m celery -A src.core.celery_tasks.celery_app worker --loglevel=info", $true),
    @('python.exe', "$python -m celery -A src.core.celery_tasks.celery_app beat", $true),
    @('python.exe', "$python -m src.platform.kernel.worker --queues netops.ai", $true),
    @('node.exe', 'node.exe "C:\NetOps Test\project\web\react_frontend\node_modules\vite\bin\vite.js" preview', $true),
    @('node.exe', '"node" "C:\NetOps Test\project\web\react_frontend\node_modules\.bin\\..\vite\bin\vite.js" preview', $true),
    @('python.exe', "$python scripts/assistant/evaluate.py --live", $false),
    @('python.exe', "$python -m pytest tests/unit", $false),
    @('python.exe', "$python src/platform/agent_runtime/dsh_worker.py", $false),
    @('python.exe', "$python -m uvicorn unrelated:app", $false),
    @('python.exe', '"C:\NetOps Test\project-old\venv\Scripts\python.exe" -m uvicorn src.gateway.main:app', $false),
    @('node.exe', 'node.exe "C:\NetOps Test\project\.tmp\assistant_foundation_browser.cjs"', $false),
    @('python.exe', '', $false)
)
foreach ($case in $cases) {
    $process = [pscustomobject]@{ Name=$case[0]; CommandLine=$case[1] }
    if ((Test-ProjectAppProcess -Process $process -ProjectRoot $taskRoot) -ne $case[2]) {
        throw "Unexpected service selection: $($case[1])"
    }
}
$launchers = @(
    @('powershell.exe', 'powershell.exe -NoProfile -File "C:\NetOps Test\project\.runtime\test\logs\fastapi-launcher.ps1"', $true),
    @('powershell.exe', 'powershell.exe -File "C:\NetOps Test\other\.runtime\test\logs\fastapi-launcher.ps1"', $false),
    @('powershell.exe', 'powershell.exe -File "C:\NetOps Test\project\scripts\test\start.ps1"', $false),
    @('python.exe', "$python scripts/assistant/evaluate.py", $false)
)
foreach ($case in $launchers) {
    $process = [pscustomobject]@{ Name=$case[0]; CommandLine=$case[1] }
    if ((Test-ProjectLauncherProcess -Process $process -ProjectRoot $taskRoot) -ne $case[2]) {
        throw "Unexpected launcher selection: $($case[1])"
    }
}
Write-Output "$($cases.Count + $launchers.Count) process selection checks passed; no processes stopped."
