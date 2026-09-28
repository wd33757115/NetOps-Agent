"""Windows bootstrap regression: configuration preservation and container safety."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare_env', ROOT / 'scripts/windows/prepare_env.py')
prepare_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_env)


def test_first_install_generates_secrets_and_preserves_existing_config(tmp_path):
    shutil.copy(ROOT / '.env.example', tmp_path / '.env.example')
    assert prepare_env.prepare(tmp_path)
    original = (tmp_path / '.env').read_bytes()
    values = dict(line.split('=', 1) for line in original.decode().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    keys = ['SECRET_KEY', 'JWT_SECRET_KEY', 'NETOPS_APP_INTERNAL_SECRET',
            'POSTGRES_PASSWORD', 'MINIO_SECRET_KEY']
    assert all(len(values[key]) >= 32 for key in keys)
    assert len({values[key] for key in keys}) == len(keys)
    assert not prepare_env.prepare(tmp_path)
    assert (tmp_path / '.env').read_bytes() == original


def test_windows_dependency_graph_has_valid_requirements_and_no_linux_server():
    visited = set()

    def visit(file):
        file = file.resolve()
        if file in visited:
            return
        visited.add(file)
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                visit(file.parent / line[3:])
            else:
                requirement = Requirement(line)
                if requirement.name == 'gunicorn':
                    assert not requirement.marker.evaluate({'sys_platform': 'win32'})

    visit(ROOT / 'requirements.txt')
    assert ROOT / 'deployment/requirements/core.txt' in visited
    assert ROOT / 'web/django_backend/requirements.txt' in visited


def test_compose_preserves_data_and_optional_observability():
    config = yaml.safe_load((ROOT / 'deployment/compose.windows.yml').read_text())
    assert config['name'] == 'deployment'
    for name in ['postgres-data', 'redis-data', 'rabbitmq-data', 'minio-data']:
        assert config['volumes'][name]['name'] == 'deployment_' + name
    assert config['volumes']['langfuse_pg_data']['name'] == 'langfuse_langfuse_pg_data'
    for name, service in config['services'].items():
        if name.startswith('langfuse'):
            assert service['profiles'] == ['observability']
        assert all(port.startswith('127.0.0.1:') for port in service.get('ports', []))


@pytest.fixture
def containers_runner(tmp_path):
    if not shutil.which('powershell.exe'):
        pytest.skip('Windows PowerShell regression')
    scripts = tmp_path / 'scripts'
    (scripts / 'windows').mkdir(parents=True)
    (tmp_path / '.env').write_text('# fake environment')
    shutil.copy(ROOT / 'scripts/windows/containers.ps1', scripts / 'windows/containers.ps1')
    log = tmp_path / 'commands.jsonl'

    def run(*args, failure='', legacy_volume=None):
        legacy = ''
        if legacy_volume is not None:
            payload = json.dumps({'Config': {'Labels': {'com.docker.compose.project': 'langfuse'}},
                                  'Mounts': [{'Destination': '/var/lib/postgresql/data', 'Name': legacy_volume}]})
            legacy = "if ($Arguments[0] -eq 'inspect') { $stdout = '" + payload + "'; $ok = $true; $stderr = '' }"
        (scripts / '_docker_utils.ps1').write_text('''
function Invoke-DockerCommand {
    param($Arguments, $TimeoutSec, [switch]$ShowOutput, [switch]$Quiet, $ProgressLabel)
    ConvertTo-Json -InputObject @($Arguments) -Compress | Add-Content -LiteralPath '__LOG__'
    $ok = $true; $stdout = ''; $stderr = ''
    if ($Arguments[0] -eq 'info') { $stdout = 'linux' }
    if ($Arguments[0] -eq 'inspect') { $ok = $false; $stderr = 'No such object' }
    __LEGACY__
    if ($Arguments -contains '__FAILURE__') { $ok = $false; $stderr = 'Injected failure' }
    return [pscustomobject]@{Succeeded=$ok; StdOut=$stdout; StdErr=$stderr; ExitCode=1; TimedOut=$false}
}
function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, $TimeoutSec) return @{StatusCode=200} }
'''.replace('__LOG__', str(log).replace("'", "''")).replace('__LEGACY__', legacy)
            .replace('__FAILURE__', failure or 'never-fail'), encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(scripts / 'windows/containers.ps1'),
                                 '-ProjectRoot', str(tmp_path), *args], capture_output=True, timeout=20)
        calls = [json.loads(line.lstrip('\ufeff')) for line in log.read_text(encoding='utf-8-sig').splitlines()]
        return result, calls

    return run


def test_stop_is_service_scoped_without_volume_deletion(containers_runner):
    result, calls = containers_runner('-Action', 'stop')
    assert result.returncode == 0, result.stderr
    assert calls[-1][-5:] == ['stop', 'postgres', 'redis', 'rabbitmq', 'minio']
    assert not any('down' in call or '--remove-orphans' in call or '-v' in call for call in calls)


def test_keep_middleware_only_stops_observability(containers_runner):
    result, calls = containers_runner('-Action', 'stop', '-SkipMiddleware', '-WithLangfuse')
    assert result.returncode == 0, result.stderr
    assert calls[-1][-3:] == ['stop', 'langfuse-db', 'langfuse']


def test_failed_pull_does_not_report_success(containers_runner):
    result, _ = containers_runner('-Action', 'pull', failure='pull')
    assert result.returncode != 0
    assert b'[OK] Containers' not in result.stdout


def test_legacy_volume_mismatch_prevents_any_container_removal(containers_runner):
    result, calls = containers_runner('-Action', 'up', '-WithLangfuse', legacy_volume='unexpected-volume')
    assert result.returncode != 0
    assert not any(call[0] in ('stop', 'rm') for call in calls)


def test_legacy_migration_removes_only_known_containers_after_image_pull(containers_runner):
    result, calls = containers_runner('-Action', 'up', '-WithLangfuse',
                                     legacy_volume='langfuse_langfuse_pg_data')
    assert result.returncode == 0, result.stderr
    destructive = [call for call in calls if call[0] in ('stop', 'rm')]
    assert destructive == [
        ['stop', 'netops-langfuse-standalone', 'netops-langfuse-db'],
        ['rm', 'netops-langfuse-standalone', 'netops-langfuse-db'],
    ]
    assert next(i for i, call in enumerate(calls) if 'pull' in call) < calls.index(destructive[0])
    assert not any('-v' in call or 'prune' in call or '--remove-orphans' in call for call in calls)
