"""Adding fields to an adapter must leave what it already provides untouched."""

from dataclasses import replace as extend
from types import SimpleNamespace

from src.apps.rule_governance.backend.check_items import adapter_fields
from src.apps.rule_governance.backend.check_items.adapter_fields import (
    added_lines,
    base_spec,
    check_extension,
    drop_values,
    extension_only,
    field_sources,
    replay,
    restore_values,
    rules_lost,
    trial_add_fields,
)
from src.apps.rule_governance.backend.parser_generation import (
    ParserSample,
    SampleRole,
    TemplateCandidate,
    TextFSMValidationBackend,
)
from src.apps.rule_governance.backend.parser_generation.service import ParserModelTurn
from src.apps.rule_governance.backend.parser_generation.spec import FieldSpec

CURRENT = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""
# Adds two Values and one line; every line in force is still there.
EXTENDED = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)
Value WAVELENGTH (\\d+)
Value VENDOR_NOTE (\\S+)

Start
  ^\\S+\\s+rx\\s+\\S+\\s+wavelength\\s+${WAVELENGTH} -> Continue
  ^\\S+\\s+rx\\s+\\S+\\s+note\\s+${VENDOR_NOTE} -> Continue
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""
# Parses the samples the same way, but the rule in force was edited rather than kept.
REWRITTEN = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)
Value WAVELENGTH (\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER}(\\s+wavelength\\s+${WAVELENGTH})? -> Record
"""
BROKEN = EXTENDED.replace("^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record", "^${INTERFACE}\\s+rx\\s+-?${RX_POWER} -> Record")
SAMPLES = {
    "s1": "Eth1/1 rx -3.20 wavelength 850\nEth1/2 rx -17.96\n",
    "s2": "Eth1/3 rx -2.10 wavelength 1310\n",
}


def test_a_new_field_counts_only_where_the_samples_contain_it():
    result = replay(TextFSMValidationBackend(), CURRENT, EXTENDED, SAMPLES, ["WAVELENGTH", "VENDOR_NOTE"])

    assert result["differences"] == [] and result["record_count"] == 3
    assert result["found"]["WAVELENGTH"] == {"records": 2, "samples": 2, "examples": ["850", "1310"]}
    assert result["found"]["VENDOR_NOTE"]["records"] == 0


def test_examples_are_distinct_values_not_the_first_records():
    twice = {"s1": "Eth1/1 rx -3.20 wavelength 850\nEth1/2 rx -1.00 wavelength 850\nEth1/3 rx -2.00 wavelength 1310\n"}

    result = replay(TextFSMValidationBackend(), CURRENT, EXTENDED, twice, ["WAVELENGTH"])

    assert result["found"]["WAVELENGTH"]["examples"] == ["850", "1310"]
    assert result["found"]["WAVELENGTH"]["records"] == 3


def test_a_template_that_moves_an_existing_value_is_reported_record_by_record():
    result = replay(TextFSMValidationBackend(), CURRENT, BROKEN, SAMPLES, ["WAVELENGTH"])

    assert result["differences"] == [
        "样本 s1 第 1 条：RX_POWER 的值变了",
        "样本 s1 第 2 条：RX_POWER 的值变了",
        "样本 s2 第 1 条：RX_POWER 的值变了",
    ]


def test_losing_or_gaining_records_is_a_regression():
    fewer = REWRITTEN.replace("(\\s+wavelength\\s+${WAVELENGTH})?", "\\s+wavelength\\s+${WAVELENGTH}")
    result = replay(TextFSMValidationBackend(), CURRENT, fewer, SAMPLES, ["WAVELENGTH"])

    assert result["differences"] == ["样本 s1：记录数由 2 变为 1"]


def _spec(backend):
    item = SimpleNamespace(canonical_contract={"identity_dimensions": ["INTERFACE"], "entity_type": "interface"})
    records = [record for text in SAMPLES.values() for record in backend.parse(CURRENT, text)]
    spec = base_spec(item, CURRENT, records, backend)
    return extend(spec, fields=(*spec.fields, FieldSpec(name="WAVELENGTH", required=False),
                                FieldSpec(name="VENDOR_NOTE", required=False)))


def _validate(candidate):
    backend = TextFSMValidationBackend()
    samples = tuple(ParserSample(sample_id=key, raw_text=text, role=SampleRole.GENERATION, prompt_text=text,
                                 validation_text_override=text) for key, text in SAMPLES.items())
    return extension_only(CURRENT, SAMPLES)(candidate, _spec(backend), samples, backend)


def test_a_candidate_that_only_adds_passes_the_guard():
    assert _validate(EXTENDED).passed


def test_rewriting_an_existing_value_is_sent_back_even_when_the_samples_parse_the_same():
    narrowed = EXTENDED.replace("Value RX_POWER (-?\\d+\\.\\d+)", "Value RX_POWER (-?[\\d.]+)")
    assert not replay(TextFSMValidationBackend(), CURRENT, narrowed, SAMPLES, [])["differences"]

    report = _validate(narrowed)

    assert not report.passed
    assert "existing Value definitions must stay exactly as they are" in report.diagnostics[0]
    assert "RX_POWER" in report.diagnostics[0]


def test_a_value_in_force_is_put_back_to_the_letter_whatever_the_model_did_to_it():
    # Options dropped, a regex loosened, and one Value left out altogether.
    returned = """Value INTERFACE (\\S+)
Value WAVELENGTH (\\d+)

Start
  ^\\S+\\s+rx\\s+\\S+\\s+wavelength\\s+${WAVELENGTH} -> Continue
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""

    restored = restore_values(CURRENT, returned)

    assert "Value Required INTERFACE (\\S+)" in restored and "Value RX_POWER (-?\\d+\\.\\d+)" in restored
    assert "Value WAVELENGTH (\\d+)" in restored and "Value INTERFACE (\\S+)" not in restored
    checked = check_extension(TextFSMValidationBackend(), CURRENT, restored, SAMPLES, ["WAVELENGTH"])
    assert checked["rewritten"] == [] and checked["differences"] == []
    assert checked["found"]["WAVELENGTH"]["records"] == 2


def test_a_rule_in_force_that_was_edited_is_named_even_when_the_samples_parse_the_same():
    # One sample cannot tell a template rewritten to fit it from one that was extended.
    assert not replay(TextFSMValidationBackend(), CURRENT, REWRITTEN, SAMPLES, ["WAVELENGTH"])["differences"]

    assert rules_lost(CURRENT, REWRITTEN) == ["Start: ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record"]
    assert rules_lost(CURRENT, EXTENDED) == []
    report = _validate(REWRITTEN)
    assert not report.passed
    assert "existing rules must stay exactly as they are" in report.diagnostics[0]
    assert "-> Continue" in report.diagnostics[0]


def test_rules_in_force_must_also_keep_their_order():
    current = CURRENT.replace("  ^${INTERFACE}", "  ^Port\\s+Power -> Next\n  ^${INTERFACE}")
    swapped = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
  ^Port\\s+Power -> Next
"""

    assert rules_lost(current, swapped) == ["Start: ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record"]


def test_what_was_added_is_listed_values_first():
    assert added_lines(CURRENT, EXTENDED) == [
        "Value WAVELENGTH (\\d+)",
        "Value VENDOR_NOTE (\\S+)",
        "^\\S+\\s+rx\\s+\\S+\\s+wavelength\\s+${WAVELENGTH} -> Continue",
        "^\\S+\\s+rx\\s+\\S+\\s+note\\s+${VENDOR_NOTE} -> Continue",
    ]


def test_values_the_outputs_never_show_are_taken_out_again():
    trimmed = drop_values(EXTENDED, ["VENDOR_NOTE"])

    # Its Value is gone, and the line that only read it did nothing else.
    assert "VENDOR_NOTE" not in trimmed and "note" not in trimmed and "Value WAVELENGTH" in trimmed
    assert not replay(TextFSMValidationBackend(), EXTENDED, trimmed, SAMPLES, ["WAVELENGTH"])["differences"]
    assert rules_lost(CURRENT, trimmed) == []


def test_a_rule_that_read_a_dropped_value_keeps_matching_the_same_lines():
    both = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)
Value WAVELENGTH (\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER}\\s+wavelength\\s+${WAVELENGTH} -> Record
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""

    trimmed = drop_values(both, ["WAVELENGTH"])

    assert "^${INTERFACE}\\s+rx\\s+${RX_POWER}\\s+wavelength\\s+(?:\\d+) -> Record" in trimmed
    backend = TextFSMValidationBackend()
    kept = lambda template: [  # noqa: E731
        (record["INTERFACE"], record["RX_POWER"]) for text in SAMPLES.values() for record in backend.parse(template, text)
    ]
    assert kept(trimmed) == kept(both) == [("Eth1/1", "-3.20"), ("Eth1/2", "-17.96"), ("Eth1/3", "-2.10")]


def test_every_new_field_comes_with_the_line_it_was_read_from():
    table = "Index  Status  Speedlevel\n-------------------------\n 1      Normal  1\n 2      Normal  3\n"
    template = """Value Required fan (\\d+)
Value fan_status (\\S+)
Value speed_rpm (\\d+)

Start
  ^\\s*${fan}\\s+${fan_status}\\s+${speed_rpm}\\s*$$ -> Record
"""

    found = field_sources(template, ["speed_rpm"], table)

    # The heading above is what shows a person that this is a speed level and not a speed.
    assert found["speed_rpm"] == [
        {"value": "1", "line": " 1      Normal  1", "above": "Index  Status  Speedlevel"},
        {"value": "3", "line": " 2      Normal  3", "above": " 1      Normal  1"},
    ]


class _Model:
    """Answers the way the real one did in trials: a rewrite first, an extension when told off."""

    def __init__(self, *answers):
        self.answers, self.feedback = list(answers), []

    def open_session(self, spec, samples):
        model = self

        class Session:
            def next(self, validation_feedback=None):
                model.feedback.append(validation_feedback)
                text = model.answers.pop(0)
                return ParserModelTurn(candidate=TemplateCandidate(template_text=text, provider="test"),
                                       submission_mode="tool")

        return Session()


def _trial(monkeypatch, model, outputs=None):
    row = lambda name, **extra: {"canonical_field": name, "value_type": "text", "required": False, **extra}  # noqa: E731
    item = SimpleNamespace(id="interface.optics", version_id="item-v3", canonical_contract={
        "identity_dimensions": ["INTERFACE"], "entity_type": "interface",
        "observations": [row("RX_POWER"), row("WAVELENGTH", description="波长，单位 nm"), row("VENDOR_NOTE")],
    })
    adapter = SimpleNamespace(id="vendor-optics", version_id="adapter-v2", check_item_ids=("interface.optics",),
                              adapter_spec={"parser": {"template_id": "t1"}, "outputs": [{
                                  "check_item_id": "interface.optics", "source_record_set": "optics",
                                  "projection": {"fields": {"INTERFACE": {"source": "INTERFACE"},
                                                            "RX_POWER": {"source": "RX_POWER"}}}}]})
    template = SimpleNamespace(id="t1", sha256="a" * 64, vendor="Vendor", model="X", profile="X",
                               canonical_command="show optics", template_text=CURRENT)
    monkeypatch.setattr(adapter_fields, "extension_context", lambda *_: {
        "adapter": adapter, "item": item, "template": template, "record_set": "optics", "current_text": CURRENT,
        "outputs": dict(SAMPLES if outputs is None else outputs), "provided": ["INTERFACE", "RX_POWER"],
        "base": {"implementation_version_id": "adapter-v2", "check_item_version_id": "item-v3",
                 "template_id": "t1", "template_sha256": "a" * 64, "record_set": "optics"},
    })
    return trial_add_fields("vendor-optics", "interface.optics", model=model)


def test_a_trial_keeps_what_is_in_force_and_reports_each_new_field_with_its_source(monkeypatch):
    # The first answer rewrites the rule in force and drops an option; it must be sent back, not taken.
    loosened = EXTENDED.replace("Value Required INTERFACE", "Value INTERFACE")
    model = _Model(REWRITTEN.replace("Value Required INTERFACE", "Value INTERFACE"), loosened)

    result = _trial(monkeypatch, model)

    assert result["status"] == "ready" and result["model_calls"] == 2
    assert "existing rules must stay exactly as they are" in str(model.feedback[1])
    assert result["provided_fields"] == ["WAVELENGTH"] and result["absent_fields"] == ["VENDOR_NOTE"]
    assert result["regression"] == {"passed": True, "differences": []}
    # The option the model dropped is back, and the field no output shows is not in the template.
    assert "Value Required INTERFACE (\\S+)" in result["template_text"]
    assert "VENDOR_NOTE" not in result["template_text"]
    assert result["rules_in_force"] == 1 and result["rules_added"] == 1 and result["existing_field_count"] == 2
    assert result["added_lines"] == [
        "Value WAVELENGTH (\\d+)", "^\\S+\\s+rx\\s+\\S+\\s+wavelength\\s+${WAVELENGTH} -> Continue"]
    wavelength = result["fields"][0]
    assert wavelength["name"] == "WAVELENGTH" and wavelength["description"] == "波长，单位 nm"
    assert wavelength["records"] == 2 and wavelength["examples"] == ["850", "1310"]
    assert wavelength["sources"][0] == {"value": "850", "line": "Eth1/1 rx -3.20 wavelength 850", "above": ""}
    assert result["base"]["template_id"] == "t1"


def test_fields_no_output_shows_end_as_not_provided_by_the_device_not_as_a_failure(monkeypatch):
    result = _trial(monkeypatch, _Model(EXTENDED), outputs={"s1": "Eth1/2 rx -17.96\n"})

    assert result["status"] == "no_data" and result["template_text"] is None
    assert result["provided_fields"] == [] and result["absent_fields"] == ["WAVELENGTH", "VENDOR_NOTE"]


def test_a_model_that_only_ever_rewrites_is_reported_in_words_an_engineer_can_act_on(monkeypatch):
    result = _trial(monkeypatch, _Model(REWRITTEN, REWRITTEN, REWRITTEN))

    assert result["status"] == "failed" and result["template_text"] is None
    assert result["reason"] == "AI 试了 3 次，每次都改动了已有的解析规则，结果没有采用。可以减少一次要补的字段再试。"
    assert "existing rules must stay" in result["detail"]
