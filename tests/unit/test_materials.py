import io
from contextlib import contextmanager
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from src.apps.materials.backend import api, import_service, library
from src.apps.materials.backend.models import Document, Revision
from src.platform.kernel.migrations import migrate
from src.platform.kernel.registry import discover

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def word(extra="", first="接口配置"):
    body = f"""<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{first}</w:t></w:r></w:p>
    <w:p><w:r><w:t># command is not a heading</w:t></w:r></w:p>
    <w:p><w:del><w:r><w:t>DELETED_SECRET</w:t></w:r></w:del><w:ins><w:r><w:t>accepted</w:t></w:r></w:ins></w:p>
    <w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>
    <w:p><w:r><w:t>合并值</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>next cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:r><w:drawing/><w:object/></w:r></w:p>
    <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>未选章节</w:t></w:r></w:p>
    <w:p><w:r><w:t>EXCLUDED_SECTION</w:t></w:r></w:p>{extra}"""
    data = io.BytesIO()
    with ZipFile(data, "w") as z:
        z.writestr(
            "word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        )
        z.writestr(
            "word/styles.xml",
            f'<w:styles xmlns:w="{W}"><w:style w:styleId="Heading1">'
            '<w:name w:val="heading 1"/></w:style></w:styles>',
        )
    return data.getvalue()


class Storage:
    def __init__(self):
        self.objects = {}

    def upload_file(self, key, data, mime):
        self.objects[key] = data
        return True

    def download_file(self, key):
        return self.objects.get(key)


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'materials.sqlite'}")
    migrate(discover()["materials"], engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(library, "sessions", sessions)
    monkeypatch.setattr(import_service, "sessions", sessions)
    backend = Storage()
    monkeypatch.setattr(import_service, "storage", lambda: backend)
    yield sessions, backend
    engine.dispose()


def plan(data, **changes):
    parsed = import_service.preview(data, "手册.docx")
    return import_service.Publication(
        **dict(
            dict(
                title="手册",
                source_sha256=parsed["source_sha256"],
                normalized_sha256=parsed["normalized_sha256"],
                acknowledged_limitations=True,
            ),
            **changes,
        )
    )


def test_local_preview_preserves_cli_and_table_coordinates(database):
    data = word()
    parsed = import_service.preview(data, "手册.docx")
    assert len(parsed["sections"]) == 2
    assert "# command" in parsed["sections"][0]["text"]
    assert '"column": 3' in parsed["sections"][0]["text"]
    assert "DELETED_SECRET" not in str(parsed)
    assert "accepted" in str(parsed)
    assert parsed["counts"]["images"] == 1 and parsed["counts"]["objects"] == 1
    assert parsed["counts"]["merged_cells"] == 1
    assert "text" not in parsed and "locations" not in parsed
    assert not database[1].objects
    assert library.documents() == []


def test_publish_entire_file_and_immutable_original(database):
    data = word()
    doc = import_service.publish("alice", data, "手册.docx", plan(data))
    reader = library.LibraryReader()
    revision, metadata, sections = reader.load(doc["id"])
    assert len(sections) == 2 and "EXCLUDED_SECTION" in sections[1].text
    assert metadata["extraction_warnings"]
    cited = reader.read_section(doc["id"], revision, sections[0].id)["citation"]
    assert cited["source"]["locations"][0]["paragraph"] == 1
    assert any(loc["kind"] == "table_row" for loc in cited["source"]["locations"])
    newer = word(first="新版接口配置")
    changed = import_service.publish(
        "alice", newer, "新版.docx", plan(newer, document_id=doc["id"], expected_revision=revision)
    )
    assert changed["revision"] != revision
    assert import_service.original(doc["id"], revision) == ("手册.docx", data)
    assert import_service.original(doc["id"], changed["revision"]) == ("新版.docx", newer)
    reader.validate_citation(cited)
    cited["source"]["locations"][0]["paragraph"] = 999
    with pytest.raises(ValueError):
        reader.validate_citation(cited)
    assert len(library.revisions(doc["id"])["items"]) == 2
    with database[0]() as session:
        assert (
            session.get(Revision, (doc["id"], revision)).source_sha256 == plan(data).source_sha256
        )


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"acknowledged_limitations": False}, 422),
        ({"source_sha256": "0" * 64}, 409),
        ({"normalized_sha256": "0" * 64}, 409),
        ({"section_ids": ["missing"]}, 422),
        ({"section_ids": ["line-1"]}, 422),
        ({"section_ids": ["line-1", "line-1"]}, 422),
    ],
)
def test_invalid_publication_does_not_write_storage(database, changes, status):
    data = word()
    with pytest.raises(HTTPException) as error:
        import_service.publish("alice", data, "手册.docx", plan(data, **changes))
    assert error.value.status_code == status
    assert not database[1].objects


def test_original_integrity_and_failed_storage(database, monkeypatch):
    data = word()
    monkeypatch.setattr(database[1], "download_file", lambda key: b"corrupted")
    with pytest.raises(HTTPException) as error:
        import_service.publish("alice", data, "手册.docx", plan(data))
    assert error.value.status_code == 503
    assert not library.documents()


@pytest.mark.parametrize(
    "raw,name", [(b"bad zip", "a.docx"), (word(), "../x.docx"), (word(), "a.docm")]
)
def test_invalid_files_rejected(raw, name):
    with pytest.raises(HTTPException) as error:
        import_service.preview(raw, name)
    assert error.value.status_code == 422


def test_xml_dtd_not_expanded():
    raw = io.BytesIO()
    with ZipFile(raw, "w") as z:
        z.writestr(
            "word/document.xml", '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///private">]><x>&e;</x>'
        )
    with pytest.raises(HTTPException):
        import_service.preview(raw.getvalue(), "a.docx")


def test_management_routes_require_materials_grant_and_assistant_has_no_write_routes(database):
    from src.apps.assistant.backend.api import router as chat

    app = FastAPI()
    app.include_router(api.router, prefix="/materials")
    app.include_router(chat, prefix="/assistant")
    with TestClient(app) as client:
        assert client.get("/materials/documents").status_code == 401
        assert (
            client.post(
                "/materials/documents/preview", files={"file": ("a.docx", word())}
            ).status_code
            == 401
        )
        app.dependency_overrides[api.user] = lambda: SimpleNamespace(user_id="alice")
        result = client.post("/materials/documents/preview", files={"file": ("a.docx", word())})
        assert result.status_code == 200
        assert client.get("/assistant/documents").status_code == 401
        assert client.post("/assistant/documents", json={}).status_code == 405
        assert client.post(
            "/assistant/documents/import", files={"file": ("a.docx", word())}
        ).status_code in (404, 405)
        response = client.post(
            "/materials/documents/import",
            files={"file": ("a.docx", word())},
            data={"options": plan(word()).model_dump_json()},
        )
        assert response.status_code == 200
        document = response.json()
        url = f"/materials/documents/{document['id']}/revisions/{document['revision']}/source"
        assert client.get(url).content == word()
        app.dependency_overrides.clear()
        assert client.get(url).status_code == 401


def test_existing_snapshots_copy_without_changing_citation_identity(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    manifests = discover()
    migrate(manifests["assistant"], engine)
    meta = MetaData()
    old_documents = Table("app_assistant_documents", meta, autoload_with=engine)
    old_revisions = Table("app_assistant_revisions", meta, autoload_with=engine)
    from src.apps.materials.backend.repository import now

    with engine.begin() as connection:
        connection.execute(
            old_documents.insert().values(
                id="d", title="原文", current_revision="r", created_by="alice"
            )
        )
        connection.execute(
            old_revisions.insert().values(
                document_id="d",
                revision="r",
                title="原文",
                source_sha256="s",
                metadata_json={},
                sections_json=[
                    {
                        "id": "line-1",
                        "title_path": ["标题"],
                        "start_line": 1,
                        "text": "# 标题\n正文",
                    }
                ],
                text="# 标题\n正文",
                created_at=now(),
            )
        )
    from src.apps.materials.backend.documents import DocumentReader, Section

    class HistoricalReader(DocumentReader):
        def __init__(self):
            pass

        def load(self, document_id, revision=None):
            with engine.connect() as connection:
                row = connection.execute(select(old_revisions)).mappings().one()
                return (
                    row["revision"],
                    row["metadata_json"],
                    [
                        Section(s["id"], tuple(s["title_path"]), s["start_line"], s["text"])
                        for s in row["sections_json"]
                    ],
                )

    citation = HistoricalReader().read_section("d", "r", "line-1")["citation"]
    migrate(manifests["materials"], engine)
    migrate(manifests["materials"], engine)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(library, "sessions", sessions)
    library.LibraryReader().validate_citation(citation)
    with Session(engine) as session:
        assert session.get(Document, "d").current_revision == "r"
        copied = session.get(Revision, ("d", "r"))
        assert copied.text == "# 标题\n正文" and copied.source_json is None
        assert session.execute(select(old_revisions.c.text)).scalar_one() == copied.text
    engine.dispose()


def test_large_whole_file_publishes_without_the_old_character_ceiling(database):
    data = word(extra="<w:p><w:r><w:t>" + "x" * 731023 + "</w:t></w:r></w:p>")
    result = import_service.publish("alice", data, "large.docx", plan(data))
    assert len(library.LibraryReader().load(result["id"])[2]) == 2
    with database[0]() as session:
        row = session.get(Revision, (result["id"], result["revision"]))
        assert len(row.text) > 731023
        assert row.source_json["publication_scope"] == "whole_file"


def test_status_delete_and_reimport_preserve_historical_citations(database):
    from src.apps.materials.backend import governance

    data = word()
    doc = import_service.publish("alice", data, "source.docx", plan(data))
    reader = library.LibraryReader()
    revision, _, sections = reader.load(doc["id"])
    citation = reader.read_section(doc["id"], revision, sections[0].id)["citation"]
    expected = governance.ExpectedDocument(expected_revision=revision, expected_status="active")
    governance.change_status(doc["id"], expected, "inactive")
    assert library.documents() == [] and reader.document_ids() == []
    assert library.documents(include_inactive=True)[0]["status"] == "inactive"
    with pytest.raises(ValueError):
        reader.load(doc["id"])
    reader.validate_citation(citation)
    with pytest.raises(HTTPException) as stale:
        governance.change_status(doc["id"], expected, "active")
    assert stale.value.status_code == 409
    expected.expected_status = "inactive"
    governance.change_status(doc["id"], expected, "active")
    assert doc["id"] in reader.document_ids()
    expected.expected_status = "active"
    governance.change_status(doc["id"], expected, "deleted")
    assert library.documents(include_inactive=True) == []
    reader.validate_citation(citation)
    assert import_service.original(doc["id"], revision) == ("source.docx", data)
    with pytest.raises(HTTPException) as removed:
        import_service.publish(
            "alice",
            data,
            "source.docx",
            plan(data, document_id=doc["id"], expected_revision=revision),
        )
    assert removed.value.status_code == 404
    other = import_service.publish("alice", data, "source.docx", plan(data))
    assert other["id"] != doc["id"]
    reader.validate_citation(citation)


def test_duplicate_rejected_before_storage_write_even_when_inactive(database, monkeypatch):
    from src.apps.materials.backend import governance

    data = word()
    doc = import_service.publish("alice", data, "source.docx", plan(data))
    governance.change_status(
        doc["id"],
        governance.ExpectedDocument(expected_revision=doc["revision"], expected_status="active"),
        "inactive",
    )
    monkeypatch.setattr(
        database[1], "upload_file", lambda *args: pytest.fail("duplicate wrote storage")
    )
    with pytest.raises(HTTPException) as duplicate:
        import_service.publish("alice", data, "renamed.docx", plan(data))
    assert duplicate.value.status_code == 409 and "已停用" in duplicate.value.detail
    assert len(library.documents(include_inactive=True)) == 1


def test_metadata_revision_and_chapter_comparison_keep_source_identity(database):
    from src.apps.materials.backend import governance

    data = word()
    doc = import_service.publish("alice", data, "source.docx", plan(data))
    reader = library.LibraryReader()
    revision, _, sections = reader.load(doc["id"])
    cited = reader.read_section(doc["id"], revision, sections[0].id)["citation"]
    changed = governance.update_metadata(
        "alice",
        doc["id"],
        governance.MetadataChange(
            title="新的产品标题",
            metadata={"product": "Cloud", "software_version": "8.2.1"},
            expected_revision=revision,
        ),
    )
    assert changed["revision"] != revision
    comparison = governance.compare(doc["id"], revision, changed["revision"])
    assert comparison["counts"] == {"added": 0, "removed": 0, "changed": 0}
    assert comparison["metadata"]["after"]["product"] == "Cloud"
    assert import_service.original(doc["id"], changed["revision"]) == ("source.docx", data)
    reader.validate_citation(cited)
    with pytest.raises(HTTPException) as stale:
        governance.update_metadata(
            "alice", doc["id"], governance.MetadataChange(title="stale", expected_revision=revision)
        )
    assert stale.value.status_code == 409
    newer = word(first="新章节", extra="<w:p><w:r><w:t>Changed body</w:t></w:r></w:p>")
    latest = import_service.publish(
        "alice",
        newer,
        "new.docx",
        plan(newer, document_id=doc["id"], expected_revision=changed["revision"]),
    )
    comparison = governance.compare(doc["id"], changed["revision"], latest["revision"], limit=1)
    assert comparison["counts"] == {"added": 1, "removed": 1, "changed": 1}
    assert len(comparison["items"]) == 1 and comparison["next_offset"] == 1


def test_management_mutations_are_guarded_and_not_exposed_in_assistant(database):
    app = FastAPI()
    app.include_router(api.router, prefix="/materials")
    from src.apps.assistant.backend.api import router as chat

    app.include_router(chat, prefix="/assistant")
    with TestClient(app) as client:
        for suffix in ("status", "metadata"):
            assert client.patch(f"/materials/documents/d/{suffix}", json={}).status_code == 401
            assert client.patch(f"/assistant/documents/d/{suffix}", json={}).status_code in (
                404,
                405,
            )
        assert client.request("DELETE", "/materials/documents/d", json={}).status_code == 401
        app.dependency_overrides[api.user] = lambda: SimpleNamespace(user_id="alice")
        doc = client.post(
            "/materials/documents", json={"title": "text", "text": "# Test\nUnique"}
        ).json()
        path = "/materials/documents/" + doc["id"]
        expected = {"expected_revision": doc["revision"], "expected_status": "active"}
        assert (
            client.patch(path + "/status", json={**expected, "status": "inactive"}).status_code
            == 200
        )
        assert client.request("DELETE", path, json=expected).status_code == 409
        assert (
            client.request(
                "DELETE", path, json={**expected, "expected_status": "inactive"}
            ).status_code
            == 200
        )
        assert client.get("/materials/documents").json() == []


def test_unique_constraint_closes_concurrent_import_race(database, monkeypatch):
    payload = library.ImportDocument(title="first", text="# Same\nSame bytes")
    library.import_document("alice", payload)
    # Simulate two importers both passing the optimistic preflight.
    monkeypatch.setattr(library, "check_duplicate", lambda *args: None)
    with pytest.raises(HTTPException) as duplicate:
        library.import_document("alice", payload.model_copy(update={"title": "second"}))
    assert duplicate.value.status_code == 409
    assert len(library.documents()) == 1
