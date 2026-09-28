from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.apps.materials.backend.documents import DocumentReader


def workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "product.txt").write_text("测试产品用于演示资料检索。", encoding="utf8")
    result = DocumentWorkspace("tester", reader=DocumentReader(source), check=lambda: None)
    result.prepare(tmp_path / "workspace")
    return result


def test_unread_sources_do_not_claim_a_publication_failure(tmp_path):
    catalog = workspace(tmp_path)
    catalog.submit_answer({"answer": "unsupported claim", "status": "insufficient_evidence"})
    answer, citations = catalog.prepared_answer
    assert "未读取到" in answer
    assert "unsupported claim" not in answer
    assert not citations


def test_read_short_description_is_evidence_and_can_be_cited(tmp_path):
    catalog = workspace(tmp_path)
    filename = next(iter(catalog.files))
    catalog.record_read({
        "path": str(catalog.root / filename),
        "lines": [{"number": 1, "text": "测试产品用于演示资料检索。"}],
    })
    result = catalog.submit_answer({
        "answer": f"资料介绍此产品用于演示资料检索。[{filename}:L1]",
        "status": "answered",
    })
    assert result["accepted"]
    assert catalog.prepared_answer[1]
    catalog.submit_answer({"answer": "unsupported claim", "status": "insufficient_evidence"})
    answer, citations = catalog.prepared_answer
    assert "已读取" in answer
    assert "证据不足" in answer
    assert "确认相关章节已经发布" not in answer
    assert "unsupported claim" not in answer
    assert not citations


def test_truncated_read_does_not_claim_evidence_was_read(tmp_path):
    catalog = workspace(tmp_path)
    filename = next(iter(catalog.files))
    catalog.record_read({
        "path": str(catalog.root / filename),
        "lines": [{"number": 1, "text": "测试产品…"}],
    })
    catalog.submit_answer({"answer": "", "status": "insufficient_evidence"})
    assert "未读取到" in catalog.prepared_answer[0]
