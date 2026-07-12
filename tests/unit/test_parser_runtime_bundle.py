from types import SimpleNamespace

import pytest

from src.core.parser.identity import DeviceIdentity
from src.core.parser.runtime import ParserRuntime
from src.core.parser.runtime_bundle import ParserRuntimeBundle


class FakePolicy:
    def decide(self, **kwargs):
        return SimpleNamespace(
            canonical_command=str(kwargs["command"]).strip().lower(),
            enabled=True,
            handling="structured",
        )


class SwitchingRegistry:
    def __init__(self):
        self.active = SimpleNamespace(
            id="template-v1",
            version="1",
            sha256="a" * 64,
        )
        self.templates = {self.active.id: self.active}

    def resolve_active(self, **_kwargs):
        return self.active

    def get_template(self, template_id):
        return self.templates.get(template_id)


def _identity():
    return DeviceIdentity(
        vendor="Cisco",
        model="C2960",
        family="IOS-C2960",
        confidence=1.0,
    )


def test_runtime_bundle_pins_template_when_active_version_changes():
    registry = SwitchingRegistry()
    source_runtime = ParserRuntime(policy=FakePolicy(), registry=registry)
    bundle = ParserRuntimeBundle.build(
        identity=_identity(),
        commands=["show interface status"],
        runtime=source_runtime,
    )
    registry.active = SimpleNamespace(
        id="template-v2",
        version="2",
        sha256="b" * 64,
    )
    registry.templates[registry.active.id] = registry.active

    pinned_runtime = ParserRuntime(
        policy=FakePolicy(),
        registry=registry,
        runtime_bundle=bundle,
    )
    template = pinned_runtime._resolve_template(
        identity=_identity(),
        canonical_command="show interface status",
    )

    assert template.id == "template-v1"
    assert bundle.digest == ParserRuntimeBundle.from_dict(bundle.to_dict()).digest


def test_runtime_bundle_rejects_tampered_payload():
    registry = SwitchingRegistry()
    bundle = ParserRuntimeBundle.build(
        identity=_identity(),
        commands=["show interface status"],
        runtime=ParserRuntime(policy=FakePolicy(), registry=registry),
    ).to_dict()
    bundle["templates"]["show interface status"]["version"] = "999"

    with pytest.raises(ValueError, match="digest mismatch"):
        ParserRuntimeBundle.from_dict(bundle)


def test_runtime_bundle_rejects_mutated_template_record():
    registry = SwitchingRegistry()
    bundle = ParserRuntimeBundle.build(
        identity=_identity(),
        commands=["show interface status"],
        runtime=ParserRuntime(policy=FakePolicy(), registry=registry),
    )
    registry.templates["template-v1"] = SimpleNamespace(
        id="template-v1",
        version="1",
        sha256="c" * 64,
    )

    with pytest.raises(RuntimeError, match="Pinned Parser template changed"):
        bundle.resolve(
            canonical_command="show interface status",
            registry=registry,
        )
