from __future__ import annotations

import json

from src.core.parser import bootstrap as parser_bootstrap


def test_bootstrap_shared_templates_resolves_current_immutable_path(
    tmp_path, monkeypatch
):
    template_content = "Value MODEL (\\S+)\n\nStart\n  ^${MODEL} -> Record\n"
    current_path = (
        tmp_path
        / "Cisco"
        / "ISR4451-X_K9"
        / "show_version__1.0.0.textfsm"
    )
    current_path.parent.mkdir(parents=True)
    current_path.write_bytes(template_content.encode("utf-8"))
    manifest = {
        "templates": [
            {
                "vendor": "Cisco",
                "model": "ISR4451-X_K9",
                "family": "IOS-XE",
                "command": "show version",
                "category": "device_version",
                "parser_version": "1.0.0",
                "template_path": "ISR4451-X_K9/show_version.textfsm",
                "validation_pass_rate": 1.0,
                "sample_count": 2,
            }
        ]
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    repositories = []

    class FakeRepository:
        def __init__(self):
            self.registered = []
            self.activated = []
            repositories.append(self)

        def find_template_version(self, **_kwargs):
            return None

        def register_template(self, **kwargs):
            self.registered.append(kwargs)
            return "template-1"

        def activate_template(self, template_id):
            self.activated.append(template_id)

    monkeypatch.setattr(parser_bootstrap, "ParserRepository", FakeRepository)

    result = parser_bootstrap.bootstrap_shared_templates(tmp_path)

    assert result == {"imported": 1, "skipped": 0, "errors": []}
    repository = repositories[0]
    registered = repository.registered[0]
    assert registered["asset_path"].endswith(
        "Cisco\\ISR4451-X_K9\\show_version__1.0.0.textfsm"
    ) or registered["asset_path"].endswith(
        "Cisco/ISR4451-X_K9/show_version__1.0.0.textfsm"
    )
    assert registered["validation"]["bootstrap_source_path"] == str(current_path)
    assert registered["validation"]["regression_validation"] == {
        "passed": True,
        "validation_pass_rate": 1.0,
        "sample_count": 2,
        "source": "shared_manifest_bootstrap",
    }
    assert repository.activated == ["template-1"]
