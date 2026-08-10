from __future__ import annotations

from dataclasses import replace

from scripts.test.ntc_template_upgrade_regression import _select_resolution
from src.core.parser_generation import (
    NtcPlatformMapper,
    NtcTemplateResolver,
    ParserReviewReport,
    ParserSample,
    ParserSpec,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TextFSMValidationBackend,
    compare_ntc_resolution,
    freeze_ntc_resolution,
    validate_template_runtime_compatibility,
)


def _sample(text: str = "400 Po400 up\n") -> ParserSample:
    return ParserSample(
        sample_id="sample-1",
        raw_text=text,
        prompt_text=text,
        role=SampleRole.GENERATION,
    )


def _spec() -> ParserSpec:
    return ParserSpec.from_dict(
        {
            "entity": "vpc",
            "primary_keys": ["id"],
            "fields": {
                "id": {"required": True, "type": "number"},
                "port": {"required": True, "type": "text"},
                "status": {"required": True, "type": "text"},
            },
            "allow_empty": False,
            "empty_patterns": [],
        }
    )


class _NoModel:
    def open_session(self, *_args, **_kwargs):
        raise AssertionError("NTC candidate should not call the generation model")


class _AcceptingReviewer:
    def review(self, **_kwargs):
        return ParserReviewReport(
            passed=True,
            summary="Parsed rows match the sample.",
            primary_key_assessment={"passed": True},
            completeness_assessment={"passed": True},
        )


class _ParserSpecRejectingReviewer:
    def review(self, **_kwargs):
        return ParserReviewReport(
            passed=False,
            summary="The executable fields are faithful; ParserSpec needs revision.",
            diagnostics=(
                {
                    "code": "field_misaligned",
                    "sample_id": "sample-1",
                    "target": "parser_spec",
                    "message": "Align the editable ParserSpec with the parsed values.",
                },
                {
                    "code": "review_record_set_assessment_missing",
                    "sample_id": "",
                    "target": "record_scope",
                    "message": "Provider omitted the record-set assessment.",
                },
            ),
        )


def test_ntc_platform_mapping_requires_enough_cisco_identity():
    mapper = NtcPlatformMapper()

    missing_family = mapper.resolve({"vendor": "Cisco"})
    nxos = mapper.resolve({"vendor": "Cisco", "family": "NXOS"})
    nxos_from_model = mapper.resolve(
        {"vendor": "Cisco", "model": "N9K-C9364C"}
    )
    nxos_from_family_variant = mapper.resolve(
        {"vendor": "Cisco", "family": "NEXUS9000"}
    )
    ios_from_model = mapper.resolve(
        {"vendor": "Cisco", "model": "WS-C2960XR-24TS-I"}
    )
    ios_from_family_variant = mapper.resolve(
        {"vendor": "Cisco", "family": "IOS-C2960"}
    )

    assert missing_family.status == "unresolved"
    assert missing_family.reason_codes == ("identity_missing:family",)
    assert nxos.status == "resolved"
    assert nxos.platforms == ("cisco_nxos",)
    assert nxos_from_model.platforms == ("cisco_nxos",)
    assert nxos_from_model.mapping_ids == ("cisco-nxos",)
    assert nxos_from_family_variant.platforms == ("cisco_nxos",)
    assert ios_from_model.platforms == ("cisco_ios",)
    assert ios_from_model.mapping_ids == ("cisco-ios",)
    assert ios_from_family_variant.platforms == ("cisco_ios",)


def test_ntc_resolver_returns_provenance_and_objective_execution():
    decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )

    assert decision.status == "resolved"
    assert len(resolutions) == 1
    result = resolutions[0]
    assert result.status == "resolved"
    assert result.template_name == "cisco_nxos_show_vpc.textfsm"
    assert result.candidate is not None
    provenance = dict(result.candidate.provenance or {})
    assert provenance["provider"] == "ntc_templates"
    assert provenance["package_version"] == "9.1.0"
    assert provenance["source_template"] == "cisco_nxos_show_vpc.textfsm"
    assert provenance["content_sha256"] == result.candidate.sha256
    assert provenance["source_content_sha256"]
    assert provenance["source_template_text"]
    assert provenance["platform"] == "cisco_nxos"
    assert provenance["command"] == "show vpc"
    assert provenance["license"] == "Apache-2.0"
    assert provenance["platform_mapping_version"] == 2
    assert provenance["platform_mapping_ids"] == ["cisco-nxos"]
    assert result.execution_evidence["compile_status"] == "compiled"
    assert result.execution_evidence["samples"][0]["record_count"] == 1
    assert result.execution_evidence["samples"][0]["field_names"] == [
        "id",
        "port",
        "state",
    ]
    assert provenance["transformation_version"] == "ntc-platform-compat/v4"
    assert provenance["transformations"] == [
        {
            "kind": "field_name_case_normalization",
            "mapping": {"ID": "id", "PORT": "port"},
        },
        {
            "kind": "field_name_contract_alignment",
            "mapping": {"STATUS": "state"},
        },
    ]
    assert len(result.execution_evidence["samples"][0]["record_shape_sha256"]) == 64
    assert len(result.execution_evidence["samples"][0]["records_sha256"]) == 64


def test_ntc_resolver_adapts_trusted_catch_all_error_without_widening_llm_dialect():
    sample = """Port Name Status Vlan Duplex Speed Type
Eth1/1 -- connected trunk full 10G 10Gbase-SR
"""
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show interface status",
        samples=(_sample(sample),),
    )

    result = resolutions[0]
    assert result.status == "resolved"
    assert result.candidate is not None
    provenance = dict(result.candidate.provenance or {})
    assert "-> Error" in provenance["source_template_text"]
    assert "-> Error" not in result.candidate.template_text
    assert provenance["transformation_version"] == "ntc-platform-compat/v4"
    assert provenance["transformations"] == [
        {
            "kind": "field_name_case_normalization",
            "mapping": {
                "DUPLEX": "duplex",
                "VLAN_ID": "vlan_id",
            },
        },
        {
            "kind": "field_name_contract_alignment",
            "mapping": {
                "NAME": "description",
                "PORT": "interface",
                "SPEED": "speed_raw",
                "STATUS": "oper_status",
                "TYPE": "media_type",
            },
        },
        {
            "kind": "catch_all_error_to_ignore",
            "line_number": 14,
            "source": "^. -> Error",
            "derived": "^.",
        }
    ]
    assert result.execution_evidence["samples"][0]["record_count"] == 1


def test_ntc_resolver_aligns_unambiguous_source_field_qualifier_to_cli_label():
    sample = """Port Name Status Vlan Duplex Speed Type
Eth1/1 -- connected trunk full 10G 10Gbase-SR
"""
    spec = ParserSpec.from_dict(
        {
            "entity": "interface",
            "primary_keys": ["port"],
            "fields": {
                "port": {"required": True, "type": "interface_name", "source_labels": ["Port"]},
                "name": {"required": True, "type": "text", "source_labels": ["Name"]},
                "status": {"required": True, "type": "text", "source_labels": ["Status"]},
                "vlan": {"required": True, "type": "text", "source_labels": ["Vlan"]},
                "duplex": {"required": True, "type": "text", "source_labels": ["Duplex"]},
                "speed": {"required": True, "type": "text", "source_labels": ["Speed"]},
                "type": {"required": True, "type": "text", "source_labels": ["Type"]},
            },
        }
    )

    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show interface status",
        samples=(_sample(sample),),
        spec=spec,
    )

    result = resolutions[0]
    assert result.status == "resolved"
    assert result.candidate is not None
    assert "Value vlan " in result.candidate.template_text
    assert "Value vlan_id " not in result.candidate.template_text
    provenance = dict(result.candidate.provenance or {})
    assert provenance["transformation_version"] == "ntc-platform-compat/v4"
    assert provenance["transformations"][1] == {
        "kind": "field_name_contract_alignment",
        "mapping": {"VLAN_ID": "vlan"},
    }


def test_ntc_resolver_aligns_source_labels_to_parser_spec_fields():
    sample = '''NAME: "Chassis", DESCR: "Nexus chassis"
PID: N9K-C9364C, VID: V01, SN: FDO123
'''
    spec = ParserSpec.from_dict(
        {
            "entity": "inventory_item",
            "primary_keys": ["name"],
            "fields": {
                "name": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["NAME"],
                },
                "description": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["DESCR"],
                },
                "pid": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["PID"],
                },
                "vid": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["VID"],
                },
                "serial_number": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["SN"],
                },
            },
        }
    )

    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show inventory",
        samples=(_sample(sample),),
        spec=spec,
    )

    result = resolutions[0]
    assert result.status == "resolved"
    assert result.candidate is not None
    assert "Value description " in result.candidate.template_text
    assert "Value serial_number " in result.candidate.template_text
    assert result.execution_evidence["samples"][0]["field_names"] == [
        "description",
        "name",
        "pid",
        "serial_number",
        "vid",
    ]
    provenance = dict(result.candidate.provenance or {})
    assert provenance["transformation_version"] == "ntc-platform-compat/v4"
    assert {
        "kind": "field_name_contract_alignment",
        "mapping": {"DESCR": "description", "SN": "serial_number"},
    } in provenance["transformations"]


def test_ntc_not_found_is_explicit():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show command that does not exist",
        samples=(_sample(),),
    )

    assert resolutions[0].status == "not_found"
    assert resolutions[0].candidate is None


def test_ntc_resolver_rejects_prefix_match_for_a_different_command():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "IOS"},
        command="show processes memory",
        samples=(_sample("Processor Pool Total: 100 Used: 40 Free: 60\n"),),
    )

    result = resolutions[0]
    assert result.status == "not_found"
    assert result.template_name == "cisco_ios_show_processes_cpu.textfsm"
    assert result.candidate is None
    assert "prefix" in str(result.error)


def test_ntc_resolver_normalizes_required_fields_to_check_item_vocabulary():
    sample = """CPU utilization for five seconds: 34%/0%; one minute: 12%; five minutes: 8%
 PID Runtime(ms) Invoked uSecs 5Sec 1Min 5Min TTY Process
   1 23 142 161 0.00% 0.00% 0.00% 0 Chunk Manager
"""
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "IOS"},
        command="show processes cpu",
        samples=(_sample(sample),),
    )

    result = resolutions[0]
    assert result.status == "resolved"
    fields = result.execution_evidence["samples"][0]["field_names"]
    assert "process_id" in fields
    assert "cpu_5s" in fields
    assert "cpu_1m" in fields
    assert "cpu_5m" in fields
    assert "process_runtime_ms" in fields
    assert "process_invocation_count" in fields
    assert "process_usec_per_invocation" in fields
    assert "PROCESS_PID" not in fields


def test_ntc_resolver_uses_same_device_version_vocabulary_across_cisco_families():
    _nxos_decision, nxos_resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show version",
        samples=(_sample("unmatched version output\n"),),
    )
    _ios_decision, ios_resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "IOS"},
        command="show version",
        samples=(_sample("unmatched version output\n"),),
    )

    nxos_template = nxos_resolutions[0].candidate.template_text
    ios_template = ios_resolutions[0].candidate.template_text
    assert "Value os_version " in nxos_template
    assert "Value model " in nxos_template
    assert "Value bios_version " in nxos_template
    assert "Value os_version " in ios_template
    assert "Value List model " in ios_template
    assert "Value rommon_version " in ios_template


def test_ntc_field_profiles_scan_all_rows_and_surface_late_sentinels():
    sample = """VLAN     MAC Address      Type      age     Secure NTFY Ports
---------+-----------------+--------+---------+------+----+------------------
*   1     0011.2233.4455    dynamic   0         F      F    Eth1/1
*   2     0011.2233.4456    dynamic   0         F      F    Eth1/2
*   3     0011.2233.4457    dynamic   0         F      F    Eth1/3
*   4     0011.2233.4458    dynamic   0         F      F    Eth1/4
*   5     0011.2233.4459    dynamic   0         F      F    Eth1/5
*   6     0011.2233.4460    dynamic   0         F      F    Eth1/6
*   7     0011.2233.4461    dynamic   0         F      F    Eth1/7
*   8     0011.2233.4462    dynamic   0         F      F    Eth1/8
G   -     0000.0c9f.f001    static    -         F      F    vPC-Peer-Link
"""
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show mac address-table",
        samples=(_sample(sample),),
    )

    result = resolutions[0]
    assert result.status == "resolved"
    vlan_profile = result.execution_evidence["field_profiles"]["vlan_id"]
    age_profile = result.execution_evidence["field_profiles"]["age"]
    assert vlan_profile["value_shape"] == "text"
    assert vlan_profile["value_shape_counts"] == {"number": 8, "text": 1}
    assert vlan_profile["examples"][:2] == ["1", "-"]
    assert age_profile["value_shape"] == "text"
    assert age_profile["examples"][:2] == ["0", "-"]


def test_ntc_resolver_projects_broad_template_to_reviewed_parser_spec():
    sample = """Power Supply:
Voltage: 12 Volts
Power Actual Actual Total
Supply Model Output Input Capacity Status
1 NXA-PAC-1200W-PI 141 W 158 W 1200 W Ok
"""
    spec = ParserSpec.from_dict(
        {
            "entity": "power_supply",
            "primary_keys": ["power_supply"],
            "fields": {
                "power_supply": {"required": True, "type": "number"},
                "model": {"required": False, "type": "text"},
                "output_watts": {"required": False, "type": "number"},
                "input_watts": {"required": False, "type": "number"},
                "capacity_watts": {"required": False, "type": "number"},
                "power_supply_status": {"required": False, "type": "text"},
            },
        }
    )

    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show environment power",
        samples=(_sample(sample),),
        spec=spec,
    )

    result = resolutions[0]
    assert result.status == "resolved"
    assert result.candidate is not None
    assert "Value fan " not in result.candidate.template_text
    assert "Value temperature_current " not in result.candidate.template_text
    assert result.execution_evidence["samples"][0]["field_names"] == [
        "capacity_watts",
        "input_watts",
        "model",
        "output_watts",
        "power_supply",
        "power_supply_status",
    ]
    provenance = dict(result.candidate.provenance or {})
    projection = next(
        item
        for item in provenance["transformations"]
        if item["kind"] == "field_schema_projection"
    )
    assert projection["removed_fields"] == [
        "fan",
        "fan_status",
        "temperature_current",
        "temperature_major_thresh",
        "temperature_minor_thresh",
        "temperature_module",
        "temperature_sensor",
        "temperature_status",
    ]


def test_objective_execution_does_not_turn_zero_records_into_semantic_failure():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample("vPC domain id : 10\n"),),
    )

    assert resolutions[0].status == "resolved"
    assert resolutions[0].execution_evidence["samples"][0]["record_count"] == 0


def test_execution_backend_accepts_native_textfsm_runtime_constructs():
    template = r"""Value Required ITEM (\S+)

Start
 ^skip$$ -> NoRecord
 ^${ITEM}$$ -> Record
"""
    backend = TextFSMValidationBackend()

    assert backend.compile(template) == ()
    assert backend.parse(template, "alpha\nskip\n") == [{"ITEM": "alpha"}]


def test_reviewed_ntc_candidate_bypasses_generation_model():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
        spec=_spec(),
    )
    candidate = resolutions[0].candidate
    assert candidate is not None

    outcome = SimpleParserGenerationService(
        compilers=(),
        model=_NoModel(),
        backend=TextFSMValidationBackend(),
        reviewer=_AcceptingReviewer(),
        seed_candidates=(candidate,),
        validator=validate_template_runtime_compatibility,
    ).generate(
        job_id="job-1",
        spec=_spec(),
        sample_set=SampleSet((_sample(),)),
    )

    assert outcome.passed
    assert outcome.stop_reason == "ntc_candidate_reviewed"
    assert outcome.model_calls == 0
    assert outcome.candidate == candidate


def test_objectively_valid_ntc_candidate_routes_spec_conflict_to_human_review():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
        spec=_spec(),
    )
    candidate = resolutions[0].candidate
    assert candidate is not None

    outcome = SimpleParserGenerationService(
        compilers=(),
        model=_NoModel(),
        backend=TextFSMValidationBackend(),
        reviewer=_ParserSpecRejectingReviewer(),
        seed_candidates=(candidate,),
        validator=validate_template_runtime_compatibility,
    ).generate(
        job_id="job-spec-conflict",
        spec=_spec(),
        sample_set=SampleSet((_sample(),)),
    )

    assert outcome.passed
    assert outcome.stop_reason == "human_review_required:ntc_parser_spec_revision"
    assert outcome.model_calls == 0
    assert outcome.candidate == candidate


def test_ntc_upgrade_regression_reports_unchanged_evidence():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )
    baseline = freeze_ntc_resolution(resolutions[0])

    result = compare_ntc_resolution(baseline, resolutions[0])

    assert result["status"] == "unchanged"
    assert result["breaking"] is False
    assert result["requires_human_review"] is False
    assert result["automatic_replacement_allowed"] is False
    assert result["differences"] == []


def test_ntc_upgrade_regression_separates_metadata_from_output_change():
    resolver = NtcTemplateResolver()
    _decision, baseline_resolutions = resolver.resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )
    current = baseline_resolutions[0]
    assert current.candidate is not None
    old_provenance = dict(current.candidate.provenance or {})
    old_provenance["package_version"] = "8.0.0"
    old_candidate = replace(current.candidate, provenance=old_provenance)
    baseline = freeze_ntc_resolution(replace(current, candidate=old_candidate))

    metadata_result = compare_ntc_resolution(baseline, current)

    assert metadata_result["status"] == "metadata_changed"
    assert metadata_result["breaking"] is False
    assert metadata_result["requires_human_review"] is True

    _decision, changed_resolutions = resolver.resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample("401 Po401 down\n"),),
    )
    output_result = compare_ntc_resolution(
        freeze_ntc_resolution(current),
        changed_resolutions[0],
    )

    assert output_result["status"] == "output_changed"
    assert output_result["breaking"] is True
    assert any(
        item["field"].endswith("records_sha256")
        for item in output_result["differences"]
    )


def test_ntc_upgrade_regression_fails_closed_when_template_disappears():
    resolver = NtcTemplateResolver()
    _decision, resolutions = resolver.resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )
    baseline = freeze_ntc_resolution(resolutions[0])
    _decision, missing = resolver.resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show command that does not exist",
        samples=(_sample(),),
    )

    result = compare_ntc_resolution(baseline, missing[0])

    assert result["status"] == "unavailable"
    assert result["breaking"] is True
    assert result["automatic_replacement_allowed"] is False


def test_ntc_upgrade_cli_reports_unavailable_when_platform_mapping_disappears():
    resolver = NtcTemplateResolver()
    _decision, resolutions = resolver.resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )
    baseline = freeze_ntc_resolution(resolutions[0])
    _decision, unmapped = resolver.resolve(
        device={"vendor": "unmapped-vendor"},
        command="show vpc",
        samples=(_sample(),),
    )

    current = _select_resolution(
        unmapped,
        expected_platform="cisco_nxos",
    )
    result = compare_ntc_resolution(baseline, current)

    assert current.platform == "cisco_nxos"
    assert current.status == "not_found"
    assert result["status"] == "unavailable"
    assert result["breaking"] is True
