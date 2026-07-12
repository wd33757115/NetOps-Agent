import subprocess
import sys
from pathlib import Path

import tomllib
import yaml


def test_optional_python_dependencies_are_not_in_default_runtime_set():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip().lower() for line in requirements if line.strip()}

    assert not any(line.startswith("qdrant-client") for line in normalized)
    assert not any(line.startswith("llama-index-vector-stores-qdrant") for line in normalized)
    assert not any(line.startswith("langfuse") for line in normalized)
    assert not any(line.startswith("streamlit") for line in normalized)

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    groups = pyproject["tool"]["poetry"]["group"]
    default_dependencies = pyproject["tool"]["poetry"]["dependencies"]
    for dependency in (
        "chromadb",
        "langchain",
        "langgraph",
        "llama-index",
        "openpyxl",
        "python-docx",
    ):
        assert dependency not in default_dependencies
    assert groups["ai"]["optional"] is True
    assert groups["rag"]["optional"] is True
    assert groups["documents"]["optional"] is True
    assert groups["qdrant"]["optional"] is True
    assert groups["observability"]["optional"] is True
    assert groups["legacy-ui"]["optional"] is True


def test_core_data_plane_dependency_set_excludes_optional_product_roles():
    requirements = Path("requirements-core.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip().lower() for line in requirements if line.strip()}

    forbidden = (
        "chromadb",
        "langchain",
        "langgraph",
        "langfuse",
        "llama-index",
        "openpyxl",
        "python-docx",
        "qdrant-client",
        "streamlit",
    )
    assert not any(
        requirement.startswith(prefix)
        for requirement in normalized
        for prefix in forbidden
    )


def test_gateway_dependency_set_supports_advertised_xlsx_import():
    requirements = Path("requirements-gateway.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip().lower() for line in requirements if line.strip()}

    assert any(requirement.startswith("openpyxl") for requirement in normalized)


def test_core_data_plane_smoke_blocks_optional_imports():
    result = subprocess.run(
        [sys.executable, "scripts/test/core_data_plane_smoke.py"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "ok"' in result.stdout


def test_optional_middleware_requires_explicit_compose_profiles():
    compose = yaml.safe_load(Path("deployment/docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]["qdrant"]["profiles"]) == {"rag", "rag-qdrant"}
    assert compose["services"]["langfuse"]["profiles"] == ["observability"]


def test_compose_runtime_roles_use_separate_dependencies_and_queues():
    compose = yaml.safe_load(Path("deployment/docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["fastapi"]["profiles"] == ["core"]
    assert services["fastapi"]["build"]["args"]["REQUIREMENTS_FILE"] == (
        "requirements-gateway.txt"
    )
    assert services["celery-patrol"]["build"]["args"]["REQUIREMENTS_FILE"] == (
        "requirements-core.txt"
    )
    assert "-Q netops.patrol" in services["celery-patrol"]["command"]
    assert "netops.ai" not in services["celery-patrol"]["command"]
    assert "-Q netops.default,netops.firewall" in services["celery-control"]["command"]

    ai_overlay = yaml.safe_load(
        Path("deployment/docker-compose.ai.yml").read_text(encoding="utf-8")
    )
    ai_services = ai_overlay["services"]
    assert ai_services["celery-ai"]["profiles"] == ["ai"]
    assert ai_services["celery-ai"]["build"]["args"]["REQUIREMENTS_FILE"] == (
        "requirements-ai-worker.txt"
    )
    assert "-Q netops.ai" in ai_services["celery-ai"]["command"]
    assert ai_services["fastapi"]["build"]["args"]["REQUIREMENTS_FILE"] == (
        "requirements.txt"
    )


def test_core_gateway_has_no_eager_optional_product_imports():
    source = Path("src/gateway/main.py").read_text(encoding="utf-8")
    eager_imports = (
        "from langchain_core.messages import HumanMessage\n",
        "from src.agents.supervisor.graph_v2 import compiled_graph_v2\n",
        "from src.core.rag_service.service import get_rag_service\n",
        "from src.gateway.chat_stream import stream_supervisor_chat\n",
    )

    for import_line in eager_imports:
        assert not source.startswith(import_line)
        assert f"\n{import_line}" not in source.split("def get_supervisor_graph", 1)[0]
