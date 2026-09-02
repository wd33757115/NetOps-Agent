from __future__ import annotations

from types import SimpleNamespace

from src.core.parser.identity import DeviceIdentity
from src.core.parser.parser_bundle import build_parser_bundle
from src.core.parser.policy import PolicyDecision
from src.core.parser.runtime import ParserRuntime

_TEMPLATE = r"""Value interface (\S+)
Value channel_token (\S*)

Start
  ^${interface}(?:\s+${channel_token})?\s*$$ -> Record
"""

_IDENTITY = DeviceIdentity(
    vendor="H3C",
    model="S6000",
    family="Comware7",
    confidence=1.0,
)


class _Registry:
    def __init__(self, template) -> None:
        self.template = template

    def resolve_active(self, **_kwargs):
        return self.template


class _Policy:
    def decide(self, **_kwargs) -> PolicyDecision:
        return PolicyDecision(
            enabled=True,
            canonical_command="display transceiver diagnosis interface",
            handling="structured",
            category="interface_optical",
            capability="interface_optical",
            capability_status="candidate",
        )


class _Classifier:
    @staticmethod
    def classify(*_args, **_kwargs):
        return SimpleNamespace(status="data", reason="test")


class _AssetStore:
    def __init__(self, content: str) -> None:
        self.content = content

    @staticmethod
    def verify(*_args, **_kwargs) -> bool:
        return True

    def read(self, *_args, **_kwargs) -> str:
        return self.content


def _runtime(*, content: str, validation: dict):
    template = SimpleNamespace(
        id="candidate-optical",
        version="candidate-v1",
        category="interface_optical",
        asset_path="embedded://candidate-optical",
        sha256="a" * 64,
        validation_json=validation,
    )
    return ParserRuntime(
        registry=_Registry(template),
        policy=_Policy(),
        asset_store=_AssetStore(content),
        output_classifier=_Classifier(),
    )


def _single_record_validation(*, include_default: bool) -> dict:
    channel = {"required": False, "type": "text"}
    if include_default:
        channel["primary_key_default"] = "aggregate"
    return {
        "category": "interface_optical",
        "entity_type": "interface_optical",
        "primary_keys": ["interface", "channel_token"],
        "required_fields": ["interface"],
        "optional_fields": ["channel_token"],
        "validators": {
            "interface": {"type": "interface_name"},
            "channel_token": {"type": "text"},
        },
        # Older pending candidates only retain the default inside this frozen
        # ParserSpec; runtime replay must honour it without changing the row.
        "parser_spec": {
            "entity": "interface_optical",
            "primary_keys": ["interface", "channel_token"],
            "fields": {
                "interface": {"required": True, "type": "interface_name"},
                "channel_token": channel,
            },
        },
    }


def test_runtime_uses_sparse_primary_key_default_only_for_key_validation():
    result = _runtime(
        content=_TEMPLATE,
        validation=_single_record_validation(include_default=True),
    ).parse(
        identity=_IDENTITY,
        command="display transceiver diagnosis interface",
        raw_text="GE1/0/1\nGE1/0/2 1\n",
    )

    assert result.parser_status == "success"
    # The source blank must be preserved for the Adapter Projection, which is
    # responsible for turning it into the canonical aggregate channel.
    assert result.structured_data == [
        {"interface": "GE1/0/1", "channel_token": ""},
        {"interface": "GE1/0/2", "channel_token": "1"},
    ]


def test_runtime_rejects_the_same_sparse_key_when_no_default_is_declared():
    result = _runtime(
        content=_TEMPLATE,
        validation=_single_record_validation(include_default=False),
    ).parse(
        identity=_IDENTITY,
        command="display transceiver diagnosis interface",
        raw_text="GE1/0/1\n",
    )

    assert result.parser_status == "failed"
    assert result.failure_code == "primary_key_invalid"


def test_runtime_uses_parser_spec_defaults_for_each_parser_bundle_record_set():
    content = build_parser_bundle({"optics": _TEMPLATE}).serialize()
    result = _runtime(
        content=content,
        validation={
            "category": "multi_record",
            "record_sets": [
                {
                    "name": "optics",
                    "entity_type": "interface_optical",
                    "primary_keys": ["interface", "channel_token"],
                    "required_fields": ["interface"],
                    "optional_fields": ["channel_token"],
                    "validators": {},
                }
            ],
            "parser_spec": {
                "version": 2,
                "business_purpose": "Parse optical diagnostic channels.",
                "record_sets": [
                    {
                        "name": "optics",
                        "entity": "interface_optical",
                        "cardinality": "repeated",
                        "primary_keys": ["interface", "channel_token"],
                        "fields": {
                            "interface": {
                                "required": True,
                                "type": "interface_name",
                            },
                            "channel_token": {
                                "required": False,
                                "type": "text",
                                "primary_key_default": "aggregate",
                            },
                        },
                        "allow_empty": False,
                        "empty_patterns": [],
                    }
                ],
            },
        },
    ).parse(
        identity=_IDENTITY,
        command="display transceiver diagnosis interface",
        raw_text="GE1/0/1\n",
    )

    assert result.parser_status == "success"
    assert result.structured_data == {
        "format": "netops.structured_record_sets/v1",
        "record_sets": {
            "optics": [{"interface": "GE1/0/1", "channel_token": ""}],
        },
    }
