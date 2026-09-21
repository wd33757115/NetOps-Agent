# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""公文写作 Skill 结构化 JSON Schema（供 LLM 与渲染脚本共用）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentSection(BaseModel):
    heading: str = Field(..., description="段落标题，如「一、申请事由」")
    content: str = Field(..., description="段落正文")


class DocumentMainBody(BaseModel):
    opening: str = Field(..., description="导语/依据段")
    sections: list[DocumentSection] = Field(default_factory=list, description="主体分节")
    closing: str = Field("", description="结尾/请求语，如「妥否，请批示。」")


class DocumentSignature(BaseModel):
    org: str = Field(..., description="发文机关署名")
    date: str = Field(..., description="成文日期，如 2026年5月24日")


class OfficialDocumentJSON(BaseModel):
    """LLM 必须输出的公文 JSON 结构。"""

    doc_type: str = Field(..., description="文种：请示、通知、函、报告、工作总结、会议纪要等")
    issuer: str = Field("", description="发文机关（可选，可留空由用户后填）")
    title: str = Field(..., description="公文标题，如「关于XXX的请示」")
    main_recipient: str = Field(..., description="主送机关")
    main_body: DocumentMainBody
    signature: DocumentSignature

    def to_render_context(self) -> dict:
        """转换为 docxtpl 渲染上下文。"""
        lines = [self.main_body.opening]
        for section in self.main_body.sections:
            if section.heading:
                lines.append(section.heading)
            lines.append(section.content)
        if self.main_body.closing:
            lines.append(self.main_body.closing)
        return {
            "doc_type": self.doc_type,
            "issuer": self.issuer,
            "title": self.title,
            "main_recipient": self.main_recipient,
            "opening": self.main_body.opening,
            "sections": [s.model_dump() for s in self.main_body.sections],
            "closing": self.main_body.closing,
            "signature_org": self.signature.org,
            "signature_date": self.signature.date,
            "main_body_text": "\n".join(lines),
        }
