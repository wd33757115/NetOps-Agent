"""A failed database/App preflight must not stop the running application."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("check_only", [False, True])
@pytest.mark.parametrize("database_exit", [0, 23])
def test_database_preflight_preserves_processes_and_reports_cause(
    tmp_path, check_only, database_exit
):
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell integration")
    scripts = tmp_path / "scripts/windows"
    scripts.mkdir(parents=True)
    source = (ROOT / "scripts/windows/start.ps1").read_text(encoding="utf-8-sig")
    source = source.replace(
        '$venvPython = Join-Path $ProjectRoot "venv\\Scripts\\python.exe"',
        "$venvPython = 'Invoke-FakePython'",
    )
    (scripts / "start.ps1").write_text(source, encoding="utf-8-sig")
    (scripts / "_port_utils.ps1").write_text(
        r"""
function Test-Path { param($Path, $LiteralPath) return $true }
function Import-DotEnvFile { param($Path) return @{} }
function Write-ColorOutput { param($Text, $Color) Write-Host $Text }
function Initialize-WindowsRuntimeEnvironment {
    param($ProjectRoot)
    $env:NETOPS_APP_INTERNAL_SECRET = 'test-only-secret-with-at-least-32-characters'
}
function npm {}
function Test-DockerAvailable { param($TimeoutSec, [switch]$Quiet) return $false }
function Invoke-FakePython {
    if ($args -contains 'check-dependencies') { $global:LASTEXITCODE = 0; return }
    if ($args -notcontains 'src.platform.database.bootstrap' -or $args -notcontains 'check') {
        throw 'Unexpected Python command'
    }
    $global:LASTEXITCODE = __EXIT__
    if ($global:LASTEXITCODE -ne 0) {
        Write-Output 'ValueError: device-access: disable/drain the deployed release before bootstrap'
    } else { Write-Output '[OK] Platform database check complete' }
}
function Stop-ProjectAppProcesses {
    param($ProjectRoot)
    'reached' | Set-Content (Join-Path $ProjectRoot 'stop.marker')
    throw 'Successful preflight reached process teardown sentinel'
}
""".replace("__EXIT__", str(database_exit)),
        encoding="utf-8-sig",
    )
    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(scripts / "start.ps1"),
        "-ProjectRoot",
        str(tmp_path),
        "-SkipMiddleware",
        "-SkipLangfuse",
        "-AllowNoDocker",
    ]
    if check_only:
        command.append("-CheckOnly")
    result = subprocess.run(command, capture_output=True, timeout=25)
    stopped = (tmp_path / "stop.marker").exists()
    assert stopped == (database_exit == 0 and not check_only)
    log = (tmp_path / ".runtime/test/logs/database-check.log").read_text(encoding="utf-8-sig")
    if database_exit:
        assert result.returncode != 0
        assert b"device-access: disable/drain" in result.stdout
        assert "device-access: disable/drain" in log
        assert b"Startup checks passed" not in result.stdout
    elif check_only:
        assert result.returncode == 0, result.stderr
        assert b"Startup checks passed" in result.stdout
