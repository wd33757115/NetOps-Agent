"""Adding fields to an adapter must leave what it already provides untouched."""

from src.apps.rule_governance.backend.check_items.adapter_fields import (
    base_spec,
    extension_only,
    replay,
    without_values,
)
from src.apps.rule_governance.backend.parser_generation import (
    ParserSample,
    SampleRole,
    TextFSMValidationBackend,
)
from types import SimpleNamespace

CURRENT = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""
EXTENDED = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)
Value WAVELENGTH (\\d+)
Value VENDOR_NOTE (\\S+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER}(\\s+wavelength\\s+${WAVELENGTH})? -> Record
"""
BROKEN = EXTENDED.replace("rx\\s+${RX_POWER}", "rx\\s+-?${RX_POWER}")
SAMPLES = {
    "s1": "Eth1/1 rx -3.20 wavelength 850\nEth1/2 rx -17.96\n",
    "s2": "Eth1/3 rx -2.10 wavelength 1310\n",
}


def test_a_new_field_counts_only_where_the_samples_contain_it():
    result = replay(TextFSMValidationBackend(), CURRENT, EXTENDED, SAMPLES, ["WAVELENGTH", "VENDOR_NOTE"])

    assert result["differences"] == [] and result["record_count"] == 3
    assert result["found"]["WAVELENGTH"] == {"records": 2, "samples": 2, "examples": ["850", "1310"]}
    assert result["found"]["VENDOR_NOTE"]["records"] == 0


def test_a_template_that_moves_an_existing_value_is_reported_record_by_record():
    result = replay(TextFSMValidationBackend(), CURRENT, BROKEN, SAMPLES, ["WAVELENGTH"])

    assert result["differences"] == [
        "样本 s1 第 1 条：RX_POWER 的值变了",
        "样本 s1 第 2 条：RX_POWER 的值变了",
        "样本 s2 第 1 条：RX_POWER 的值变了",
    ]


def test_losing_or_gaining_records_is_a_regression():
    fewer = EXTENDED.replace("(\\s+wavelength\\s+${WAVELENGTH})?", "\\s+wavelength\\s+${WAVELENGTH}")
    result = replay(TextFSMValidationBackend(), CURRENT, fewer, SAMPLES, ["WAVELENGTH"])

    assert result["differences"] == ["样本 s1：记录数由 2 变为 1"]


def _validate(candidate):
    backend = TextFSMValidationBackend()
    item = SimpleNamespace(canonical_contract={"identity_dimensions": ["INTERFACE"], "entity_type": "interface"})
    records = [record for text in SAMPLES.values() for record in backend.parse(CURRENT, text)]
    spec = base_spec(item, CURRENT, records, backend)
    from dataclasses import replace as extend
    from src.apps.rule_governance.backend.parser_generation.spec import FieldSpec
    spec = extend(spec, fields=(*spec.fields, FieldSpec(name="WAVELENGTH", required=False), FieldSpec(name="VENDOR_NOTE", required=False)))
    samples = tuple(ParserSample(sample_id=key, raw_text=text, role=SampleRole.GENERATION, prompt_text=text,
                                 validation_text_override=text) for key, text in SAMPLES.items())
    return extension_only(CURRENT, SAMPLES)(candidate, spec, samples, backend)


def test_a_candidate_that_only_adds_passes_the_guard():
    assert _validate(EXTENDED).passed


def test_rewriting_an_existing_value_is_sent_back_even_when_the_samples_parse_the_same():
    narrowed = EXTENDED.replace("Value RX_POWER (-?\\d+\\.\\d+)", "Value RX_POWER (-?[\\d.]+)")
    assert not replay(TextFSMValidationBackend(), CURRENT, narrowed, SAMPLES, [])["differences"]

    report = _validate(narrowed)

    assert not report.passed
    assert "existing Value definitions must stay exactly as they are" in report.diagnostics[0]
    assert "RX_POWER" in report.diagnostics[0]


def test_values_the_outputs_never_show_are_taken_out_again():
    trimmed = without_values(EXTENDED, ["VENDOR_NOTE"])

    assert "VENDOR_NOTE" not in trimmed and "Value WAVELENGTH" in trimmed
    assert not replay(TextFSMValidationBackend(), EXTENDED, trimmed, SAMPLES, ["WAVELENGTH"])["differences"]
    assert without_values(EXTENDED, ["WAVELENGTH"]) is None
