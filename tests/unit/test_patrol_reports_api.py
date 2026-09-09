import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.auth.models import CurrentUser
from src.gateway import patrol_reports_api as reports


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(reports.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="owner", username="test", role="operator"
    )
    monkeypatch.setattr(
        reports,
        "report_context",
        lambda job_id, **kwargs: ({"assessment": {"status": "需复核"}, "metrics": {}}, {}),
    )
    return TestClient(app)


def test_local_export_does_not_require_ai_or_draft_storage(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("local export touched AI")

    monkeypatch.setattr(reports.drafting, "provider_config", forbidden)
    monkeypatch.setattr(reports.drafting, "accepted_text", forbidden)
    response = client.post("/api/v1/patrol/offline-jobs/job/report/export", json={})
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert response.headers["content-type"] == reports.DOCX


def test_unauthorized_job_checked_before_loading_template(client, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("other owner")

    monkeypatch.setattr(reports, "report_context", denied)
    assert client.get("/api/v1/patrol/offline-jobs/job/report/evidence").status_code == 403
    assert client.post("/api/v1/patrol/offline-jobs/job/report/export", json={}).status_code == 403
    assert (
        client.post("/api/v1/patrol/offline-jobs/job/report/drafts/preview", json={}).status_code
        == 403
    )


def test_operator_cannot_import_templates(client):
    assert (
        client.post(
            "/api/v1/patrol/report-templates", files={"file": ("test.docx", b"fake")}
        ).status_code
        == 403
    )


def test_excel_authorizes_before_loading_private_snapshots(client, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("other owner")
    def forbidden(*args, **kwargs):
        raise AssertionError("private snapshots accessed before authorization")
    monkeypatch.setattr(reports, "report_context", denied)
    monkeypatch.setattr(reports, "load_detail_data", forbidden)
    assert client.get("/api/v1/patrol/offline-jobs/job/report/details").status_code == 403


def test_excel_export_uses_binary_response_and_no_ai(client, monkeypatch):
    monkeypatch.setattr(reports, "load_detail_data", lambda source: {"definitions": {}, "snapshots": []})
    response = client.get("/api/v1/patrol/offline-jobs/job/report/details")
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert response.headers["content-type"] == reports.XLSX


def test_unknown_metadata_and_draft_scopes_rejected(client):
    assert (
        client.post(
            "/api/v1/patrol/offline-jobs/job/report/export",
            json={"metadata": {"conclusion.text": "正常"}},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/patrol/offline-jobs/job/report/drafts/preview",
            json={"sections": ["conclusion.text"]},
        ).status_code
        == 422
    )


def test_full_evidence_rejects_stale_version(client):
    response = client.get(
        "/api/v1/patrol/offline-jobs/job/report/evidence/CLI-001/full",
        params={"source_digest": "0" * 64},
    )
    assert response.status_code == 409


def test_evidence_filters_reference_before_pagination_and_never_sends_full_cli(client, monkeypatch):
    entries = [
        {
            "id": f"CLI-{i}",
            "references": [f"event-{i}"],
            "match_references": [f"event-{i}", f"obs-{i}", f"change-{i}"],
            "full_source": {"current": "PRIVATE CLI"},
        }
        for i in range(15)
    ]
    monkeypatch.setattr(reports, "build_excerpts", lambda _: entries)
    for ref in ("obs-14", "change-14", "event-14"):
        r = client.get("/api/v1/patrol/offline-jobs/job/report/evidence", params={"reference": ref})
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["id"] == "CLI-14"
        assert "full_source" not in r.json()["items"][0]
    missing = client.get(
        "/api/v1/patrol/offline-jobs/job/report/evidence", params={"reference": "unrelated"}
    )
    assert missing.json()["items"] == []


@pytest.mark.parametrize("attempt_count", [0, 1, 2])
def test_comparison_coverage_uses_unique_frozen_attempt_not_changed_rows(attempt_count):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from src.core.patrol.report_service import load_comparison_coverage

    attempt = SimpleNamespace(
        id="comparison",
        scope_json={"previous_run_id": "base", "full_run": True},
        result_json={"unstructured_compared_outputs": [["d", "cmd-1"], ["d", "cmd-2"]]},
    )

    class Query:
        def filter(self, *args):
            return self

        def all(self):
            return [attempt] * attempt_count

    db = SimpleNamespace(
        get=lambda *args: SimpleNamespace(
            run_id="current", completed_at=datetime.now(timezone.utc)
        ),
        query=lambda *args: Query(),
    )
    report = {
        "evidence": {"analysis": {"attempt_id": "event"}},
        "unstructured_comparison": {"changed_output_count": 0},
    }
    load_comparison_coverage(db, report, [{"role": "baseline", "patrol_run_id": "base"}])
    assert report["unstructured_comparison"]["comparison_coverage_known"] == (attempt_count == 1)
    if attempt_count == 1:
        assert report["unstructured_comparison"]["compared_output_count"] == 2
        assert report["metrics"]["unstructured_compared_count"] == 2


@pytest.mark.parametrize(
    "changed",
    [
        {},
        {"canonical_command": "display logbuffer"},
        {"run_id": "another-run"},
        {"device_id": "another-device"},
    ],
)
def test_comparison_references_reject_wrong_command_device_and_period(changed):
    from types import SimpleNamespace

    from src.core.patrol.report_service import validate_comparison_references

    snapshot = SimpleNamespace(
        **{
            **{"canonical_command": "display startup", "run_id": "base-run", "device_id": "d"},
            **changed,
        }
    )
    args = (
        [{"device_id": "d", "command": "display startup", "baseline": {"snapshot_id": "base"}}],
        {"base": snapshot},
        [{"role": "baseline", "patrol_run_id": "base-run"}],
    )
    if changed:
        with pytest.raises(ValueError, match="复用文件新建分析"):
            validate_comparison_references(*args)
    else:
        validate_comparison_references(*args)


@pytest.mark.parametrize("baseline_count", [0, 1, 2])
@pytest.mark.parametrize("with_recovery", [False, True])
def test_current_findings_produce_evidence_without_new_events(
    monkeypatch, baseline_count, with_recovery
):
    from contextlib import contextmanager
    from copy import deepcopy
    from types import SimpleNamespace

    from src.core.patrol import report_service
    from src.infrastructure.db import postgres

    report = {
        "structured_event_evidence": {"items": []},
        "_source": {
            "observations": [],
            "current_findings": [
                {
                    "device_id": "d",
                    "entity_key": '["XGE1/0/1"]',
                    "field": "oper_status",
                    "value": "down",
                    "snapshot_id": "s",
                    "observation_id": "o",
                }
            ],
        },
    }
    if with_recovery:
        report["_source"]["current_findings"][0]["health_assessment"] = {
            "status": "abnormal",
            "violations": ["baseline_relative_deviation_exceeded"],
        }
        report["structured_event_evidence"]["items"] = [
            {
                "device_id": "d",
                "entity_key": '["XGE1/0/1"]',
                "field": "oper_status",
                "current": {"snapshot_id": "s"},
                "event": {
                    "event_id": "recovery",
                    "event_type": "Recovered",
                    "event_class": "recovery",
                },
            }
        ]

    class Service:
        def get_job(self, job, **kwargs):
            assert kwargs == {"owner_id": "owner", "is_admin": False, "purpose": "offline_patrol"}
            return {
                "status": "completed",
                "periods": [{"role": "baseline", "patrol_run_id": "base-run"}]
                if baseline_count
                else [],
            }

        def build_leadership_report_data(self, *args, **kwargs):
            return deepcopy(report)

        def _snapshot_comparison_evidence(self, row):
            return {
                "snapshot_id": row.id,
                "command": "display interface brief",
                "raw_text": "Interface Link\nXGE1/0/1 DOWN",
            }

    class Query:
        def __init__(self, number):
            self.number = number

        def filter(self, *args):
            return self

        def all(self):
            return (
                [
                    SimpleNamespace(
                        id="s", device_id="d", canonical_command="display interface brief"
                    )
                ]
                if self.number == 0
                else [
                    SimpleNamespace(
                        id=f"base-{i}", device_id="d", canonical_command="display interface brief"
                    )
                    for i in range(baseline_count)
                ]
            )

    queries = []

    def query(*args):
        result = Query(len(queries))
        queries.append(result)
        return result

    @contextmanager
    def session():
        yield SimpleNamespace(query=query)

    monkeypatch.setattr(report_service, "OfflinePatrolJobService", Service)
    monkeypatch.setattr(postgres, "get_db_session", session)
    result, _ = report_service.report_context("job", owner_id="owner")
    assert len(result["structured_event_evidence"]["items"]) == (2 if with_recovery else 1)
    item = result["structured_event_evidence"]["items"][-1]
    assert item["current_finding"] is True and "DOWN" in item["current"]["raw_text"]
    assert bool(item["baseline"]) == (baseline_count == 1)
    if baseline_count == 1:
        assert item["baseline"]["snapshot_id"] == "base-0"
        assert "value" not in item["baseline"]  # no inferred baseline judgement
