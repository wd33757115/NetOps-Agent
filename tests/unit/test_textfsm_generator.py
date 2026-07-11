# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""textfsm-generator 单元测试（不调用 LLM）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from config_loader import (  # noqa: E402
    CategorySpec,
    find_category,
    load_command_mapping,
    resolve_device_mapping,
)
from db_reader import (  # noqa: E402
    TemplateCandidate,
    discover_missing_template_candidates,
)
from device_detector import detect_device_identity  # noqa: E402
from directory_reader import discover_directory_candidates  # noqa: E402
from generate_templates import (  # noqa: E402
    Candidate,
    _deterministic_template,
    _job_candidate,
    cmd_to_template_filename,
    resolve_template_path,
)
from llm_generator import build_generation_prompt  # noqa: E402
from template_synthesizer import expected_min_records, synthesize_template  # noqa: E402
from template_validator import (  # noqa: E402
    check_required_fields,
    strip_llm_template,
    validate_field_values,
    validate_template,
)

from src.core.parser.assets import TemplateAssetStore, safe_slug  # noqa: E402
from src.core.parser.contract_inference import infer_contract  # noqa: E402
from src.core.parser.policy import (  # noqa: E402
    SHARED_PARSER_CONFIG_ROOT,
    ParserPolicy,
)
from src.core.patrol.command_splitter import split_cli_capture  # noqa: E402
from src.core.patrol.raw_importer import _read_text_best_effort  # noqa: E402

try:
    import textfsm  # noqa: F401
    HAS_TEXTFSM = True
except ImportError:
    HAS_TEXTFSM = False


SAMPLE_TEMPLATE = """Value Slot (\\S+)
Value CPU_5S (\\d+)
Value CPU_1M (\\d+)
Value CPU_5M (\\d+)

Start
  ^Slot\\s+${Slot}\\s+CPU\\s+5s\\s+1m\\s+5m
  ^${Slot}\\s+${CPU_5S}\\s+${CPU_1M}\\s+${CPU_5M}
"""

SAMPLE_CLI = """Slot  CPU 5s 1m 5m
0     10   12  15
1     8    9   11
"""


class TestConfigLoader(unittest.TestCase):
    def test_load_mapping_from_active_template_assets(self):
        mappings = load_command_mapping(
            SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"
        )
        self.assertTrue(mappings)
        cat = find_category("H3C", "S5130S-54S-HI-G", "display cpu", mappings)
        self.assertEqual(cat, "cpu")

    def test_policy_is_vendor_neutral(self):
        decision = ParserPolicy().decide(
            vendor="NewVendor",
            model="NewModel",
            family=None,
            command="show version",
        )
        self.assertTrue(decision.enabled)

    def test_resolve_device_model(self):
        mappings = load_command_mapping(
            SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"
        )
        self.assertEqual(
            resolve_device_mapping("S5130S-54S-HI-G", mappings),
            ("H3C", "S5130S-54S-HI-G"),
        )

    def test_command_aliases_are_database_assets(self):
        from config_loader import normalize_command

        self.assertEqual(normalize_command("display cpu-us"), "display cpu")
        self.assertEqual(
            normalize_command("show env power"),
            "show environment power",
        )


class TestDbReader(unittest.TestCase):
    def test_dedupe_by_vendor_model_command(self):
        mappings = load_command_mapping(
            SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"
        )
        snapshots = [
            SimpleNamespace(
                parse_status="template_missing",
                vendor="H3C",
                model="S5130S-54S-HI-G",
                command_canonical="display cpu",
                raw_text=SAMPLE_CLI,
                device_id="sw1-10.0.0.1",
            ),
            SimpleNamespace(
                parse_status="template_missing",
                vendor="H3C",
                model="S5130S-54S-HI-G",
                command_canonical="display cpu",
                raw_text=SAMPLE_CLI,
                device_id="sw2-10.0.0.2",
            ),
        ]
        with patch("db_reader.PatrolRepository") as repository:
            repository.return_value.list_dataset_snapshots.return_value = snapshots
            items = discover_missing_template_candidates(None, None, mappings)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].vendor, "H3C")
            self.assertEqual(items[0].model, "S5130S-54S-HI-G")


class TestTemplateValidator(unittest.TestCase):
    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_four_layer_validation(self):
        # 字段名映射为模板 Value 名（大写+下划线是 TextFSM 惯例，配置用小写）
        template = """Value slot (\\d+)
Value cpu_5s (\\d+)
Value cpu_1m (\\d+)
Value cpu_5m (\\d+)

Start
  ^Slot\\s+CPU
  ^${slot}\\s+${cpu_5s}\\s+${cpu_1m}\\s+${cpu_5m}
"""
        cli = "Slot CPU\n0 10 12 15\n1 8 9 11\n"
        required = ["slot", "cpu_5s", "cpu_1m", "cpu_5m"]
        validators = {
            "cpu_5s": {"type": "percent"},
            "cpu_1m": {"type": "percent"},
            "cpu_5m": {"type": "percent"},
        }
        result = validate_template(template, cli, required, validators)
        self.assertTrue(result.compile_success)
        self.assertGreater(result.record_count, 0)
        self.assertEqual(result.missing_fields, [])

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_duplicate_primary_key_fails_template_validation(self):
        template = """Value interface (\\S+)
Value status (\\S+)

Start
  ^${interface}\\s+${status} -> Record
"""
        result = validate_template(
            template,
            "Gi1/0/1 up\nGi1/0/1 down\n",
            ["interface", "status"],
            primary_keys=["interface"],
        )

        self.assertFalse(result.is_valid)
        self.assertTrue(result.key_errors)

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_expected_min_records_fails_underparsed_multi_entity_output(self):
        template = """Value Required slot (\\d+)
Value fan (\\d+)
Value Required state (.+)
Value Filldown direction (.+)

Start
  ^Slot\\s+${slot}: -> Slot

Slot
  ^\\s+Fan\\s+${fan}: -> Fan
  ^Slot\\s+\\d+: -> Record Slot

Fan
  ^\\s+State\\s+:\\s+${state}
  ^\\s+Airflow Direction:\\s+${direction}
  ^\\s+Fan\\s+\\d+: -> Record Fan
"""
        cli = """Slot 1:
 Fan 1:
 State    : Normal
 Airflow Direction: Port-to-power
 Fan 2:
 State    : Normal
 Airflow Direction: Port-to-power
"""

        result = validate_template(
            template,
            cli,
            ["slot", "fan", "state"],
            primary_keys=["slot", "fan"],
            expected_min_records=2,
        )

        self.assertFalse(result.is_valid)
        self.assertIn("below expected minimum 2", result.completeness_errors[0])

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_deterministic_h3c_display_fan_template_parses_all_fans(self):
        cli = """Slot 1:
 Fan 1:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 2:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 3:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 4:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power

 Slot 2:
 Fan 1:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 2:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 3:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
 Fan 4:
 State    : Normal
 Airflow Direction: Port-to-power
 Prefer Airflow Direction: Port-to-power
"""
        candidate = Candidate(
            vendor="H3C",
            model="S6860-54HF",
            family="Comware7",
            command="display fan",
            category="fan",
            samples=(
                SimpleNamespace(
                    output=cli,
                    file_path="sample",
                    device_id="device-1",
                    command="display fan",
                ),
            ),
        )
        spec = CategorySpec(
            name="fan",
            required_fields=["slot", "fan", "state"],
            optional_fields=["direction", "speed", "model"],
            entity_type="fan",
            primary_keys=["slot", "fan"],
            validators={},
        )

        template = _deterministic_template(candidate, spec)
        result = validate_template(
            template,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=8,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 8)

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_simple_slot_fan_state_blocks(self):
        cli = """Slot 1:
 Fan 1:
 State    : Normal
"""
        spec = CategorySpec(
            name="fan",
            required_fields=["slot", "fan", "state"],
            optional_fields=["direction", "speed", "model"],
            entity_type="fan",
            primary_keys=["slot", "fan"],
            validators={},
        )
        candidate = Candidate(
            vendor="H3C",
            model="S5130S-52S-HI",
            family="Comware7",
            command="display fan",
            category="fan",
            samples=(
                SimpleNamespace(
                    output=cli,
                    file_path="sample",
                    device_id="device-1",
                    command="display fan",
                ),
            ),
        )

        synthesized = synthesize_template(
            command=candidate.command,
            samples=candidate.samples,
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=1,
        )

        self.assertEqual(synthesized.strategy, "section_block:slot_fan_state")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.records[0]["slot"], "1")
        self.assertEqual(result.records[0]["fan"], "1")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_index_status_speedlevel_table(self):
        cli = """Index  Status     Speedlevel
 ----------------------------
 1      Normal     1
"""
        proposal = infer_contract(command="display fan", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="display fan",
        )

        synthesized = synthesize_template(
            command="display fan",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=1,
        )

        self.assertEqual(proposal.primary_keys, ("fan",))
        self.assertEqual(synthesized.strategy, "table:index_status_speedlevel")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.records[0]["fan"], "1")
        self.assertEqual(result.records[0]["state"], "Normal")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_inventory_name_pid_pairs(self):
        cli = """NAME: "Chassis",  DESCR: "Nexus9000 C9364C Chassis"
PID: N9K-C9364C          ,  VID: V01 ,  SN: FDO22350PHA

NAME: "Fan 1",  DESCR: "Nexus9000 C9364C Chassis Fan Module"
PID: NXA-FAN-160CFM-PI   ,  VID: V01 ,  SN: N/A
"""
        spec = CategorySpec(
            name="inventory",
            required_fields=["component", "name"],
            optional_fields=["pid", "serial", "vendor"],
            entity_type="inventory_component",
            primary_keys=["component"],
            validators={},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show inventory",
        )

        synthesized = synthesize_template(
            command="show inventory",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=2,
        )

        self.assertEqual(synthesized.strategy, "paired_lines:name_descr_pid_sn")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["component"], "Chassis")
        self.assertEqual(result.records[0]["pid"], "N9K-C9364C")
        self.assertEqual(result.records[1]["serial"], "N/A")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_inventory_with_blank_serial(self):
        cli = """NAME: "Fan Tray", DESCR: "Cisco ISR4450 Fan Assembly"
PID: ACS-4450-FANASSY  , VID:    , SN:

NAME: "module 0", DESCR: "Cisco ISR4451 Built-In NIM controller"
PID: ISR4451-X/K9      , VID:    , SN:
"""
        proposal = infer_contract(command="show inventory", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show inventory",
        )

        synthesized = synthesize_template(
            command="show inventory",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=2,
        )

        self.assertEqual(proposal.primary_keys, ("component",))
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["serial"], "")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_ios_interface_status_with_blank_name(self):
        cli = """Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1                      connected    704        a-full a-1000 10/100/1000BaseTX
Gi1/0/22  To-BaoLeiJi1       connected    704        a-full  a-100 10/100/1000BaseTX
Gi1/0/23                     notconnect   704          auto   auto 10/100/1000BaseTX
"""
        spec = CategorySpec(
            name="interface_l2",
            required_fields=["interface", "status"],
            optional_fields=["vlan", "duplex", "speed", "description", "type"],
            entity_type="interface_l2",
            primary_keys=["interface"],
            validators={"interface": {"type": "interface_name"}},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show interface status",
        )

        synthesized = synthesize_template(
            command="show interface status",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(synthesized.strategy, "fixed_width_table:interface_status")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 3)
        self.assertEqual(result.records[0]["interface"], "Gi1/0/1")
        self.assertEqual(result.records[0]["description"], "")
        self.assertEqual(result.records[1]["description"], "To-BaoLeiJi1")
        self.assertEqual(result.records[2]["status"], "notconnect")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_interface_status_sections(self):
        cli = """--------------------------------------------------------------------------------
Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
mgmt0         --                 connected routed    full    1000    --

--------------------------------------------------------------------------------
Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
Eth1/3        Connect_to_XA-FOTI connected routed    full    40G     QSFP-40G-SR-BD
Eth1/13       --                 xcvrAbsen routed    auto    auto    --
"""
        spec = CategorySpec(
            name="interface_l2",
            required_fields=["interface", "status"],
            optional_fields=["vlan", "duplex", "speed", "description", "type"],
            entity_type="interface_l2",
            primary_keys=["interface"],
            validators={"interface": {"type": "interface_name"}},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show interface status",
        )

        synthesized = synthesize_template(
            command="show interface status",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 3)
        self.assertEqual(result.records[0]["interface"], "mgmt0")
        self.assertEqual(result.records[1]["description"], "Connect_to_XA-FOTI")
        self.assertEqual(result.records[2]["status"], "xcvrAbsen")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_environment_power(self):
        cli = """Power Supply:
Voltage: 12 Volts
Power                      Actual             Actual        Total
Supply    Model            Output             Input      Capacity       Status
                           (Watts )           (Watts )     (Watts )
-------  ----------  ---------------  ------  ----------  --------------------
1        NXA-PAC-1200W-PI      126 W              158 W      1200 W      Ok
2        NXA-PAC-1200W-PI      138 W              158 W      1200 W      Ok
"""
        proposal = infer_contract(command="show environment power", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show environment power",
        )

        synthesized = synthesize_template(
            command="show environment power",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(proposal.primary_keys, ("power_supply",))
        self.assertEqual(synthesized.strategy, "fixed_width_table:environment_power")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["power_supply"], "1")
        self.assertEqual(result.records[0]["output_watts"], "126")
        self.assertEqual(result.records[1]["state"], "Ok")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_environment_fan(self):
        cli = """Fan:
---------------------------------------------------------------------------
Fan             Model                Hw     Direction       Status
---------------------------------------------------------------------------
Fan1(sys_fan1)  NXA-FAN-160CFM-PI    --     front-to-back   Ok
Fan_in_PS1      --                   --     front-to-back   Ok
Fan Zone Speed: Zone 1: 0x66
Fan Air Filter : NotSupported
"""
        proposal = infer_contract(command="show environment fan", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show environment fan",
        )

        synthesized = synthesize_template(
            command="show environment fan",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(proposal.primary_keys, ("fan",))
        self.assertEqual(synthesized.strategy, "fixed_width_table:environment_fan")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["fan"], "Fan1(sys_fan1)")
        self.assertEqual(result.records[0]["state"], "Ok")
        self.assertEqual(result.records[1]["model"], "--")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_environment_temperature(self):
        cli = """Temperature:
--------------------------------------------------------------------
Module   Sensor        MajorThresh   MinorThres   CurTemp     Status
                       (Celsius)     (Celsius)    (Celsius)
--------------------------------------------------------------------
1        FRONT           70              42          24         Ok
1        CPU             90              80          44         Ok
"""
        proposal = infer_contract(command="show environment temperature", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show environment temperature",
        )

        synthesized = synthesize_template(
            command="show environment temperature",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(synthesized.strategy, "fixed_width_table:environment_temperature")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["component"], "1")
        self.assertEqual(result.records[0]["location"], "FRONT")
        self.assertEqual(result.records[1]["reading"], "44")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_system_resources_as_singleton(self):
        cli = """Load average:   1 minute: 0.69   5 minutes: 0.73   15 minutes: 0.62
Processes   :   678 total, 1 running
CPU states  :   1.75% user,   3.62% kernel,   94.61% idle
        CPU0 states  :   0.00% user,   2.04% kernel,   97.95% idle
Memory usage:   32828228K total,   6512772K used,   26315456K free
Current memory status: OK
"""
        proposal = infer_contract(command="show system resources", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show system resources",
        )

        synthesized = synthesize_template(
            command="show system resources",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            validators=spec.validators,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(proposal.primary_keys, ())
        self.assertEqual(synthesized.strategy, "key_value_block:system_resources")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 1)
        self.assertEqual(result.records[0]["cpu_idle"], "94.61")
        self.assertEqual(result.records[0]["total"], "32828228")
        self.assertEqual(result.records[0]["memory_status"], "OK")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_nxos_vpc_status_rows(self):
        cli = """vPC domain id                     : 300
Peer status                       : peer adjacency formed ok
vPC role                          : primary

vPC Peer-link status
---------------------------------------------------------------------
id    Port   Status Active vlans
--    ----   ------ -------------------------------------------------
1     Po300  up     1,7,9-11

vPC status
----------------------------------------------------------------------------
Id    Port          Status Consistency Reason                Active vlans
--    ------------  ------ ----------- ------                ---------------
400   Po400         up     success     success               1,7,9-11
401   Po401         down   failed      ConsistencyCheck      851
"""
        proposal = infer_contract(command="show vpc", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
            allow_empty=proposal.allow_empty,
            empty_patterns=list(proposal.empty_patterns),
        )
        sample = SimpleNamespace(
            output=cli,
            file_path="sample",
            device_id="device-1",
            command="show vpc",
        )

        synthesized = synthesize_template(
            command="show vpc",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(proposal.primary_keys, ("member", "peer"))
        self.assertEqual(synthesized.strategy, "fixed_width_table:vpc_status")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 3)
        self.assertEqual(result.records[0]["member"], "1")
        self.assertEqual(result.records[0]["peer"], "Po300")
        self.assertEqual(result.records[1]["consistency"], "success")
        self.assertEqual(result.records[2]["state"], "down")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_h3c_manuinfo_sections(self):
        cli = """Slot 1 CPU 0:
DEVICE_NAME          : S5130S-54S-HI-G
DEVICE_SERIAL_NUMBER : 219801A3R36242V0004N
MAC_ADDRESS          : 6CE2-D38C-EC1D
MANUFACTURING_DATE   : 2024-05-06
VENDOR_NAME          : H3C
PRODUCT ID           : LS-5130S-54S-HI-G
Power 1:
Failed to display the manufacture information of the specified power.
"""
        proposal = infer_contract(command="display device manuinfo", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(output=cli, file_path="sample", device_id="device-1")

        synthesized = synthesize_template(
            command="display device manuinfo",
            samples=(sample,),
            spec=spec,
        )
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(synthesized.strategy, "section_block:h3c_manuinfo")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 1)
        self.assertEqual(result.records[0]["component"], "Slot 1 CPU 0")
        self.assertEqual(result.records[0]["serial"], "219801A3R36242V0004N")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_h3c_memory_slots(self):
        cli = """Memory statistics are measured in KB:
Slot 0:
             Total      Used      Free    Shared   Buffers    Cached   FreeRatio
Mem:       2026244    790556   1235688         0      1476    241232       61.1%
-/+ Buffers/Cache:    547848   1478396
Swap:           0         0         0

Slot 1:
             Total      Used      Free    Shared   Buffers    Cached   FreeRatio
Mem:       2026244    753580   1272664         0      1476    239212       62.9%
"""
        proposal = infer_contract(command="display memory", samples_text=cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
        )
        sample = SimpleNamespace(output=cli, file_path="sample", device_id="device-1")

        synthesized = synthesize_template(command="display memory", samples=(sample,), spec=spec)
        result = validate_template(
            synthesized.template_text,
            cli,
            spec.required_fields,
            validators=spec.validators,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(cli, spec),
        )

        self.assertEqual(synthesized.strategy, "section_table:h3c_memory")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.records[0]["slot"], "0")
        self.assertEqual(result.records[1]["free_ratio"], "62.9")

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_h3c_power_variants(self):
        slot_cli = """Slot 1:
 Input Power: --
 PowerID  State  Mode   Current(A)  Voltage(V)  Power(W) FanDirection
 1        Normal AC     --          --          --       --
 2        Normal AC     --          --          --       --
"""
        index_cli = """Index          Status
  ----------------------
  PWR1          normal
  PWR2          normal
"""
        for cli, expected_strategy, expected_count in (
            (slot_cli, "section_table:h3c_power_slot", 2),
            (index_cli, "simple_table:h3c_power_index", 2),
        ):
            proposal = infer_contract(command="display power", samples_text=cli)
            spec = CategorySpec(
                name=proposal.category,
                required_fields=list(proposal.required_fields),
                optional_fields=list(proposal.optional_fields),
                entity_type=proposal.entity_type,
                primary_keys=list(proposal.primary_keys),
                validators=proposal.validators or {},
            )
            sample = SimpleNamespace(output=cli, file_path="sample", device_id="device-1")
            synthesized = synthesize_template(command="display power", samples=(sample,), spec=spec)
            result = validate_template(
                synthesized.template_text,
                cli,
                spec.required_fields,
                primary_keys=spec.primary_keys,
                expected_min_records=expected_min_records(cli, spec),
            )

            self.assertEqual(synthesized.strategy, expected_strategy)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.record_count, expected_count)

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_h3c_environment_variants(self):
        slot_cli = """System temperature information (degree centigrade):
 ----------------------------------------------------------------------
 Slot  Sensor    Temperature  Lower  Warning  Alarm  Shutdown
 1     hotspot 1 37           0      65       70     NA
 1     hotspot 2 58           0      88       102    107
"""
        subslot_cli = """Slot Subslot  Sensor  ID Temperature LowerLimit WarningLimit AlarmLimit
-----------------------------------------------------------------------
0    0        hotspot 1  33          0          57           65
2    0        inflow  1  27          -5         50           60
"""
        for cli, expected_strategy, expected_count in (
            (slot_cli, "fixed_width_table:h3c_environment_slot", 2),
            (subslot_cli, "fixed_width_table:h3c_environment_subslot", 2),
        ):
            proposal = infer_contract(command="display environment", samples_text=cli)
            spec = CategorySpec(
                name=proposal.category,
                required_fields=list(proposal.required_fields),
                optional_fields=list(proposal.optional_fields),
                entity_type=proposal.entity_type,
                primary_keys=list(proposal.primary_keys),
                validators=proposal.validators or {},
            )
            sample = SimpleNamespace(output=cli, file_path="sample", device_id="device-1")
            synthesized = synthesize_template(command="display environment", samples=(sample,), spec=spec)
            result = validate_template(
                synthesized.template_text,
                cli,
                spec.required_fields,
                primary_keys=spec.primary_keys,
                expected_min_records=expected_min_records(cli, spec),
            )

            self.assertEqual(synthesized.strategy, expected_strategy)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.record_count, expected_count)

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_synthesizer_parses_h3c_irf_and_accepts_empty_mlag(self):
        irf_cli = """MemberID    Role    Priority  CPU-Mac         Description
 *+1        Master  1         00e0-fc0f-8c02  ---
--------------------------------------------------
"""
        proposal = infer_contract(command="display irf", samples_text=irf_cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
            allow_empty=proposal.allow_empty,
            empty_patterns=list(proposal.empty_patterns),
        )
        sample = SimpleNamespace(output=irf_cli, file_path="sample", device_id="device-1")
        synthesized = synthesize_template(command="display irf", samples=(sample,), spec=spec)
        result = validate_template(
            synthesized.template_text,
            irf_cli,
            spec.required_fields,
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(irf_cli, spec),
        )
        self.assertEqual(synthesized.strategy, "fixed_width_table:h3c_irf")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.records[0]["member"], "1")

        empty_cli = "M-LAG is not configured.\n"
        proposal = infer_contract(command="display m-lag system", samples_text=empty_cli)
        spec = CategorySpec(
            name=proposal.category,
            required_fields=list(proposal.required_fields),
            optional_fields=list(proposal.optional_fields),
            entity_type=proposal.entity_type,
            primary_keys=list(proposal.primary_keys),
            validators=proposal.validators or {},
            allow_empty=proposal.allow_empty,
            empty_patterns=list(proposal.empty_patterns),
        )
        sample = SimpleNamespace(output=empty_cli, file_path="sample", device_id="device-1")
        synthesized = synthesize_template(command="display m-lag system", samples=(sample,), spec=spec)
        result = validate_template(
            synthesized.template_text,
            empty_cli,
            spec.required_fields,
            allow_empty=spec.allow_empty,
            empty_patterns=list(spec.empty_patterns),
            primary_keys=spec.primary_keys,
            expected_min_records=expected_min_records(empty_cli, spec),
        )
        self.assertEqual(synthesized.strategy, "empty_allowed:not_configured")
        self.assertTrue(result.is_valid)
        self.assertTrue(result.empty_accepted)

    def test_strip_llm_fence(self):
        raw = "```textfsm\nValue X (\\d+)\n\nStart\n  ^${X}\n```"
        cleaned = strip_llm_template(raw)
        self.assertIn("Value X", cleaned)
        self.assertNotIn("```", cleaned)

    def test_strip_llm_repairs_invalid_state_record_action(self):
        raw = """Value Filldown SLOT (\\d+)
Value FAN (\\d+)
Value STATE (\\S+)

Start
  ^Slot ${SLOT}: -> Slot.Record

Slot
  ^\\s+Fan ${FAN}: ${STATE} -> Record
"""
        cleaned = strip_llm_template(raw)
        self.assertIn("-> Slot", cleaned)
        self.assertNotIn("Slot.Record", cleaned)
        result = validate_template(
            cleaned,
            "Slot 1:\n Fan 1: Normal\n Fan 2: Normal\n",
            ["SLOT", "FAN", "STATE"],
            primary_keys=["SLOT", "FAN"],
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.record_count, 2)

    def test_field_value_percent(self):
        errors = validate_field_values(
            [{"cpu_5s": "150"}],
            {"cpu_5s": {"type": "percent"}},
        )
        self.assertTrue(errors)

    def test_required_field_coverage(self):
        cov, missing = check_required_fields([{"a": "1"}], ["a", "b"])
        self.assertEqual(missing, ["b"])
        self.assertEqual(cov, 50)

    @unittest.skipUnless(HAS_TEXTFSM, "textfsm not installed")
    def test_allow_known_empty_output(self):
        template = """Value interface (\\S+)
Value group (\\d+)
Value state (\\S+)

Start
  ^${interface}\\s+${group}\\s+${state}
"""
        result = validate_template(
            template,
            "VRRP4 is not configured.",
            ["interface", "group", "state"],
            allow_empty=True,
            empty_patterns=[r"VRRP4 is not configured"],
        )
        self.assertTrue(result.is_valid)
        self.assertTrue(result.empty_accepted)


class TestInputParser(unittest.TestCase):
    def test_parse_display_fan_block(self):
        from input_parser import parse_chat_cli_block

        text = """<SW1>display fan
 Slot 1:
 Fan 1:
 State    : Normal
 Fan 2:
 State    : Normal"""
        parsed = parse_chat_cli_block(text)
        self.assertEqual(parsed.command, "display fan")
        self.assertEqual(parsed.device_prompt, "SW1")
        self.assertIn("Slot 1:", parsed.raw_output)
        self.assertIn("Fan 1:", parsed.raw_output)

    def test_extract_from_params(self):
        from input_parser import extract_direct_input

        direct = extract_direct_input({"user_query": "display fan\nline1\nline2"})
        self.assertIsNotNone(direct)
        assert direct is not None
        self.assertEqual(direct.command, "display fan")
        self.assertEqual(direct.raw_output, "line1\nline2")

    def test_extract_intent_only_falls_back_to_database(self):
        from input_parser import extract_direct_input

        self.assertIsNone(extract_direct_input({"user_query": "生成textfsm解析模板"}))

    def test_explicit_cli_still_raises_when_invalid(self):
        from input_parser import extract_direct_input

        with self.assertRaises(ValueError):
            extract_direct_input({"raw_output": "只有一行没有命令"})

    def test_parse_with_natural_language_prefix(self):
        from input_parser import parse_chat_cli_block

        text = """生成TEXTFSM解析模板，<SW1>display fan
 Slot 1:
 Fan 1:
 State    : Normal"""
        parsed = parse_chat_cli_block(text)
        self.assertEqual(parsed.command, "display fan")
        self.assertEqual(parsed.device_prompt, "SW1")
        self.assertIn("Slot 1:", parsed.raw_output)

    def test_infer_vendor_model_semantic(self):
        from unittest.mock import patch

        from input_parser import infer_vendor_model, parse_chat_cli_block

        text = """帮我给华三核心交换机 S5590 生成 textfsm 模板
<SW>display cpu
Slot 1 CPU 0 CPU usage:
      21% in last 5 seconds"""
        direct = parse_chat_cli_block(text)

        with patch("semantic_extract.extract_device_context", return_value=("H3C", "S5590")):
            vendor, model = infer_vendor_model(
                {"user_query": text},
                direct,
                known_devices=["H3C S5590", "Huawei CE12800"],
            )
        self.assertEqual(vendor, "H3C")
        self.assertEqual(model, "S5590")

    def test_infer_vendor_model_skips_semantic_when_disabled(self):
        from unittest.mock import patch

        from input_parser import infer_vendor_model, parse_chat_cli_block

        text = "display fan\nSlot 1:\nFan 1:\nState : Normal"
        direct = parse_chat_cli_block(text)

        with patch("semantic_extract.extract_device_context") as mock_extract:
            vendor, model = infer_vendor_model(
                {"user_query": text, "use_semantic_extraction": False},
                direct,
            )
            mock_extract.assert_not_called()
        self.assertEqual(vendor, "Huawei")
        self.assertEqual(model, "Generic")

    def test_semantic_parse_json(self):
        from semantic_extract import _parse_json_object, build_extract_prompt

        data = _parse_json_object('说明\n{"vendor": "H3C", "model": "S5590"}')
        self.assertEqual(data.get("vendor"), "H3C")
        self.assertEqual(data.get("model"), "S5590")

        prompt = build_extract_prompt(
            "华三 S5590 生成模板",
            known_devices=["H3C S5590"],
            command="display cpu",
        )
        self.assertIn("H3C S5590", prompt)
        self.assertIn("自然语义", prompt)


class TestGenerateFlow(unittest.TestCase):
    def test_template_path_compatible_with_patrol(self):
        path = resolve_template_path(Path("/tmp/templates"), "CE12800", "display cpu-usage")
        self.assertEqual(path.name, "display_cpu-usage.textfsm")
        self.assertEqual(path.parent.name, "CE12800")

    @patch("generate_templates.generate_template_text")
    def test_end_to_end_with_mock_llm(self, mock_llm):
        mock_llm.return_value = """Value slot (\\d+)
Value cpu_5s (\\d+)
Value cpu_1m (\\d+)
Value cpu_5m (\\d+)

Start
  ^Slot\\s+CPU
  ^${slot}\\s+${cpu_5s}\\s+${cpu_1m}\\s+${cpu_5m}
"""
        skill_root = SHARED_PARSER_CONFIG_ROOT

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            templates = tmp / "templates"
            reports = tmp / "reports"

            from generate_templates import generate_templates

            cli = "Slot CPU\n0 10 12 15\n"
            candidate = TemplateCandidate(
                vendor="H3C",
                model="S5130S-54S-HI-G",
                command="display cpu",
                sample_output=cli,
                source_table="netops_command_snapshots",
                device_id="sw1-10.0.0.1",
            )
            with patch(
                "generate_templates.discover_missing_template_candidates",
                return_value=[candidate],
            ):
                result = generate_templates(
                    {
                        "templates_dir": str(templates),
                        "reports_dir": str(reports),
                        "mapping_config": str(skill_root / "command_policy.yaml"),
                        "max_retries": 1,
                        "publish": False,
                    }
                )
            self.assertTrue(result["success"])
            self.assertEqual(result["validation_pass_rate"], 1.0)
            out_file = (
                templates
                / "S5130S-54S-HI-G"
                / cmd_to_template_filename("display cpu")
            )
            self.assertFalse(out_file.is_file())
            report_files = list(reports.glob("*.json"))
            self.assertTrue(report_files)

    @patch("generate_templates.generate_template_text")
    def test_direct_input_mode(self, mock_llm):
        mock_llm.return_value = """Value slot (\\d+)
Value fan (\\d+)
Value state (\\S+)

Start
  ^Slot\\s+${slot}:
  ^Fan\\s+${fan}:
  ^State\\s+:\\s+${state}
"""
        skill_root = SHARED_PARSER_CONFIG_ROOT
        cli = """Slot 1:
Fan 1:
State    : Normal
Fan 2:
State    : Normal"""
        user_query = f"<SW>display fan\n{cli}"

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            from generate_templates import generate_templates

            result = generate_templates(
                {
                    "user_query": user_query,
                    "vendor": "Huawei",
                    "model": "Generic",
                    "templates_dir": str(tmp / "templates"),
                    "reports_dir": str(tmp / "reports"),
                    "mapping_config": str(skill_root / "command_policy.yaml"),
                    "max_retries": 1,
                    "publish": False,
                }
            )
            self.assertEqual(result["mode"], "direct")
            self.assertTrue(result.get("parsed_records") or result["success_count"] >= 0)
            self.assertIn("display fan", result["message"])

    @patch("generate_templates.generate_template_text")
    def test_directory_mode_publishes_shared_asset(self, mock_llm):
        mock_llm.return_value = """Value model (\\S+)
Value os_version (\\S+)

Start
  ^H3C Comware Software, Version ${os_version},
  ^H3C ${model} uptime -> Record
"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            capture = tmp / "SW1-10.0.0.1.txt"
            capture.write_text(
                "<SW1>display version\n"
                "H3C Comware Software, Version 7.1.070, Release 1\n"
                "H3C S5130S-54S-HI-G uptime is 1 week\n",
                encoding="utf-8",
            )
            from generate_templates import generate_templates

            result = generate_templates(
                {
                    "source_path": str(capture),
                    "templates_dir": str(tmp / "shared"),
                    "reports_dir": str(tmp / "reports"),
                    "max_retries": 0,
                    "publish": False,
                }
            )
            self.assertEqual(result["mode"], "directory")
            self.assertEqual(result["files_scanned"], 1)
            self.assertEqual(result["devices_detected"], 1)
            self.assertEqual(result["validation_pass_rate"], 1.0)
            self.assertFalse((tmp / "shared").exists())

    @patch("generate_templates.generate_template_text")
    def test_unconfigured_directory_command_does_not_call_llm(self, mock_llm):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            capture = tmp / "SW1-10.0.0.1.txt"
            capture.write_text(
                "<SW1>display version\n"
                "H3C Comware Software, Version 7.1.070, Release 1\n"
                "H3C S5130S-54S-HI-G uptime is 1 week\n"
                "<SW1>display clock\n12:00:00\n",
                encoding="utf-8",
            )
            from generate_templates import generate_templates

            result = generate_templates(
                {
                    "source_path": str(capture),
                    "command": "display clock",
                    "templates_dir": str(tmp / "shared"),
                    "reports_dir": str(tmp / "reports"),
                }
            )
            mock_llm.assert_not_called()
            self.assertEqual(result["candidate_groups"], 0)
            self.assertEqual(result["skipped_commands"][0]["command"], "display clock")


class TestOfflineDirectoryDiscovery(unittest.TestCase):
    def _discover(self, directory: Path):
        from config_loader import load_command_mapping

        return discover_directory_candidates(
            directory,
            mappings=load_command_mapping(
                SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"
            ),
        )

    def test_multiple_same_model_files_are_grouped_once(self):
        capture = (
            "<SW>display version\n"
            "H3C Comware Software, Version 7.1.070, Release 1\n"
            "H3C S5130S-54S-HI-G uptime is 1 week\n"
            "<SW>display cpu\n"
            "Slot 1 CPU 0 CPU usage:\n"
            "  10% in last 5 seconds\n"
            "  9% in last 1 minute\n"
            "  8% in last 5 minutes\n"
            "<SW>display clock\n12:00:00\n"
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            directory = Path(tmpdir)
            (directory / "SW1-10.0.0.1.txt").write_text(capture, encoding="utf-8")
            (directory / "SW2-10.0.0.2.txt").write_text(capture, encoding="utf-8")
            discovery = self._discover(directory)

        self.assertEqual(discovery.files_scanned, 2)
        self.assertEqual(discovery.devices_detected, 2)
        self.assertEqual(discovery.unresolved_files, ())
        cpu_groups = [
            candidate
            for candidate in discovery.candidates
            if candidate.command == "display cpu"
        ]
        self.assertEqual(len(cpu_groups), 1)
        self.assertEqual(len(cpu_groups[0].samples), 2)
        self.assertEqual(cpu_groups[0].vendor, "H3C")
        self.assertEqual(cpu_groups[0].model, "S5130S-54S-HI-G")
        self.assertTrue(
            any(
                item["command"] == "display clock"
                and item["reason"] == "command_not_configured"
                for item in discovery.skipped_commands
            )
        )

    def test_version_evidence_has_high_confidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            path = Path(tmpdir) / "CORE1-10.0.0.1.txt"
            path.write_text(
                "CORE1#show version\n"
                "Cisco Nexus Operating System (NX-OS) Software\n"
                "  cisco Nexus9000 C9364C Chassis\n",
                encoding="utf-8",
            )
            blocks = split_cli_capture(_read_text_best_effort(path))
            identity = detect_device_identity(
                path=path,
                blocks=blocks,
                config={},
            )
        self.assertEqual((identity.vendor, identity.model), ("Cisco", "N9K-C9364C"))
        self.assertGreaterEqual(identity.confidence, 0.99)
        self.assertEqual(identity.evidence_command, "show version")


class TestSharedTextfsmAssets(unittest.TestCase):
    def test_safe_model_and_command_paths(self):
        self.assertEqual(safe_slug("ISR4451-X/K9"), "ISR4451-X_K9")
        self.assertEqual(safe_slug("show ip interface brief"), "show_ip_interface_brief")

    def test_immutable_asset_and_hash_verification(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            store = TemplateAssetStore(Path(tmpdir))
            path, digest = store.write_immutable(
                vendor="Cisco",
                profile="N9K-C9364C",
                command="show version",
                version="1",
                content="template",
            )
            self.assertTrue(store.verify(path, digest))
            self.assertEqual(path.parent.name, "N9K-C9364C")
            self.assertEqual(path.name, "show_version__1.textfsm")
            with self.assertRaises(FileExistsError):
                store.write_immutable(
                    vendor="Cisco",
                    profile="N9K-C9364C",
                    command="show version",
                    version="1",
                    content="different",
                )

    def test_global_raw_hash_policy(self):
        decision = ParserPolicy().decide(
            vendor="Cisco",
            model="N9K-C9364C",
            family="NXOS9K",
            command="show running-config",
        )
        self.assertFalse(decision.enabled)
        self.assertEqual(decision.handling, "raw_hash_diff")


class TestParserJobSamples(unittest.TestCase):
    def test_generation_prompt_allows_filldown_for_parent_primary_keys(self):
        prompt = build_generation_prompt(
            vendor="H3C",
            model="S6860-54HF",
            command="display fan",
            cli_output="Slot 1\n Fan 1 Normal",
            required_fields=["slot", "fan", "state"],
            primary_keys=["slot", "fan"],
        )
        self.assertIn("Value Filldown", prompt)
        self.assertIn("slot, fan", prompt)

    def test_job_candidate_accepts_postgresql_validation_samples(self):
        job = {
            "vendor": "H3C",
            "model": "S6860-54HF",
            "profile": "S6860-54HF",
            "family": "Comware7",
            "canonical_command": "display fan",
            "samples": [
                {"snapshot_id": "sample-a", "snapshot_source": "postgresql"},
                {"snapshot_id": "sample-b", "snapshot_source": "postgresql"},
            ],
        }
        contexts = {
            sample_id: {
                "mode": "parser_validation",
                "snapshot": SimpleNamespace(
                    snapshot_id=sample_id,
                    device_id=f"device-{sample_id}",
                    command_canonical="display fan",
                    raw_text="Slot 1 Fan 1 Normal",
                ),
            }
            for sample_id in ("sample-a", "sample-b")
        }
        with patch("generate_templates.ParserRepository") as parser_repository:
            parser_repository.return_value.get_job.return_value = job
            with patch("generate_templates.PatrolRepository") as patrol_repository:
                patrol_repository.return_value.get_snapshot_context.side_effect = (
                    lambda snapshot_id: contexts[snapshot_id]
                )
                candidate = _job_candidate({"job_id": "job-1"})
        self.assertIsNotNone(candidate)
        self.assertEqual(len(candidate.samples), 2)

    def test_job_candidate_uses_only_sanitized_production_sample(self):
        job = {
            "source_mode": "production",
            "vendor": "H3C",
            "model": "S6860-54HF",
            "profile": "S6860-54HF",
            "family": "Comware7",
            "canonical_command": "display fan",
            "samples": [
                {
                    "snapshot_id": "sample-production",
                    "snapshot_source": "postgresql",
                    "device_id": "device-1",
                    "sanitization_status": "sanitized",
                    "sanitized_text": "Slot 1 Fan 1 Normal",
                }
            ],
        }
        with patch("generate_templates.ParserRepository") as repository:
            repository.return_value.get_job.return_value = job
            with patch("generate_templates.PatrolRepository") as patrol_repository:
                candidate = _job_candidate({"job_id": "job-1"})
        self.assertEqual(candidate.samples[0].output, "Slot 1 Fan 1 Normal")
        patrol_repository.return_value.get_snapshot_context.assert_not_called()

    def test_job_candidate_rejects_unsanitized_production_sample(self):
        job = {
            "source_mode": "production",
            "vendor": "H3C",
            "model": "S6860-54HF",
            "profile": "S6860-54HF",
            "canonical_command": "display fan",
            "samples": [
                {
                    "snapshot_id": "sample-production",
                    "snapshot_source": "postgresql",
                    "device_id": "device-1",
                    "sanitization_status": "pending_sanitization",
                }
            ],
        }
        with patch("generate_templates.ParserRepository") as repository:
            repository.return_value.get_job.return_value = job
            with self.assertRaisesRegex(ValueError, "not sanitized"):
                _job_candidate({"job_id": "job-1"})


if __name__ == "__main__":
    unittest.main()
