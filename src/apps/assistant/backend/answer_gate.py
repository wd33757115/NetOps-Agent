"""A read-only MCP draft check; retries remain inside the official DSH agent loop."""

import re

MAX_ANSWER_CHARACTERS = 24000

DEFINITION = {
    "name": "submit_answer",
    "description": (
        "Check a draft against sources actually read this turn. Call before the final response. "
        "On rejection, use the feedback to read missing lines or fix citations and call again. "
        "This does not publish to the user or execute a business action. "
        "Use insufficient_evidence when the published sources cannot answer the question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "maxLength": MAX_ANSWER_CHARACTERS},
            "status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
        },
        "required": ["answer", "status"],
        "additionalProperties": False,
    },
}

REASONS = {
    "Malformed source reference": (
        "citation_format_invalid",
        "引用格式不正确，请使用本轮已读位置标记。",
    ),
    "Invalid source line range": ("citation_range_invalid", "引用行范围无效或过长，请缩小范围。"),
    "Answer cites unread lines": (
        "citation_unread",
        "引用包含未实际读取的行，请补读或移除不必要的引用。",
    ),
    "Citation exceeds read budget": ("citation_too_large", "单条引用过长，请引用必要原文。"),
    "Read sources were not cited": (
        "citation_missing",
        "回答缺少原文引用；若正文不足，请选择资料不足。",
    ),
    "Business observations were not cited": ("citation_missing", "业务结论缺少本轮证据引用。"),
    "Answer cites unread business evidence": (
        "evidence_unknown",
        "这些方括号不是本轮或本对话之前取得的证据编号，请改成工具返回的编号或删掉：",
    ),
    "citation_mismatch": ("citation_changed", "引用修订或原文内容不一致，请重新读取。"),
}


def reason(error):
    return REASONS.get(str(error), ("citation_changed", "引用对应的原始修订不可用，请重新读取。"))


def submit(workspace, arguments):
    workspace.check()
    workspace.prepared_answer = None
    workspace.validation_issue = "citation_missing"
    if (
        set(arguments) != {"answer", "status"}
        or not isinstance(arguments["answer"], str)
        or len(arguments["answer"]) > MAX_ANSWER_CHARACTERS
        or arguments["status"] not in {"answered", "insufficient_evidence"}
    ):
        return {"accepted": False, "code": "invalid_answer", "message": "回答参数无效。"}
    if arguments["status"] == "insufficient_evidence":
        # No model-authored factual claims escape validation under a refusal label.
        if any(workspace.observed.values()) or workspace.business_evidence:
            text = (
                "本轮已读取资料或业务记录，但现有证据不足以支持可靠结论。"
                "这不代表资料未发布或检索失败。请补充与问题相关的详细说明或验证依据，"
                "也可以进一步明确想了解的内容。"
            )
        else:
            text = (
                "本轮未读取到可用于回答问题的正文或业务证据。"
                "请在资料管理中确认相关资料已发布，或调整关键词和问题范围后重试。"
            )
        workspace.prepared_answer = (text, [])
        workspace.validation_issue = None
        return {"accepted": True, "status": "insufficient_evidence", "message": "已记录资料不足。"}
    if not arguments["answer"].strip():
        return {"accepted": False, "code": "empty_answer", "message": "回答不能为空。"}
    try:
        references = workspace.references(arguments["answer"])
        if not references:
            raise ValueError("Read sources were not cited")
    except ValueError as exc:
        code, message = reason(exc)
        workspace.validation_issue = code
        if code == "evidence_unknown":
            # Name each one: a vague refusal once cost two extra rounds over "[evidence-...]".
            message += "、".join(workspace.unknown_evidence(arguments["answer"])[:12])
        return {
            "accepted": False,
            "code": code,
            "message": message,
            "read_locations": workspace.citation_guidance(),
            "submitted_locations": re.findall(
                r"\[(?:[a-f0-9]{20}/line-\d+|attachments/[a-f0-9]{12})\.txt:L\d+(?:-L?\d+)?\]",
                arguments["answer"],
            )[:12],
            "next_step": "补读所缺正文、修正引用后重新提交；资料不足时用 insufficient_evidence。",
        }
    workspace.prepared_answer = (arguments["answer"], references)
    workspace.validation_issue = None
    return {"accepted": True, "references": len(references), "message": "校验通过，请结束本轮。"}
