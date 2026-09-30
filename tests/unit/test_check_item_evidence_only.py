from types import SimpleNamespace

import pytest

from src.core.check_items.run_finalizer import CheckItemRunFinalizer


def snapshot(parse_status, bound_to="v-text"):
    binding = {"check_item_id": "network.arp_text", "check_item_version_id": bound_to}
    return SimpleNamespace(
        parse_status=parse_status,
        metadata_json={"check_item_bindings": [binding], "selected_check_item_bindings": [binding]},
    )


@pytest.mark.parametrize("parse_status", ["raw_hashed", "template_missing", "parse_failed"])
def test_evidence_only_keeps_raw_output_whatever_the_parser_said(parse_status):
    assert CheckItemRunFinalizer._snapshot_selected(
        snapshot(parse_status), check_item_version_id="v-text", result_mode="evidence_only"
    )


def test_evidence_only_still_requires_its_own_binding():
    assert not CheckItemRunFinalizer._snapshot_selected(
        snapshot("raw_hashed", bound_to="v-other"),
        check_item_version_id="v-text",
        result_mode="evidence_only",
    )


def test_text_comparison_modes_still_reject_unparsed_template_gaps():
    assert not CheckItemRunFinalizer._snapshot_selected(
        snapshot("template_missing"), check_item_version_id="v-text", result_mode="normalized_diff"
    )
