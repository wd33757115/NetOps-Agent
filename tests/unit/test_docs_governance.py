from datetime import date
from pathlib import Path

import yaml

from scripts.docs.audit_docs import audit


def _write_registry(root: Path, documents: list[dict]) -> Path:
    path = root / "document-governance.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "review_interval_days": 120,
                "documents": documents,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_docs_audit_accepts_one_active_authority_and_valid_replacement(
    tmp_path,
):
    (tmp_path / "current.md").write_text(
        "# Current\n\n[History](history.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "history.md").write_text(
        "# History\n\n"
        "<!-- document-status: superseded -->\n"
        "> Replaced by [Current](current.md).\n",
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        [
            {
                "path": "current.md",
                "topic": "platform",
                "status": "active",
                "owner": "team",
                "last_verified": "2026-07-28",
            },
            {
                "path": "history.md",
                "topic": "platform-history",
                "status": "superseded",
                "owner": "team",
                "superseded_by": "current.md",
            },
        ],
    )

    result = audit(
        registry_path=registry,
        today=date(2026, 7, 28),
        all_links=True,
        strict_unregistered=True,
    )

    assert result["success"] is True
    assert result["active_count"] == 1
    assert result["superseded_count"] == 1


def test_docs_audit_rejects_duplicate_active_topics_and_stale_reviews(
    tmp_path,
):
    for name in ("one.md", "two.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            {
                "path": name,
                "topic": "same-topic",
                "status": "active",
                "owner": "team",
                "last_verified": "2025-01-01",
            }
            for name in ("one.md", "two.md")
        ],
    )

    result = audit(
        registry_path=registry,
        today=date(2026, 7, 28),
        all_links=False,
        strict_unregistered=True,
    )

    assert result["success"] is False
    assert any(
        "multiple active documents" in error
        for error in result["errors"]
    )
    assert sum(
        "review is" in error for error in result["errors"]
    ) == 2


def test_docs_audit_reports_broken_links_and_unregistered_files(tmp_path):
    (tmp_path / "current.md").write_text(
        "# Current\n\n[Missing](missing.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "unregistered.md").write_text(
        "# Unregistered\n",
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        [
            {
                "path": "current.md",
                "topic": "platform",
                "status": "active",
                "owner": "team",
                "last_verified": "2026-07-28",
            }
        ],
    )

    result = audit(
        registry_path=registry,
        today=date(2026, 7, 28),
        all_links=True,
        strict_unregistered=True,
    )

    assert result["success"] is False
    assert result["unregistered_count"] == 1
    assert result["broken_link_count"] == 1


def test_docs_audit_blocks_superseded_document_from_active_index(tmp_path):
    (tmp_path / "README.md").write_text(
        "<!-- governed-index: active -->\n"
        "## Current guides\n\n"
        "[Old guide](history.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# Current\n", encoding="utf-8")
    (tmp_path / "history.md").write_text(
        "# History\n\n"
        "<!-- document-status: superseded -->\n"
        "> Replaced by [Current](current.md).\n",
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        [
            {
                "path": "README.md",
                "topic": "index",
                "status": "active",
                "owner": "team",
                "last_verified": "2026-07-30",
            },
            {
                "path": "current.md",
                "topic": "platform",
                "status": "active",
                "owner": "team",
                "last_verified": "2026-07-30",
            },
            {
                "path": "history.md",
                "topic": "platform-history",
                "status": "superseded",
                "owner": "team",
                "superseded_by": "current.md",
            },
        ],
    )

    result = audit(
        registry_path=registry,
        today=date(2026, 7, 30),
        all_links=True,
        strict_unregistered=True,
    )

    assert result["success"] is False
    assert result["index_status_violation_count"] == 1
    assert any(
        "active index links to superseded document history.md" in error
        for error in result["errors"]
    )


def test_docs_audit_requires_superseded_banner_and_replacement_link(tmp_path):
    (tmp_path / "current.md").write_text("# Current\n", encoding="utf-8")
    (tmp_path / "missing-banner.md").write_text(
        "# Missing banner\n",
        encoding="utf-8",
    )
    (tmp_path / "missing-link.md").write_text(
        "# Missing link\n\n"
        "<!-- document-status: superseded -->\n"
        "> Historical only.\n",
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        [
            {
                "path": "current.md",
                "topic": "platform",
                "status": "active",
                "owner": "team",
                "last_verified": "2026-07-30",
            }
        ]
        + [
            {
                "path": name,
                "topic": f"history-{index}",
                "status": "superseded",
                "owner": "team",
                "superseded_by": "current.md",
            }
            for index, name in enumerate(
                ("missing-banner.md", "missing-link.md"),
                start=1,
            )
        ],
    )

    result = audit(
        registry_path=registry,
        today=date(2026, 7, 30),
        all_links=True,
        strict_unregistered=True,
    )

    assert result["success"] is False
    assert result["status_banner_violation_count"] == 2
    assert any("requires a document-status marker" in item for item in result["errors"])
    assert any("banner must link to current.md" in item for item in result["errors"])
