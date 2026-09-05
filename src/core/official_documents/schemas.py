from __future__ import annotations

import hashlib
import json
import uuid
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Run(StrictModel):
    text: str = Field(max_length=20000)
    marks: list[Literal["bold", "underline"]] = Field(default_factory=list, max_length=2)


class Block(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    role: Literal["issuer", "title", "main_recipient", "opening", "section_heading", "body", "closing", "signature_org", "signature_date"] = "body"
    level: int = Field(default=1, ge=1, le=3)
    runs: list[Run] = Field(default_factory=list, max_length=1000)


class Content(StrictModel):
    schema_version: Literal[1] = 1
    doc_type: str = Field(default="通知", min_length=1, max_length=30)
    blocks: list[Block] = Field(min_length=1, max_length=1000)
    unresolved_facts: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_structure(self):
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("段落 ID 重复")
        for role in ("issuer", "title", "main_recipient", "opening", "closing", "signature_org", "signature_date"):
            if sum(b.role == role for b in self.blocks) > 1:
                raise ValueError(f"{role} 只能出现一次")
        if not any(b.role == "title" for b in self.blocks):
            raise ValueError("缺少标题段落")
        if sum(len(r.text) for b in self.blocks for r in b.runs) > 100000:
            raise ValueError("文档超过 10 万字")
        return self


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def block_text(block: dict) -> str:
    return "".join(r["text"] for r in block["runs"])


def blank_content(doc_type="通知", title="未命名公文") -> dict:
    roles = [("title", title), ("main_recipient", ""), ("opening", ""), ("body", ""), ("signature_org", ""), ("signature_date", "")]
    return Content(doc_type=doc_type, blocks=[Block(id=uuid.uuid4().hex, role=role, runs=[Run(text=text)]) for role, text in roles]).model_dump()


def blocking_issues(content: dict) -> list[dict]:
    result = []
    required = [("title", "标题"), ("signature_org", "发文机关"), ("signature_date", "成文日期")]
    from .templates import resolve_template
    if resolve_template(content["doc_type"])["recipient_required"]:
        required.append(("main_recipient", "主送机关"))
    for role, label in required:
        block = next((b for b in content["blocks"] if b["role"] == role), None)
        if not block or not block_text(block).strip() or (role == "title" and block_text(block) == "未命名公文"):
            result.append({"id": role, "block_id": block["id"] if block else None, "severity": "error", "category": "structure", "message": f"请补充{label}"})
    if not any(b["role"] == "body" and block_text(b).strip() for b in content["blocks"]):
        result.append({"id": "body", "block_id": None, "severity": "error", "category": "structure", "message": "请补充正文内容"})
    signature = next((b for b in content["blocks"] if b["role"] == "signature_date"), None)
    if signature and block_text(signature).strip():
        parts = re.fullmatch(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", block_text(signature).strip())
        try:
            if not parts:
                raise ValueError("invalid date")
            date(*map(int, parts.groups()))
        except ValueError:
            result.append({"id": "signature_date_format", "block_id": signature["id"], "severity": "error", "category": "structure", "message": "请填写有效成文日期，如2026年9月5日"})
    text = "\n".join(block_text(b) for b in content["blocks"])
    if content["unresolved_facts"] or "待确认" in text or "待补充" in text:
        result.append({"id": "facts", "block_id": None, "severity": "error", "category": "facts", "message": "仍有待确认事实，请补齐后定稿"})
    return result


class SaveRequest(StrictModel):
    base_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=100)
    content: Content


class RevisionRequest(StrictModel):
    base_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=100)


class CreateRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="未命名公文", min_length=1, max_length=500)
    doc_type: str = Field(default="通知", min_length=1, max_length=30)


class AssistantRequest(RevisionRequest):
    action: Literal["generate", "rewrite", "review", "answer"]
    instruction: str = Field(min_length=1, max_length=20000)
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_placeholders: bool = True
    parent_operation_id: str | None = None


class Patch(StrictModel):
    op: Literal["replace_block", "insert_after", "delete_block"]
    block_id: str
    value: Block | None = None


class Issue(StrictModel):
    id: str
    block_id: str | None = None
    severity: Literal["error", "warning", "info"] = "warning"
    category: str = "language"
    message: str
    suggestion: str = ""
    patches: list[Patch] = Field(default_factory=list, max_length=30)


class AIResult(StrictModel):
    kind: Literal["clarification", "proposal", "review", "answer"]
    message: str
    questions: list[str] = Field(default_factory=list, max_length=10)
    content: Content | None = None
    patches: list[Patch] = Field(default_factory=list, max_length=100)
    issues: list[Issue] = Field(default_factory=list, max_length=100)


def apply_patches(content: dict, patches: list[dict]) -> dict:
    result = json.loads(json.dumps(content))
    for patch in patches:
        p = Patch.model_validate({k: v for k, v in patch.items() if k != "expected_hash"})
        index = next((i for i, b in enumerate(result["blocks"]) if b["id"] == p.block_id), None)
        if index is None:
            raise ValueError("修改目标已不存在")
        original = result["blocks"][index]
        if patch.get("expected_hash") and digest(original) != patch["expected_hash"]:
            raise ValueError("修改目标已变化")
        if p.op == "delete_block":
            result["blocks"].pop(index)
        else:
            if p.value is None:
                raise ValueError("修改内容为空")
            value = p.value.model_dump()
            value["id"] = original["id"] if p.op == "replace_block" else uuid.uuid4().hex
            if p.op == "replace_block":
                result["blocks"][index] = value
            else:
                result["blocks"].insert(index + 1, value)
    return Content.model_validate(result).model_dump()
