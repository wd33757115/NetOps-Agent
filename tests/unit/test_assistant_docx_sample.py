import hashlib
import json
from zipfile import ZipFile

from scripts.assistant import prepare_docx_sample as sample
from src.apps.materials.backend.documents import DocumentReader


def test_local_docx_keeps_locations_literal_cli_and_does_not_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(sample, "ROOT", tmp_path)
    source = tmp_path / "source.docx"
    namespace = sample.W[1:-1]
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "word/styles.xml",
            f'<w:styles xmlns:w="{namespace}">'
            '<w:style w:styleId="h1"><w:name w:val="heading 1"/></w:style>'
            "</w:styles>",
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{namespace}"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="h1"/></w:pPr>'
            "<w:r><w:t>检查接口</w:t></w:r></w:p>"
            "<w:p><w:r><w:t># CLI output is not a heading</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r>"
            "</w:p><w:p><w:r><w:t>next line</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl></w:body></w:document>",
        )
    before = source.read_bytes()
    output, counts = sample.prepare(source, vendor="Huawei", product="Cloud Stack", version="8.2.1")
    assert output.is_relative_to(tmp_path / ".runtime" / "assistant-eval")
    assert not (tmp_path / "knowledge_base").exists()
    assert source.read_bytes() == before
    assert counts["headings"] == counts["tables"] == 1
    reader = DocumentReader(output)
    _, metadata, sections = reader.load("sample.md")
    assert len(sections) == 1
    assert "# CLI output is not a heading" in sections[0].text
    assert "cell\nnext line" in sections[0].text
    assert metadata["software_version"] == "8.2.1"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert manifest["model_calls"] == 0
    assert manifest["status"] == "text_only_requires_review"
    assert manifest["locations"][-1]["kind"] == "table_cell"
    assert sample.verify(output)["pagination_roundtrip"] is True
