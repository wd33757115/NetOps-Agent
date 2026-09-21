import pytest

from src.apps.materials.backend.documents import DocumentReader
from src.apps.assistant.backend.workspace import DocumentWorkspace


@pytest.fixture
def reader(tmp_path):
    (tmp_path / "manual.md").write_text(
        "---\nvendor: Huawei\nsoftware_version: 8.2.1\n---\n"
        "# 网络配置\n限制条件。\n## 接口检查\n"
        "执行 display interface 检查接口状态。\n"
        "```text\n# 这不是章节\n```\n"
        "| 状态 | 含义 |\n| --- | --- |\n| down | 故障 |\n",
        encoding="utf-8",
    )
    return DocumentReader(tmp_path)


def test_document_heading_filter_pagination_and_revision(reader):
    hit = reader.search("接口 display interface", filters={"software_version": "8.2.1"})["items"][0]
    assert reader.search("接口", filters={"software_version": "8.2.2"})["items"] == []
    pages = reader.list_sections(hit["document_id"], hit["revision"], limit=1)
    assert pages["next_offset"] == 1
    assert len(reader.load("manual.md")[2]) == 2
    parts, offset = [], 0
    while offset is not None:
        page = reader.read_section(
            hit["document_id"], hit["revision"], hit["section_id"], offset=offset, limit=13
        )
        reader.validate_citation(page["citation"])
        parts.append(page["text"])
        offset = page["next_offset"]
    assert "".join(parts) == reader.load("manual.md")[2][1].text
    with (reader.root / "manual.md").open("a", encoding="utf-8") as stream:
        stream.write("新修订")
    with pytest.raises(ValueError, match="revision_changed"):
        reader.validate_citation(page["citation"])


@pytest.mark.parametrize("name", ["../private.md", "C:/private.md", "/private.md", "sub\\x.md"])
def test_arbitrary_paths_rejected(reader, name):
    with pytest.raises(ValueError):
        reader.load(name)


def test_bad_files_and_filters_are_explicit(reader):
    (reader.root / "not-supported.pdf").write_bytes(b"not a pdf")
    (reader.root / "invalid.md").write_bytes(b"\xff")
    assert len(reader.search("接口")["skipped"]) == 2
    with pytest.raises(ValueError, match="metadata_filter"):
        reader.search("接口", filters={"actor_id": "other"})
    with pytest.raises(ValueError, match="section_page"):
        reader.list_sections("manual.md", reader.load("manual.md")[0], offset=-1)


def test_registry_rechecks_permissions_and_write_classification(monkeypatch):
    from contextlib import nullcontext

    from fastapi import HTTPException
    from pydantic import BaseModel

    from src.platform.kernel import runtime
    from src.platform.tools import registry

    class Args(BaseModel):
        value: int

    calls, revoked = [], False

    def authorize(actor, app):
        assert actor == "alice" and app == "inspection"
        if revoked:
            raise HTTPException(403)
        return actor

    catalog = {
        "inspection__read_batch": (
            "inspection",
            registry.Tool(
                "read_batch",
                "Read a batch",
                Args,
                lambda user, body: calls.append(body.value),
                evidence=True,
            ),
        )
    }
    catalog["inspection__legacy_read"] = (
        "inspection",
        registry.Tool("legacy_read", "Unbounded legacy read", Args, lambda *_: None),
    )
    monkeypatch.setattr(registry, "catalog", lambda: catalog)
    monkeypatch.setattr(registry, "authorize_worker", authorize)
    monkeypatch.setattr(runtime, "invocation", lambda app: nullcontext())
    tools = DocumentWorkspace(
        "alice", reader=DocumentReader("unused-test-scope"), check=lambda: None
    )
    assert "inspection__read_batch" in {d["name"] for d in tools.definitions()}
    assert "inspection__legacy_read" not in {d["name"] for d in tools.definitions()}
    with pytest.raises(PermissionError):
        tools.invoke("inspection__legacy_read", {"value": 1})
    revoked = True
    with pytest.raises(HTTPException):
        tools.invoke("inspection__read_batch", {"value": 1})
    revoked = False
    catalog["inspection__read_batch"] = (
        "inspection",
        registry.Tool(
            "read_batch", "Now a write", Args, lambda user, body: calls.append(body.value), True
        ),
    )
    with pytest.raises(PermissionError):
        tools.invoke("inspection__read_batch", {"value": 1})
    assert calls == []
