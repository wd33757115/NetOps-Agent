from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.patrol.raw_importer import _preview_parse_status


@pytest.mark.parametrize(
    ("policy_status", "identity_status", "parser_status", "failure_code", "expected"),
    [
        ("eligible", "known", "success", None, "ready"),
        ("eligible", "known", "empty", None, "ready"),
        ("eligible", "known", "unavailable", "template_missing", "template_missing"),
        ("eligible", "known", "failed", "textfsm_error", "parse_failed"),
        ("eligible", "known", "unavailable", "unsupported_command", "ignored"),
        ("raw", "known", "not_required", None, "raw_compare"),
        ("ignored", "known", "unavailable", None, "ignored"),
        ("eligible", "unknown", "unavailable", None, "identity_unknown"),
    ],
)
def test_preview_parse_status_matches_snapshot_quality_buckets(
    policy_status,
    identity_status,
    parser_status,
    failure_code,
    expected,
):
    parsed = SimpleNamespace(
        policy_status=policy_status,
        identity_status=identity_status,
        parser_status=parser_status,
        failure_code=failure_code,
    )

    assert _preview_parse_status(parsed) == expected
