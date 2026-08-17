from __future__ import annotations

import asyncio

import pytest

from src.core.check_items.batch_results import (
    CheckItemBatchResultService,
    _cursor_scope,
    _decode_cursor,
    _encode_cursor,
)
from src.gateway import workflow_api


def _scope(search: str = "") -> str:
    return _cursor_scope(
        search=search,
        assessment_status="healthy",
        completeness_status=None,
        source_identity_status=None,
        output_quality_status=None,
        freshness_status=None,
        coverage_status=None,
    )


def test_check_result_cursor_round_trips_exact_query_scope():
    scope = _scope("switch-1")
    values = (
        "device-1",
        "check-item-version-1",
        "execution-1",
    )

    cursor = _encode_cursor(
        patrol_batch_id="batch-1",
        scope_digest=scope,
        values=values,
        total=40000,
        secret="test-secret",
        now=1_000,
        ttl_seconds=300,
    )

    assert _decode_cursor(
        cursor,
        patrol_batch_id="batch-1",
        scope_digest=scope,
        secret="test-secret",
        now=1_100,
    ) == (values, 40000)


def test_check_result_cursor_rejects_batch_or_filter_reuse():
    cursor = _encode_cursor(
        patrol_batch_id="batch-1",
        scope_digest=_scope("switch-1"),
        values=("a", "b", "c"),
        total=20,
        secret="test-secret",
        now=1_000,
        ttl_seconds=300,
    )

    with pytest.raises(ValueError, match="does not match"):
        _decode_cursor(
            cursor,
            patrol_batch_id="batch-2",
            scope_digest=_scope("switch-1"),
            secret="test-secret",
            now=1_100,
        )
    with pytest.raises(ValueError, match="does not match"):
        _decode_cursor(
            cursor,
            patrol_batch_id="batch-1",
            scope_digest=_scope("switch-2"),
            secret="test-secret",
            now=1_100,
        )


def test_check_result_cursor_rejects_tampering_and_wrong_secret():
    cursor = _encode_cursor(
        patrol_batch_id="batch-1",
        scope_digest=_scope(),
        values=("a", "b", "c"),
        total=20,
        secret="test-secret",
        now=1_000,
        ttl_seconds=300,
    )
    payload, signature = cursor.split(".", 1)
    tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]

    with pytest.raises(ValueError, match="invalid"):
        _decode_cursor(
            f"{tampered_payload}.{signature}",
            patrol_batch_id="batch-1",
            scope_digest=_scope(),
            secret="test-secret",
            now=1_100,
        )
    with pytest.raises(ValueError, match="invalid"):
        _decode_cursor(
            cursor,
            patrol_batch_id="batch-1",
            scope_digest=_scope(),
            secret="wrong-secret",
            now=1_100,
        )


def test_check_result_cursor_rejects_noncanonical_or_malformed_base64url():
    cursor = _encode_cursor(
        patrol_batch_id="batch-1",
        scope_digest=_scope(),
        values=("a", "b", "c"),
        total=20,
        secret="test-secret",
        now=1_000,
        ttl_seconds=300,
    )
    payload, signature = cursor.split(".", 1)
    replacement = "A" if signature[-1] != "A" else "B"

    with pytest.raises(ValueError, match="invalid"):
        _decode_cursor(
            f"{payload}.{signature[:-1]}{replacement}",
            patrol_batch_id="batch-1",
            scope_digest=_scope(),
            secret="test-secret",
            now=1_100,
        )
    with pytest.raises(ValueError, match="invalid"):
        _decode_cursor(
            f"{payload}.%",
            patrol_batch_id="batch-1",
            scope_digest=_scope(),
            secret="test-secret",
            now=1_100,
        )


def test_check_result_cursor_rejects_expired_token():
    cursor = _encode_cursor(
        patrol_batch_id="batch-1",
        scope_digest=_scope(),
        values=("a", "b", "c"),
        total=20,
        secret="test-secret",
        now=1_000,
        ttl_seconds=60,
    )

    with pytest.raises(ValueError, match="expired"):
        _decode_cursor(
            cursor,
            patrol_batch_id="batch-1",
            scope_digest=_scope(),
            secret="test-secret",
            now=1_060,
        )


def test_check_result_deep_offset_requires_cursor(monkeypatch):
    monkeypatch.setattr(
        "src.core.check_items.batch_results.settings.CHECK_ITEM_RESULT_MAX_OFFSET",
        1000,
    )
    service = CheckItemBatchResultService()

    with pytest.raises(ValueError, match="continue with next_cursor"):
        service.list_results(
            "batch-1",
            limit=50,
            offset=1001,
        )


def test_check_result_api_forwards_cursor_and_maps_invalid_cursor_to_400(
    monkeypatch,
):
    calls = []

    class StubService:
        def list_results(self, patrol_batch_id, **kwargs):
            calls.append((patrol_batch_id, kwargs))
            if kwargs.get("cursor") == "bad":
                raise ValueError("invalid check-result cursor")
            return {"items": [], "next_cursor": None}

    monkeypatch.setattr(
        workflow_api,
        "CheckItemBatchResultService",
        StubService,
    )
    result = asyncio.run(
        workflow_api.list_patrol_batch_check_results(
            "batch-1",
            cursor="good",
            _user=object(),
        )
    )

    assert result["items"] == []
    assert calls[0][1]["cursor"] == "good"
    with pytest.raises(workflow_api.HTTPException) as exc_info:
        asyncio.run(
            workflow_api.list_patrol_batch_check_results(
                "batch-1",
                cursor="bad",
                _user=object(),
            )
        )
    assert exc_info.value.status_code == 400


def test_check_result_detail_api_forwards_entry_pagination_and_maps_missing_to_404(
    monkeypatch,
):
    calls = []

    class StubService:
        def get_result_detail(self, patrol_batch_id, execution_id, **kwargs):
            calls.append((patrol_batch_id, execution_id, kwargs))
            if execution_id == "missing":
                raise ValueError("check result not found")
            return {
                "schema_version": "patrol-check-result-detail/v1",
                "assessment_basis_entries": {
                    "total": 3,
                    "limit": kwargs["entry_limit"],
                    "offset": kwargs["entry_offset"],
                    "has_more": False,
                },
            }

    monkeypatch.setattr(
        workflow_api,
        "CheckItemBatchResultService",
        StubService,
    )

    result = asyncio.run(
        workflow_api.get_patrol_batch_check_result_detail(
            "batch-1",
            "execution-1",
            entry_limit=25,
            entry_offset=50,
            _user=object(),
        )
    )

    assert result["assessment_basis_entries"] == {
        "total": 3,
        "limit": 25,
        "offset": 50,
        "has_more": False,
    }
    assert calls[0] == (
        "batch-1",
        "execution-1",
        {"entry_limit": 25, "entry_offset": 50},
    )

    with pytest.raises(workflow_api.HTTPException) as exc_info:
        asyncio.run(
            workflow_api.get_patrol_batch_check_result_detail(
                "batch-1",
                "missing",
                entry_limit=50,
                entry_offset=0,
                _user=object(),
            )
        )
    assert exc_info.value.status_code == 404
