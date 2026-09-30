import json

import pytest
from pydantic import ValidationError

from src.platform.device_access.config_sections import (
    MAX_FRAGMENT_BYTES,
    Scope,
    build_command,
    compare_sections,
    extract_section,
    redact_config,
)


def scope(section="interface", target="GigabitEthernet3/0"):
    return Scope(platform="comware", section=section, target=target)


def fragment(text, selected=None, **kwargs):
    return extract_section(text, selected or scope(), source_complete=True, **kwargs)


def test_fixed_commands_are_complete_and_cannot_export_all_configuration():
    assert build_command(scope()) == "display current-configuration interface GigabitEthernet3/0"
    assert build_command(scope("ospf", "1")) == "display current-configuration configuration ospf"


@pytest.mark.parametrize(
    "target",
    [
        "GE3/0",
        "GigabitEthernet",
        "GigabitEthernet3/0 | include password",
        "GigabitEthernet3/0\nreturn",
        "Vlan-interface100;display current-configuration",
        "GigabitEthernet3/0\t",
        "1",
        "",
        " GigabitEthernet3/0",
    ],
)
def test_interface_scope_rejects_abbreviations_and_injection(target):
    with pytest.raises(ValidationError):
        scope(target=target)


@pytest.mark.parametrize("target", ["0", "65536", "01", "1 area 0", "1\n", "١"])
def test_process_scope_rejects_unsupported_ids(target):
    with pytest.raises(ValidationError):
        scope("ospf", target)


def test_other_platform_and_sections_are_not_accepted():
    with pytest.raises(ValidationError):
        Scope(platform="ios", section="interface", target="GigabitEthernet3/0")
    with pytest.raises(ValidationError):
        Scope(platform="comware", section="acl", target="3000")


def test_exact_interface_only_and_original_line_numbers():
    text = (
        "#\ninterface GigabitEthernet3/01\n description other\n#\n"
        "interface GigabitEthernet3/0\n port access vlan 100\n#\n"
        "interface Vlan-interface100\n ip address 192.0.2.1 24\n#\nreturn"
    )
    result = fragment(text)
    assert result["complete"]
    assert result["start_line"] == 5 and result["end_line"] == 6
    assert result["lines"] == [
        {"line": 5, "text": "interface GigabitEthernet3/0"},
        {"line": 6, "text": " port access vlan 100"},
    ]
    assert "description other" not in result["text"]


def test_ospf_selects_one_process_and_rejects_vpn_scope():
    text = (
        "#\nospf 1 router-id 192.0.2.1\n area 0.0.0.0\n"
        "  network 192.0.2.0 0.0.0.255\n#\nospf 11\n area 1\n#"
    )
    result = fragment(text, scope("ospf", "1"))
    assert result["complete"] and "ospf 11" not in result["text"]
    invalid = fragment("#\nospf 1 vpn-instance example\n area 0\n#", scope("ospf", "1"))
    assert (
        not invalid["complete"] and "ospf_process_context_not_supported" in invalid["quality_gaps"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "#\ninterface Vlan-interface100\n shutdown\n#",
        "#\ninterface GigabitEthernet3/0\n shutdown",
        "#\ninterface GigabitEthernet3/0\n shutdown\ninterface Vlan-interface100\n#",
        "#\ninterface GigabitEthernet3/0\n shutdown\n#\ninterface GigabitEthernet3/0\n#",
        "Wrong parameter found at '^' position.\n#\ninterface GigabitEthernet3/0\n#",
        "#\ninterface GigabitEthernet3/0\n---- More ----\n#",
        "#\ninterface GigabitEthernet3/0\n[output truncated]\n#",
        "#\ninterface GigabitEthernet3/0\n#\nPermission denied",
        "#\ninterface GigabitEthernet3/0\n#\nOutput was truncated",
    ],
)
def test_bad_missing_or_ambiguous_output_cannot_be_unchanged(text):
    value = fragment(text)
    assert not value["complete"]
    assert compare_sections(value, value)["status"] == "unavailable"


def test_source_completeness_and_command_are_required_for_valid_scope():
    text = "#\ninterface GigabitEthernet3/0\n shutdown\n#"
    assert not extract_section(text, scope(), source_complete=False)["complete"]
    assert fragment(text, command="display current-configuration")["complete"]
    assert fragment(text, command="display current-configuration interface GigabitEthernet 3/0")[
        "complete"
    ]
    assert not fragment(text, command="display current-configuration | include interface")[
        "complete"
    ]


def test_redaction_preserves_lines_and_hides_authentication_private_key_values():
    text = (
        "#\ninterface GigabitEthernet3/0\n ospf authentication-mode md5 1 cipher SECRET-A\n"
        " password cipher SECRET-B\n -----BEGIN PRIVATE KEY-----\n SECRET-C\n"
        " -----END PRIVATE KEY-----\n mtu 1500\n#"
    )
    safe = redact_config(text)
    assert "SECRET-" not in safe
    assert len(safe.splitlines()) == len(text.splitlines())
    result = fragment(text)
    assert result["redacted"] and result["complete"]
    assert result["lines"][-1] == {"line": 8, "text": " mtu 1500"}
    assert compare_sections(result, result)["status"] == "unavailable"
    assert "SECRET-" not in json.dumps(result)


def test_already_redacted_ssh_output_keeps_hidden_line_warning():
    result = fragment("#\ninterface GigabitEthernet3/0\n[redacted credential line]\n#")
    assert result["redacted"] and result["complete"]
    assert compare_sections(result, result)["status"] == "unavailable"


def test_raw_baseline_and_pre_audit_redacted_live_do_not_invent_sensitive_changes():
    from src.platform.device_access.governance import redact

    text = (
        "#\ninterface GigabitEthernet3/0\n"
        " ospf authentication-mode md5 1 cipher EXAMPLE-SECRET\n mtu 1500\n#"
    )
    before, after = fragment(text), fragment(redact(text))
    result = compare_sections(before, after)
    assert before["complete"] and after["complete"]
    assert result["status"] == "unavailable"
    assert not result["added"] and not result["removed"]
    assert "sensitive_lines_not_compared" in result["quality_gaps"]
    assert "EXAMPLE-SECRET" not in json.dumps(result)


def test_actual_existing_config_diff_returns_deterministic_target_only_changes():
    before = fragment("#\ninterface GigabitEthernet3/0\n port access vlan 100\n#")
    after = fragment("#\ninterface GigabitEthernet3/0\n port access vlan 101\n#")
    result = compare_sections(before, after)
    assert result["status"] == "changed"
    assert result["added"] == [" port access vlan 101"]
    assert result["removed"] == [" port access vlan 100"]
    assert result["added_count"] == result["removed_count"] == 1
    assert compare_sections(before, before)["status"] == "unchanged"
    assert (
        compare_sections(before, {**after, "target": "GigabitEthernet3/1"})["status"]
        == "unavailable"
    )


def test_large_scope_is_bounded_and_never_claims_complete():
    result = fragment("#\ninterface GigabitEthernet3/0\n" + " description 中文配置" * 1000 + "\n#")
    size = len(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode()
    )
    assert size <= MAX_FRAGMENT_BYTES
    assert result["truncated"] and not result["complete"]
    assert compare_sections(result, result)["status"] == "unavailable"
