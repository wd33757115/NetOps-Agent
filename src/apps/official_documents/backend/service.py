from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from src.infrastructure.db.models import Conversation, Message
from .models import OfficialDocument as Document, OfficialDocumentVersion as Version, OfficialDocumentOperation as Operation, now
from .schemas import Content, blank_content, block_text, digest, blocking_issues, apply_patches


def owned(s: Session, document_id: str, owner: str, *, deleted=False, lock=False):
    q = select(Document).where(Document.id == document_id, Document.owner_id == owner)
    if not deleted:
        q = q.where(Document.deleted_at.is_(None))
    if lock:
        q = q.with_for_update()
    doc = s.scalar(q)
    if not doc:
        raise HTTPException(404, "公文不存在")
    return doc


def version(s, doc, number=None):
    v = s.scalar(select(Version).where(Version.document_id == doc.id, Version.version == (number or doc.current_version)))
    if not v:
        raise HTTPException(404, "版本不存在")
    return v


def snapshot(s, doc, content, actor, key, source, summary):
    content = Content.model_validate(content).model_dump()
    s.add(Version(id=uuid.uuid4().hex, document_id=doc.id, version=doc.current_version,
                  content_json=content, content_hash=digest(content), plain_text="\n".join(block_text(b) for b in content["blocks"]),
                  change_source=source, change_summary=summary[:500], created_by=actor, request_id=key))
    doc.title = next(block_text(b) for b in content["blocks"] if b["role"] == "title")[:500] or "未命名公文"
    doc.document_type = content["doc_type"]
    doc.updated_at = now()
    s.flush()


def describe(s, doc, *, content=True):
    data = {k: getattr(doc, k) for k in ("id", "title", "document_type", "status", "revision", "current_version", "created_at", "updated_at", "deleted_at")}
    if content:
        data["content"] = version(s, doc).content_json
        data["validation_issues"] = blocking_issues(data["content"])
    return data


def create(s, owner, request):
    values = request.model_dump()
    h = digest(values)
    # Lock the authenticated user's conversation namespace is unnecessary: deterministic
    # IDs plus the unique create key protect retried and concurrent requests.
    existing = s.scalar(select(Document).where(Document.owner_id == owner, Document.create_key == request.request_id))
    if existing:
        if existing.create_hash != h:
            raise HTTPException(409, "同一请求编号对应不同内容")
        return describe(s, existing)
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"official:{owner}:{request.request_id}").hex
    conversation_id = f"doc-{identifier}"
    doc = Document(id=identifier, owner_id=owner, title=request.title, document_type=request.doc_type,
                   conversation_id=conversation_id, create_key=request.request_id, create_hash=h,
                   status="draft", revision=0, current_version=1)
    s.add(doc)
    s.add(Conversation(id=conversation_id, user_id=owner, title=request.title, status="document", is_deleted=False))
    s.flush()
    snapshot(s, doc, blank_content(request.doc_type, request.title), owner, request.request_id, "user", "创建公文")
    return describe(s, doc)


def check_revision(doc, revision):
    if doc.revision != revision:
        raise HTTPException(409, "文档已在其他页面修改，请保留本地稿并加载最新版本")


def receipt(doc, key, body):
    previous = (doc.mutation_receipts or {}).get(key)
    if previous and previous != digest(body):
        raise HTTPException(409, "同一请求编号对应不同操作")
    return bool(previous)


def record_receipt(doc, key, body):
    values = dict(doc.mutation_receipts or {})
    values[key] = digest(body)
    # Content saves have their own permanent unique request IDs on immutable versions.
    doc.mutation_receipts = values


def save(s, doc, owner, request, *, source="user", summary="编辑公文", force=False):
    content = request.content.model_dump()
    old = s.scalar(select(Version).where(Version.document_id == doc.id, Version.request_id == request.request_id))
    if old:
        if old.content_hash != digest(content):
            raise HTTPException(409, "保存请求编号已被使用")
        return describe(s, doc)
    check_revision(doc, request.base_revision)
    if doc.status == "finalized":
        raise HTTPException(409, "请先重新编辑已定稿公文")
    if not force and version(s, doc).content_hash == digest(content):
        return describe(s, doc)
    doc.revision += 1
    doc.current_version += 1
    snapshot(s, doc, content, owner, request.request_id, source, summary)
    return describe(s, doc)


def mutate(s, doc, owner, request, action, value=None):
    body = {"action": action, "value": value, "base_revision": request.base_revision}
    if receipt(doc, request.request_id, body):
        return describe(s, doc)
    check_revision(doc, request.base_revision)
    if action == "finalize":
        issues = blocking_issues(version(s, doc).content_json)
        if issues:
            raise HTTPException(422, "；".join(i["message"] for i in issues))
        doc.status = "finalized"
    elif action == "reopen":
        doc.status = "draft"
    elif action == "delete":
        doc.deleted_at = now()
        for op in s.scalars(select(Operation).where(Operation.document_id == doc.id, Operation.status.in_(["queued", "running", "proposed", "needs_input"]))):
            op.status = "cancelled"
    elif action == "undelete":
        doc.deleted_at = None
    else:
        raise HTTPException(422, "未知操作")
    doc.revision += 1
    doc.updated_at = now()
    record_receipt(doc, request.request_id, body)
    s.flush()
    return describe(s, doc)


def operation(s, doc, op_id, *, lock=False):
    q = select(Operation).where(Operation.document_id == doc.id, Operation.id == op_id)
    if lock:
        q = q.with_for_update()
    result = s.scalar(q)
    if not result:
        raise HTTPException(404, "任务不存在")
    return result


def operation_data(op):
    # Storage keys and frozen document contents never leave this response boundary.
    result = dict(op.result_json or {})
    result.pop("object_key", None)
    return {"id": op.id, "kind": op.kind, "status": op.status, "base_revision": op.base_revision,
            "base_version": op.base_version, "instruction": op.input_json.get("instruction", "导出 Word"),
            "draft": bool(op.input_json.get("draft")) if op.kind == "export" else None,
            "result": result, "error": op.error_code, "created_at": op.created_at}


def submit(s, doc, owner, request, *, kind=None, export_version=None, draft=False):
    body = request.model_dump()
    body.update({"kind": kind or body.get("action"), "export_version": export_version, "draft": draft})
    previous = s.scalar(select(Operation).where(Operation.document_id == doc.id, Operation.request_key == request.request_id))
    if previous:
        if previous.request_hash != digest(body):
            raise HTTPException(409, "同一任务编号对应不同请求")
        return previous
    check_revision(doc, request.base_revision)
    v = version(s, doc, export_version)
    ids = {b["id"] for b in v.content_json["blocks"]}
    if not set(body.get("block_ids", [])).issubset(ids):
        raise HTTPException(422, "选中的段落不存在")
    if body["kind"] in ("generate", "rewrite") and doc.status == "finalized":
        raise HTTPException(409, "请先重新编辑已定稿公文")
    if body.get("parent_operation_id"):
        parent = operation(s, doc, body["parent_operation_id"])
        if parent.status != "needs_input":
            raise HTTPException(409, "原任务不在等待补充状态")
        body["clarification_context"] = {"instruction": parent.input_json.get("instruction"), "questions": (parent.result_json or {}).get("questions", []), "reference_time": parent.input_json["reference_time"]}
        body["clarification_context"]["original_instruction"] = (parent.input_json.get("clarification_context") or {}).get("original_instruction") or parent.input_json.get("instruction", "")
        parent.status = "completed"
        parent.updated_at = now()
    payload = {**body, "content": v.content_json, "reference_time": now().isoformat()}
    from .templates import resolve_template
    payload["template"] = resolve_template(v.content_json["doc_type"])
    if body["kind"] == "generate":
        payload["block_ids"] = []
    if body.get("clarification_context"):
        payload["reference_time"] = body["clarification_context"]["reference_time"]
    if body["kind"] != "export":
        history = list(s.scalars(select(Message).where(Message.conversation_id == doc.conversation_id).order_by(Message.created_at.desc()).limit(12)))
        payload["conversation_history"] = [{"role": m.role, "content": m.content[:4000]} for m in reversed(history)]
    op = Operation(id=uuid.uuid4().hex, document_id=doc.id, owner_id=owner, kind=body["kind"], base_revision=doc.revision,
                   base_version=v.version, request_key=request.request_id, request_hash=digest({**request.model_dump(), "kind": kind or body.get("action"), "export_version": export_version, "draft": draft}), input_json=payload, status="queued")
    s.add(op)
    if op.kind != "export":
        s.add(Message(id=uuid.uuid4().hex, conversation_id=doc.conversation_id, role="user", content=body["instruction"], agent_type="official-document"))
    s.flush()
    return op


def accept(s, doc, owner, op, request, issue_id=None):
    body = {"operation": op.id, "issue_id": issue_id, "base_revision": request.base_revision}
    if receipt(doc, request.request_id, body):
        return describe(s, doc)
    if op.status not in ("proposed", "completed") or op.kind not in ("generate", "rewrite", "review"):
        raise HTTPException(409, "该任务没有可接受的修改")
    check_revision(doc, op.base_revision)
    check_revision(doc, request.base_revision)
    result = op.result_json or {}
    content = result.get("content") if op.kind == "generate" else None
    patches = result.get("patches", [])
    if issue_id:
        issue = next((i for i in result.get("issues", []) if i["id"] == issue_id), None)
        if not issue:
            raise HTTPException(404, "审查问题不存在")
        patches = issue.get("patches", [])
    if not content and not patches:
        raise HTTPException(422, "没有可应用的修改")
    content = content or apply_patches(version(s, doc).content_json, patches)
    from .schemas import SaveRequest
    data = save(s, doc, owner, SaveRequest(base_revision=request.base_revision, request_id=request.request_id, content=content), source="ai", summary=result.get("message", "AI 修改"))
    op.status = "completed"
    record_receipt(doc, request.request_id, body)
    return data
