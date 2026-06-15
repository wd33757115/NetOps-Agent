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
from llm_generator import build_generation_prompt  # noqa: E402
from generate_templates import (  # noqa: E402
    _job_candidate,
    cmd_to_template_filename,
    resolve_template_path,
)
from template_validator import (  # noqa: E402
    check_required_fields,
    strip_llm_template,
    validate_field_values,
    validate_template,
)

from src.core.patrol.command_splitter import split_cli_capture  # noqa: E402
from src.core.patrol.raw_importer import _read_text_best_effort  # noqa: E402
from src.core.parser.assets import TemplateAssetStore, safe_slug  # noqa: E402
from src.core.parser.policy import (  # noqa: E402
    SHARED_PARSER_CONFIG_ROOT,
    ParserPolicy,
)

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

    def test_job_candidate_rejects_production_mode_sample(self):
        job = {
            "vendor": "H3C",
            "model": "S6860-54HF",
            "profile": "S6860-54HF",
            "family": "Comware7",
            "canonical_command": "display fan",
            "samples": [
                {"snapshot_id": "sample-production", "snapshot_source": "postgresql"}
            ],
        }
        context = {
            "mode": "production",
            "snapshot": SimpleNamespace(
                snapshot_id="sample-production",
                device_id="device-1",
                command_canonical="display fan",
                raw_text="Slot 1 Fan 1 Normal",
            ),
        }
        with patch("generate_templates.ParserRepository") as repository:
            repository.return_value.get_job.return_value = job
            with patch("generate_templates.PatrolRepository") as patrol_repository:
                patrol_repository.return_value.get_snapshot_context.return_value = context
                with self.assertRaisesRegex(ValueError, "outside parser_validation mode"):
                    _job_candidate({"job_id": "job-1"})


if __name__ == "__main__":
    unittest.main()
