from __future__ import annotations

from src.core.check_items.parser_capability import (
    build_parser_field_naming_context,
    build_parser_check_item_capability,
    parser_output_profile,
)


def _optical_spec(*, measurements: bool) -> dict:
    fields = {
        "interface": {
            "required": True,
            "type": "interface_name",
            "role": "identity",
        },
        "transceiver_status": {
            "required": True,
            "type": "text",
            "role": "state",
            "canonical_candidate": "transceiver.presence",
        },
    }
    if measurements:
        fields.update(
            {
                "rx_power": {
                    "required": True,
                    "type": "number",
                    "role": "metric",
                },
                "tx_power": {
                    "required": True,
                    "type": "number",
                    "role": "metric",
                },
                "rx_power_low": {
                    "required": True,
                    "type": "number",
                    "role": "threshold",
                },
            }
        )
    return {
        "version": 2,
        "business_purpose": "Inspect optical transceivers",
        "record_sets": [
            {
                "name": "optical",
                "entity": "interface_optical",
                "primary_keys": ["interface"],
                "fields": fields,
            }
        ],
    }


def test_parser_field_naming_context_uses_vendor_neutral_semantic_profile():
    context = build_parser_field_naming_context(
        vendor="Cisco",
        model="N9K-C9364C",
        family="NXOS9K",
        profile="NEXUS9000",
        command="show interface transceiver detail",
        sample_text=(
            "Ethernet1/1\n"
            "  Rx Power -3.10 dBm\n"
            "  Rx Power (Warning Low) -17.21 dBm"
        ),
    )

    assert context["semantic_profile_id"] == "interface-optical"
    assert context["resolved"] is True
    assert {
        "interface",
        "channel",
        "rx_power_dbm",
        "tx_power_dbm",
        "rx_low_warning_dbm",
        "temperature_celsius",
        "voltage_v",
        "bias_ma",
    } <= set(context["preferred_value_names"])
    optical = next(
        item
        for item in context["check_items"]
        if item["check_item_id"] == "interface.optical_power"
    )
    vocabulary = {
        item["preferred_name"]: item
        for item in optical["configured_vocabulary"]
    }
    assert "lane_number" in vocabulary["channel"]["aliases"]
    assert "rx_power_high_alarm" in vocabulary["rx_high_alarm_dbm"]["aliases"]
    assert vocabulary["rx_power_dbm"]["source"] == "check_item.parser_field_vocabulary"


def test_parser_field_naming_context_exposes_stable_system_value_names():
    version = build_parser_field_naming_context(
        vendor="Cisco",
        model="WS-C2960XR-24TS-I",
        family="IOS",
        profile="CISCO_IOS",
        command="show version",
    )
    cpu = build_parser_field_naming_context(
        vendor="Cisco",
        model="WS-C2960XR-24TS-I",
        family="IOS",
        profile="CISCO_IOS",
        command="show processes cpu",
    )
    memory = build_parser_field_naming_context(
        vendor="Cisco",
        model="WS-C2960XR-24TS-I",
        family="IOS",
        profile="CISCO_IOS",
        command="show processes memory",
    )

    assert {"os_version", "boot_image", "bootloader_version"} <= set(
        version["preferred_value_names"]
    )
    assert {"cpu_5s", "cpu_1m", "cpu_5m", "process_cpu_5m"} <= set(
        cpu["preferred_value_names"]
    )
    assert {
        "memory_total_bytes",
        "memory_used_bytes",
        "memory_free_bytes",
        "process_holding_bytes",
    } <= set(memory["preferred_value_names"])


def test_parser_field_naming_context_unifies_common_cross_vendor_value_names():
    interface = build_parser_field_naming_context(
        vendor="Cisco",
        model="N9K-C9364C",
        family="NXOS9K",
        profile="N9K-C9364C",
        command="show interface status",
    )
    inventory = build_parser_field_naming_context(
        vendor="H3C",
        model="S5130S-54S-HI-G",
        family="Comware7",
        profile="S5130S-54S-HI-G",
        command="display device manuinfo",
    )
    arp = build_parser_field_naming_context(
        vendor="H3C",
        model="S5130S-54S-HI-G",
        family="Comware7",
        profile="S5130S-54S-HI-G",
        command="display arp",
    )

    assert {"interface", "oper_status", "vlan_id", "speed_raw"} <= set(
        interface["preferred_value_names"]
    )
    vlan = next(
        item
        for item in interface["check_items"][0]["configured_vocabulary"]
        if item["preferred_name"] == "vlan_id"
    )
    assert vlan["value_type"] == "text"
    assert {"model", "serial_number", "hardware_version"} <= set(
        inventory["preferred_value_names"]
    )
    assert {"vrf", "ip_address", "mac_address", "interface"} <= set(
        arp["preferred_value_names"]
    )


def test_parser_output_profile_keeps_canonical_candidates_as_unverified_hints():
    profile = parser_output_profile(_optical_spec(measurements=False))

    assert profile["role_counts"] == {
        "identity": 1,
        "state": 1,
        "metric": 0,
        "threshold": 0,
        "context": 0,
        "unspecified": 0,
    }
    assert profile["canonical_candidate_hints"] == [
        {
            "record_set": "optical",
            "field": "transceiver_status",
            "candidate": "transceiver.presence",
            "status": "unverified_hint",
        }
    ]
    assert "not an approved ObservationContract" in profile["hint_claim"]


def test_presence_only_parser_is_not_claimed_as_complete_optical_power_capability():
    capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=False),
    )

    assert capability["status"] == "parser_shape_incomplete"
    assert capability["shape_assessment"] == {
        "status": "incomplete",
        "observed_roles": ["identity", "state"],
        "missing_required_roles": ["metric", "threshold"],
    }
    assert capability["canonical_mapping"]["status"] == "mapping_unverified"
    assert set(capability["target"]["required_observation_types"]) == {
        "interface.optical.presence",
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    }
    assert {item["code"] for item in capability["blockers"]} == {
        "parser_output_role_missing",
        "observation_contract_mapping_required",
    }
    assert capability["claim_boundary"]["check_item_runtime_ready"] is False


def test_parser_roles_include_catalog_identity_and_threshold_requirements():
    capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=False),
    )

    assert capability["target"]["required_semantic_roles"] == [
        "identity",
        "metric",
        "state",
        "threshold",
    ]
    assert {
        (item["role"], item["reason"])
        for item in capability["target"]["role_requirements"]
    } >= {
        ("identity", "non_device_identity_dimensions"),
        ("metric", "required_canonical_observation"),
        ("state", "required_canonical_observation"),
        ("threshold", "structured_threshold_reference_required"),
    }


def test_metric_fields_make_shape_compatible_but_do_not_prove_canonical_mapping():
    capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=True),
    )

    assert capability["status"] == "mapping_unverified"
    assert capability["shape_assessment"]["status"] == "compatible"
    assert capability["shape_assessment"]["missing_required_roles"] == []
    assert capability["canonical_mapping"]["mapped_observation_types"] == []
    assert capability["claim_boundary"]["canonical_mapping_proven"] is False


def test_observation_contract_is_the_authoritative_canonical_mapping_proof():
    partial = {
        "entity_type": "interface_optical",
        "state_semantics": {
            "transceiver_status": {
                "observation_type": "interface.optical.presence"
            },
        },
    }
    partial_capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=True),
        observation_contract=partial,
    )

    assert partial_capability["canonical_mapping"]["status"] == "partial_mapping"
    assert partial_capability["canonical_mapping"][
        "missing_required_observation_types"
    ] == ["interface.optical.rx_power", "interface.optical.tx_power"]

    complete = {
        "entity_type": "interface_optical",
        "state_semantics": {
            "transceiver_status": {
                "observation_type": "interface.optical.presence"
            },
            "rx_power": {"observation_type": "interface.optical.rx_power"},
            "tx_power": {"observation_type": "interface.optical.tx_power"},
        },
    }
    complete_capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=True),
        observation_contract=complete,
    )

    assert complete_capability["canonical_mapping"]["status"] == "mapping_declared"
    assert complete_capability["canonical_mapping"][
        "missing_required_observation_types"
    ] == []
    assert complete_capability["claim_boundary"]["canonical_mapping_proven"] is True
    assert complete_capability["claim_boundary"]["check_item_runtime_ready"] is False


def test_declared_check_item_must_match_catalog_implementation_scope():
    capability = build_parser_check_item_capability(
        check_item_id="interface.optical_power",
        parser_spec=_optical_spec(measurements=True),
        vendor="UnknownVendor",
        model="UnknownModel",
        profile="UnknownModel",
        command="show unknown optics",
    )

    assert capability["implementation_scope"] == {
        "evaluated": True,
        "matched": False,
        "resolved_check_item_ids": [],
    }
    assert "check_item_implementation_scope_mismatch" in {
        item["code"] for item in capability["blockers"]
    }
