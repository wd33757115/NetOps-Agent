"""The handbook's demo package: the smallest thing the kernel accepts, answering for whoever may use it."""

from src.apps.hello_demo.backend.api import greeting
from src.auth.models import CurrentUser
from src.platform.kernel.registry import discover


def test_the_kernel_accepts_the_demo_with_a_router_and_a_page_only():
    manifest = discover(app_id="hello-demo")["hello-demo"]

    assert manifest.public()["route"] == "/apps/hello-demo"
    assert set(manifest.data) == {
        "manifest_version", "id", "name", "version", "platform_api", "icon", "dependencies", "backend", "frontend",
    }
    assert "task_names" not in manifest.data["backend"] and "database" not in manifest.data


def test_the_greeting_names_the_user_and_the_version_in_force():
    manifest = discover(app_id="hello-demo")["hello-demo"]

    answer = greeting(actor=CurrentUser(user_id="7", username="operator", role="operator"))

    assert answer["message"] == "你好，operator"
    assert answer["version"] == manifest.data["version"]
    assert answer["role"] == "operator" and answer["served_at"]
