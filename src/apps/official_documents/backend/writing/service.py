# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
公文写作技能业务逻辑（official-document-writing Skill 执行入口）。

来源：https://github.com/kagurananaga/official-document-writing-skill
"""

import json
import time
from pathlib import Path
from typing import Any


from src.apps.official_documents.backend.writing.schema import OfficialDocumentJSON




# ==================== 2. LLM 客户端 ====================
def get_document_llm():
    """获取公文写作专用的 LLM 客户端"""
    from langchain_deepseek import ChatDeepSeek

    from src.common.config import get_settings
    settings = get_settings()

    # LLM 模型名称
    model = getattr(settings, 'LLM_MODEL', None) or getattr(settings, 'LLM_PROVIDER', 'deepseek-chat')
    if model == "deepseek":
        model = "deepseek-chat"

    # API Key
    api_key = settings.DEEPSEEK_API_KEY

    return ChatDeepSeek(
        model=model,
        api_key=api_key,
        base_url=settings.DEEPSEEK_BASE_URL,
        # Structured tool calls require non-thinking mode on DeepSeek v4.
        extra_body={"thinking": {"type": "disabled"}},
        temperature=0.3,  # 较低温度，保持准确性
        max_tokens=4000   # 足够的 token 生成完整公文
    )


# ==================== 3. 读取参考文档 ====================
def get_skill_base_path() -> Path:
    """获取 SKILL.md 资源目录（references / checklists）。"""
    return Path(__file__).resolve().parents[2] / "skills/official-document-writing"


def read_reference_file(relative_path: str) -> str:
    """读取参考文档内容"""
    try:
        base_path = get_skill_base_path()
        file_path = base_path / relative_path

        if file_path.exists():
            with open(file_path, encoding='utf-8') as f:
                return f.read()
        else:
            print(f"[WARN] Reference file not found: {file_path}")
            return ""
    except Exception as e:
        print(f"[WARN] Failed to read {relative_path}: {e}")
        return ""


def get_document_templates() -> str:
    """获取公文模板"""
    content = read_reference_file("references/document-templates.md")
    if not content:
        # 如果文件不存在，返回基本模板
        content = """
# 请示模板
[发文机关] 关于 [请示事由] 的请示

[主送机关]：

[导语/依据]：
根据[法律、法规、政策依据]，[情况说明]。现将有关问题请示如下：

[主体/请示内容]：
一、[具体事项1]

二、[具体事项2]

[结尾/请求语]：
妥否，请批示。

[发文机关署名]
[成文日期]
"""
    return content


def get_gb_standard() -> str:
    """获取 GB/T 9704-2012 标准"""
    return read_reference_file("references/gb-t-9704-2012-standard.md")


def get_quality_checklist() -> str:
    """获取质量检查清单"""
    return read_reference_file("checklists/quality-checklist.md")




# ==================== 5. 上传到 MinIO ====================
def upload_document_to_minio(file_data: bytes, filename: str) -> str:
    """
    上传公文文件到 MinIO，返回下载链接
    """
    try:
        from src.infrastructure.storage.minio_client import get_minio_storage

        minio = get_minio_storage()

        if not minio.is_ready():
            print("[WARN] MinIO not ready, skipping upload")
            return None

        # 生成对象名称
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        object_name = f"documents/{timestamp}_{filename}"

        # 上传文件
        success = minio.upload_file(
            object_name=object_name,
            file_data=file_data,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

        if success:
            # 生成下载链接（有效期7天）
            download_url = minio.get_presigned_url(object_name, expires=3600 * 24 * 7)
            print(f"   [MinIO] Document uploaded: {object_name}")
            print(f"   [MinIO] Download URL: {download_url}")
            return download_url
        else:
            print("[WARN] Failed to upload document to MinIO")
            return None

    except Exception as e:
        print(f"[WARN] MinIO upload error: {e}")
        return None


# ==================== 6. LLM 生成结构化 JSON ====================
async def generate_document_json_with_llm(
    document_type: str,
    purpose: str,
    user_query: str,
    templates: str,
    gb_standard: str,
) -> OfficialDocumentJSON | None:
    """使用 LLM 结构化输出公文 JSON（必须可被渲染脚本消费）。"""
    llm = get_document_llm()
    structured_llm = llm.with_structured_output(OfficialDocumentJSON, method="function_calling")

    prompt = f"""你是党政机关公文写作专家。请根据用户需求生成**可直接渲染为 Word 文档**的结构化 JSON。

## 用户需求
{user_query}

## 文种
{document_type}

## 用途/背景
{purpose or '（从用户需求推断）'}

## 模板参考（节选）
{templates[:2500]}

## 格式规范（节选）
{(gb_standard or '遵循 GB/T 9704-2012')[:1500]}

## 硬性要求
1. 必须输出完整 JSON 对象，字段齐全、内容具体，禁止占位符如「XXX」「待补充」
2. title 格式：「关于……的{document_type}」
3. main_body.opening 写依据与背景；sections 至少 1 节；closing 使用规范结尾语
4. signature.date 使用中文日期，如 2026年5月24日
5. 不要输出 Markdown 或解释文字，只输出 JSON 结构
"""

    try:
        t_start = time.time()
        result = await structured_llm.ainvoke(prompt)
        elapsed = time.time() - t_start
        print(f"   [Document JSON] LLM took {elapsed:.1f}s")
        if isinstance(result, OfficialDocumentJSON):
            return result
        return OfficialDocumentJSON.model_validate(result)
    except Exception as e:
        print(f"   [Document JSON] structured output failed: {e}")
        return _parse_document_json_from_text(
            await _generate_document_json_fallback_text(
                document_type, purpose, user_query, templates, gb_standard
            )
        )


async def _generate_document_json_fallback_text(
    document_type: str,
    purpose: str,
    user_query: str,
    templates: str,
    gb_standard: str,
) -> str | None:
    """结构化输出失败时的 JSON 文本兜底。"""
    llm = get_document_llm()
    from src.apps.official_documents.backend.writing.schema import OfficialDocumentJSON

    schema_hint = OfficialDocumentJSON.model_json_schema()
    prompt = f"""请仅输出一个 JSON 对象（不要 markdown 代码块），Schema 如下：
{json.dumps(schema_hint, ensure_ascii=False)}

用户需求：{user_query}
文种：{document_type}
用途：{purpose or '无'}

模板参考：{templates[:1500]}
"""
    try:
        response = await llm.ainvoke(prompt)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        print(f"   [Document JSON] fallback LLM error: {e}")
        return None


def _parse_document_json_from_text(text: str | None) -> OfficialDocumentJSON | None:
    if not text:
        return None
    import json
    import re

    from src.apps.official_documents.backend.writing.schema import OfficialDocumentJSON

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return OfficialDocumentJSON.model_validate(json.loads(match.group()))
    except Exception as e:
        print(f"   [Document JSON] parse error: {e}")
        return None


# ==================== 5. Handler 函数 ====================
async def official_document_writing_handler(params: dict[str, Any]) -> dict[str, Any]:
    """
    公文写作处理函数

    提供党政机关公文写作的完整服务，包括：
    - 使用 LLM 生成符合规范的公文
    - GB/T 9704-2012格式规范
    - 常用公文模板
    - 公文质量检查
    """
    start_time = time.time()

    try:
        document_type = params.get("document_type")
        content = params.get("content")
        purpose = params.get("purpose")
        action = params.get("action", "write")
        user_query = params.get("user_query", "")

        # 如果用户没有提供 document_type，从 user_query 中推断
        if not document_type and user_query:
            document_type = infer_document_type(user_query)

        # 读取参考文档
        templates = get_document_templates()
        gb_standard = get_gb_standard()
        checklist = get_quality_checklist()

        # 处理不同操作类型
        if action == "review" or action == "check":
            # 审核/检查模式
            result = await review_document(content, document_type, checklist, gb_standard)

        elif action == "guide":
            # 指导模式
            result = await guide_document(document_type, templates)

        else:
            # 默认撰写模式 - 调用 LLM 生成公文
            result = await write_document(document_type, purpose, user_query, templates, gb_standard)

        elapsed = time.time() - start_time
        result["execution_time_ms"] = int(elapsed * 1000)

        return result

    except Exception as e:
        print(f"   [Official Document Writing] Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"公文写作处理失败: {str(e)}",
            "error": str(e),
            "execution_time_ms": 0
        }


def infer_document_type(query: str) -> str:
    """从用户查询中推断公文类型"""
    query_lower = query.lower()

    if "请示" in query_lower:
        return "请示"
    elif "通知" in query_lower:
        return "通知"
    elif "函" in query_lower:
        return "函"
    elif "纪要" in query_lower:
        return "会议纪要"
    elif "报告" in query_lower:
        return "报告"
    elif "总结" in query_lower:
        return "工作总结"
    elif "决定" in query_lower:
        return "决定"
    else:
        return "通用公文"


async def write_document(
    document_type: str,
    purpose: str,
    user_query: str,
    templates: str,
    gb_standard: str
) -> dict[str, Any]:
    """撰写公文：LLM 结构化 JSON → 渲染 DOCX → 上传 MinIO（同步链路）。"""

    print(f"   [Document Writing] Generating {document_type} (sync + JSON)...")

    document_json = await generate_document_json_with_llm(
        document_type=document_type,
        purpose=purpose,
        user_query=user_query,
        templates=templates,
        gb_standard=gb_standard,
    )

    if not document_json:
        return {
            "success": False,
            "message": "公文内容生成失败：LLM 未返回有效 JSON",
            "error": "invalid_document_json",
        }

    from src.apps.official_documents.backend.writing.render import render_document_bytes

    print("   [Document Writing] Rendering DOCX from JSON...")
    try:
        docx_data = render_document_bytes(document_json)
    except Exception as e:
        print(f"   [Document Writing] Render failed: {e}")
        return {
            "success": False,
            "message": f"公文渲染失败: {e}",
            "error": str(e),
            "data": {"document_json": document_json.model_dump()},
        }

    import datetime

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{document_json.doc_type}_{timestamp}.docx"
    download_url = upload_document_to_minio(docx_data, filename)

    flat_text = document_json.to_render_context().get("main_body_text", "")
    preview = f"{document_json.title}\n\n{flat_text}\n\n{document_json.signature.org}\n{document_json.signature.date}"

    return {
        "success": True,
        "message": "公文撰写完成，已生成可下载 Word 文档",
        "filename": filename,
        "data": {
            "document_type": document_json.doc_type,
            "purpose": purpose,
            "action": "write",
            "document_json": document_json.model_dump(),
            "document_content": preview,
            "workflow": ["需求确认", "JSON 生成", "模板渲染", "DOCX 上传", "返回下载链接"],
        },
        "download_url": download_url,
    }


async def review_document(
    content: str,
    document_type: str,
    checklist: str,
    gb_standard: str
) -> dict[str, Any]:
    """审核公文"""
    llm = get_document_llm()

    prompt = f"""你是一位专业的公文审核专家，请审核以下公文内容：

## 公文类型
{document_type or '通用公文'}

## 待审核内容
{content}

## 质量检查清单
{checklist[:2000]}

## GB/T 9704-2012 格式规范
{gb_standard[:1500] if gb_standard else '请遵循党政机关公文格式规范'}

## 审核要求
请从以下方面进行审核：
1. 格式规范：标题、主送机关、正文、落款等格式是否正确
2. 语言规范：是否准确、平实、简明、庄重
3. 内容质量：逻辑是否清晰，依据是否充分
4. 法规合规：是否符合相关政策法规要求

请提供详细的审核意见和改进建议。

格式：
### 审核结果：通过 / 需要修改
### 格式问题：（如有）
### 内容问题：（如有）
### 修改建议：
"""

    try:
        t_start = time.time()
        response = await llm.ainvoke(prompt)
        elapsed = time.time() - t_start

        print(f"   [Document Review] LLM took {elapsed:.1f}s")

        review_result = response.content if hasattr(response, 'content') else str(response)

        return {
            "success": True,
            "message": "公文审核完成",
            "data": {
                "document_type": document_type,
                "action": "review",
                "review_result": review_result,
                "reference_files": [
                    "checklists/quality-checklist.md",
                    "references/gb-t-9704-2012-standard.md"
                ]
            }
        }

    except Exception as e:
        print(f"   [Document Review] LLM Error: {e}")
        return {
            "success": False,
            "message": "公文审核失败，请重试。以下仅为通用检查提示，并非本次审核结果。",
            "data": {
                "document_type": document_type,
                "action": "review",
                "suggestions": [
                    "请对照GB/T 9704-2012标准检查格式规范",
                    "检查字体字号是否符合要求",
                    "检查结构层次序数是否正确",
                    "检查成文日期格式是否正确",
                    "检查语言是否准确、平实、简明、庄重"
                ],
                "reference_files": [
                    "checklists/quality-checklist.md",
                    "references/gb-t-9704-2012-standard.md"
                ]
            }
        }


async def guide_document(
    document_type: str,
    templates: str
) -> dict[str, Any]:
    """公文写作指导"""
    llm = get_document_llm()

    # 提取对应类型的模板
    template_section = ""
    if document_type:
        lines = templates.split('\n')
        in_section = False
        section_lines = []

        for line in lines:
            if document_type in line and ('模板' in line or '###' in line):
                in_section = True
                section_lines.append(line)
            elif in_section:
                if line.startswith('## ') and document_type not in line:
                    break
                section_lines.append(line)

        template_section = '\n'.join(section_lines[:50])

    prompt = f"""你是一位专业的公文写作导师，请提供关于"{document_type}"的写作指导。

## 模板参考
{template_section}

## 指导要求
请提供：
1. {document_type}的定义和使用场景
2. 写作要点和注意事项
3. 常用句式和表达
4. 写作示例

请用简洁专业的语言进行指导。"""

    try:
        t_start = time.time()
        response = await llm.ainvoke(prompt)
        elapsed = time.time() - t_start

        print(f"   [Document Guide] LLM took {elapsed:.1f}s")

        guide_content = response.content if hasattr(response, 'content') else str(response)

        return {
            "success": True,
            "message": guide_content,
            "data": {
                "document_type": document_type,
                "action": "guide",
                "writing_principles": ["准确", "平实", "简明", "庄重"],
                "reference_files": [
                    "references/document-templates.md",
                    "references/writing-techniques.md"
                ]
            }
        }

    except Exception as e:
        print(f"   [Document Guide] LLM Error: {e}")
        # 返回基本指导
        templates_dict = {
            "请示": "请示写作要点：明确请示依据，说清请示事项，一文一事，结尾用'妥否，请批示'",
            "通知": "通知写作要点：说明通知依据，明确通知事项，提出执行要求，结尾用'特此通知'",
            "函": "函写作要点：说明发函依据，明确商洽事项，注意语气得体，结尾用'请予函复'或'特此函复'",
            "总结": "总结写作要点：概括主要成绩，提炼经验做法，查找存在问题，明确今后打算",
            "纪要": "纪要写作要点：记录会议信息完整，客观记录讨论情况，明确议定事项，落实责任分工",
            "报告": "报告写作要点：真实准确，条理清晰，重点突出，报告及时"
        }

        guide_content = templates_dict.get(
            document_type,
            f"正在为您提供{document_type}的写作指导..."
        )

        return {
            "success": True,
            "message": guide_content,
            "data": {
                "document_type": document_type,
                "action": "guide",
                "writing_principles": ["准确", "平实", "简明", "庄重"],
                "reference_files": [
                    "references/document-templates.md",
                    "references/writing-techniques.md"
                ]
            }
        }


if __name__ == "__main__":
    import asyncio

    async def test():
        result = await official_document_writing_handler(
            {
                "document_type": "请示",
                "purpose": "申请采购新的网络设备",
                "user_query": "帮我写一份请示，向信息中心申请采购一台新的核心交换机",
                "action": "write",
            }
        )
        print(f"success={result.get('success')} message={result.get('message', '')[:80]}")

    asyncio.run(test())
