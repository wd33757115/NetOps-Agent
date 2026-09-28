# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""公文写作 Skill：JSON Schema 与 DOCX 渲染（无 LLM）。"""

from src.skills.official_document.render import ensure_default_templates, render_document_bytes
from src.skills.official_document.schema import (
    DocumentMainBody,
    DocumentSection,
    DocumentSignature,
    OfficialDocumentJSON,
)
from src.skills.registry import skill_registry


def test_official_document_json_render_produces_docx_bytes():
    doc = OfficialDocumentJSON(
        doc_type="请示",
        issuer="信息中心",
        title="关于采购核心交换机的请示",
        main_recipient="局领导",
        main_body=DocumentMainBody(
            opening="根据网络建设需要，现就采购事项请示如下：",
            sections=[
                DocumentSection(heading="一、申请事由", content="现网核心交换机已运行满五年，需更新换代。"),
            ],
            closing="妥否，请批示。",
        ),
        signature=DocumentSignature(org="信息中心", date="2026年5月24日"),
    )
    ensure_default_templates()
    data = render_document_bytes(doc)
    assert isinstance(data, bytes)
    assert len(data) > 1000
    assert data[:2] == b"PK"


def test_document_application_is_not_registered_as_a_global_skill():
    skill_registry.sync_skills_from_files(force_replace=True)
    assert skill_registry.get_metadata("official-document-writing") is None
