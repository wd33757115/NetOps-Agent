from __future__ import annotations

from dataclasses import dataclass

from src.core.parser.generation_samples import (
    compact_parser_sample,
    partition_parser_samples,
    render_sanitized_training_samples,
)
from src.core.parser.structure_profiler import profile_cli_structure
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    build_textfsm_system_prompt,
    dialect_hash,
    lint_textfsm_template,
)


def test_dialect_prompt_is_version_bound_and_hashed():
    prompt = build_textfsm_system_prompt()
    assert "textfsm==2.1.0" in prompt
    assert "Continue.Record" in prompt
    assert "StateName.Record" in prompt
    assert dialect_hash() in prompt


def test_allowed_dialect_compiles_with_real_runtime():
    template = """Value Filldown HOST (\\S+)
Value INTERFACE (\\S+)
Value STATUS (\\S+)

Start
  ^Host: ${HOST}
  ^BEGIN -> Detail

Detail
  ^${INTERFACE}\\s+${STATUS}$$ -> Continue.Record
  ^END -> Record Start

EOF
"""
    assert lint_textfsm_template(template) == ()


def test_required_is_rejected_to_preserve_incomplete_entity_diagnostics():
    template = """Value Required ITEM (\\S+)

Start
  ^${ITEM} -> Record
"""
    violations = lint_textfsm_template(template)
    assert any(item.code == "unsupported_platform_construct" for item in violations)


def test_compiler_list_values_are_supported_by_platform_dialect():
    template = """Value ITEM (\\S+)
Value List METRIC (-?\\d+(?:\\.\\d+)?)

Start
  ^${ITEM}\\s+${METRIC} -> Record
"""
    assert lint_textfsm_template(template) == ()


def test_hallucinated_actions_are_rejected_with_stable_codes():
    templates = (
        "Value X (\\S+)\n\nStart\n  ^${X} -> Set X value\n",
        "Value X (\\S+)\n\nStart\n  ^${X} -> Other.Record\n",
        "Value X (\\S+)\n\nStart\n  ^${X} -> Continue Other\n",
    )
    for template in templates:
        violations = lint_textfsm_template(template)
        assert any(item.code == "unsupported_platform_construct" for item in violations)


def test_filldown_without_explicit_eof_is_rejected():
    template = """Value Filldown X (\\S+)

Start
  ^${X} -> Record
"""
    assert any(item.code == "implicit_eof_record" for item in lint_textfsm_template(template))


@dataclass(frozen=True)
class Sample:
    output: str
    device_id: str
    file_path: str
    prompt_output: str | None = None


def test_prompt_samples_are_partitioned_and_never_expose_identity_or_secret():
    samples = (
        Sample("Gi1/0/1 up 10.0.0.1 password=hunter2", "CORE-REAL", "C:/secret/a.txt"),
        Sample("Gi1/0/2 down 10.0.0.2", "EDGE-REAL", "C:/secret/b.txt"),
        Sample("Gi1/0/3 up 10.0.0.3", "WAN-REAL", "C:/secret/c.txt"),
    )
    partition = partition_parser_samples(samples, max_train_samples=1)
    rendered = render_sanitized_training_samples(samples, partition.train)

    assert partition.train
    assert partition.holdout
    assert {item.sample_id for item in partition.train}.isdisjoint(
        {item.sample_id for item in partition.holdout}
    )
    assert "CORE-REAL" not in rendered
    assert "C:/secret" not in rendered
    assert "hunter2" not in rendered
    assert "10.0.0." not in rendered


def test_exact_duplicate_outputs_are_one_validation_sample_with_auditable_counts():
    samples = (
        Sample("Gi1/0/1 up", "SW1", "C:/q1/sw1.txt"),
        Sample("Gi1/0/1 up", "SW1", "C:/q2/sw1.txt"),
        Sample("Gi1/0/2 down", "SW2", "C:/q1/sw2.txt"),
        Sample("Gi1/0/2 down", "SW2", "C:/q2/sw2.txt"),
        Sample("Command execution failed", "SW3", "C:/q1/sw3.txt"),
        Sample("Command execution failed", "SW3", "C:/q2/sw3.txt"),
    )

    partition = partition_parser_samples(samples, max_train_samples=1)
    positive = (*partition.train, *partition.holdout)

    assert partition.source_sample_count == 6
    assert partition.unique_sample_count == 3
    assert partition.duplicate_sample_count == 3
    assert len(positive) == 2
    assert len(partition.negative) == 1
    assert len({item.content_sha256 for item in positive}) == 2
    assert {item.sample_id for item in partition.train}.isdisjoint(
        {item.sample_id for item in partition.holdout}
    )
    assert partition.to_dict()["source_sample_count"] == 6
    assert partition.to_dict()["unique_sample_count"] == 3
    assert partition.to_dict()["duplicate_sample_count"] == 3


def test_structure_profiler_is_vendor_neutral():
    table = """Port  Status  Speed
Gi1/0/1  up  1000
Gi1/0/2  down  1000
"""
    block = """Item A:
  State: up
Item B:
  State: down
"""
    assert profile_cli_structure(table).layout_kind == "table"
    assert profile_cli_structure(block).layout_kind == "repeated_block"
    assert DEFAULT_TEXTFSM_DIALECT.runtime_version == "2.1.0"


def test_prompt_compaction_keeps_each_repeated_block_shape():
    blocks = []
    for index in range(80):
        if index % 3 == 0:
            body = "State: unavailable"
        elif index % 3 == 1:
            body = "Metric  Current  Limit\nrx      -10      -17"
        else:
            body = "Metric  Current  Limit\nrx      N/A      -17"
        blocks.append(f"Item {index}:\n{body}")
    raw = "\n".join(blocks)

    compacted = compact_parser_sample(raw, max_lines=30, max_chars=2_000)

    assert len(compacted.splitlines()) <= 30
    assert "State: unavailable" in compacted
    assert "rx      -10" in compacted
    assert "rx      N/A" in compacted
    assert len(compacted) < len(raw)
