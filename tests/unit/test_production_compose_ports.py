"""Check resolved Compose output so list merging cannot expose production ports."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("with_ai", [False, True])
def test_production_overlay_replaces_public_port_bindings(tmp_path, with_ai):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose is required for configuration validation")
    probe = subprocess.run(
        [docker, "compose", "version"], capture_output=True, timeout=15
    )
    if probe.returncode:
        pytest.skip("Docker Compose plugin is unavailable")

    root = Path(__file__).resolve().parents[2]
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    filenames = ["docker-compose.yml"]
    if with_ai:
        filenames.append("docker-compose.ai.yml")
    filenames.append("docker-compose.prod.yml")
    shutil.copyfile(root / ".env.example", tmp_path / ".env")
    command = [docker, "compose", "--project-name", "netops-config-test"]
    for name in filenames:
        shutil.copyfile(root / "deployment" / name, deployment / name)
        command.extend(["-f", str(deployment / name)])
    command.extend(["--profile", "core"])
    if with_ai:
        command.extend(["--profile", "ai"])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    django = services["django"]["environment"]
    patrol = services["celery-patrol"]["environment"]
    assert "django" in django["DJANGO_ALLOWED_HOSTS"].split(",")
    assert django["CELERY_BROKER_URL"] == patrol["CELERY_BROKER_URL"]
    assert django["CELERY_RESULT_BACKEND"] == patrol["CELERY_RESULT_BACKEND"]
    if with_ai:
        assert "--exclude-queues" in services["celery-ai"]["command"]
    assert {"postgres", "django", "react", "fastapi"} <= services.keys()
    assert not services["fastapi"].get("ports")
    bindings = [
        (name, port)
        for name, service in services.items()
        for port in service.get("ports", [])
    ]
    assert bindings
    assert all(port.get("host_ip") == "127.0.0.1" for _, port in bindings), bindings
    assert len(bindings) == len(
        {(port["published"], port["protocol"]) for _, port in bindings}
    )
