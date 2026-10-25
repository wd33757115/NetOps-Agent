"""Bounded execution summaries and redacted file-tool previews, not reasoning."""

from copy import deepcopy
from datetime import timezone

from .repository import now

LABELS = {
    "glob": "查找资料文件",
    "grep": "搜索资料内容",
    "read": "阅读原文",
    "skill": "加载技能说明",
    "mcp__netops__submit_answer": "核对答案引用",
    "mcp__netops__inspection__read_batch": "查询巡检记录",
    "mcp__netops__inspection__search_devices": "查找设备资产",
    "mcp__netops__inspection__list_runs": "查询巡检运行与质量",
    "mcp__netops__inspection__list_events": "查询网络事件",
    "mcp__netops__inspection__list_snapshots": "查找采集证据",
    "mcp__netops__inspection__read_snapshot": "阅读采集原文",
    "mcp__netops__device_access__compare_baseline": "对比基线与现场",
    "mcp__netops__device_access__compare_rows": "查基线与现场条目",
    "mcp__netops__rule_governance__read_generation": "查询规则治理记录",
    "mcp__netops__topology__locate": "定位网络终端",
    "mcp__netops__topology__trace_path": "计算网络路径",
    "mcp__netops__device_access__run": "执行设备诊断命令",
    "mcp__netops__assistant__run_skill": "执行 Skill",
    "mcp__netops__assistant__start_workflow": "启动 Workflow",
    "mcp__netops__assistant__read_workflow_run": "查询 Workflow 运行",
}
STATUS = {
    "running": "正在准备资料与工具",
    "cancel_requested": "正在停止本次问答",
    "completed": "已完成",
    "failed": "本次问答未完成，可重新提问",
    "cancelled": "已停止本次问答",
    "interrupted": "运行已中断，可重新提问",
}
ERRORS = {
    "session_context_full": "本会话上下文已达到容量上限，请新建聊天继续；现有记录已保留",
    "model_output_truncated": (
        "回答达到单次输出上限，未发布被截断的配置。请指定设备角色或拆分配置范围"
    ),
    "dsh_run_failed": "Agent 执行异常，未发布答案；可使用本次运行编号排查",
    "citation_validation_failed": "回答引用未通过原文校验，请重新提问",
    # "Auto-correction failed" was wrong whenever the model answered without submitting.
    "citation_unread": "回答引用了本轮没有读取的资料原文，答案未发布，请重试",
    "evidence_unknown": "回答引用了本对话没有取得过的证据编号，答案未发布，请重试",
    "citation_format_invalid": "回答引用格式有误，答案未发布，请重试",
    "citation_range_invalid": "回答引用的行范围无效，答案未发布，请重试",
    "citation_too_large": "回答引用范围过长，答案未发布，请重试",
    "citation_missing": "回答缺少可核验的原文引用，请重试或补充资料",
    "citation_changed": "引用的资料修订不可用，请重新提问",
    "run_budget_exceeded": "本次检索已达到调用上限，请缩小问题范围后重试",
    "deadline_exceeded": "本次问答超时，已停止执行",
    "provider_timeout": "模型响应超时，请稍后重试",
    "model_unavailable": "模型服务暂不可用，请稍后重试",
    "model_configuration_unavailable": "问答模型配置不可用",
}


def stamp(value=None):
    value = value or now()
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()


def snapshot(row, *, include_outputs=False):
    data = deepcopy(row.progress_json or {"steps": [], "message": STATUS["running"]})
    if not include_outputs:
        data.pop("outputs", None)
    data.update(run_id=row.id, status=row.status, started_at=stamp(row.created_at))
    if row.status != "running":
        data["message"] = STATUS.get(row.status, "运行已结束")
    if row.status == "failed" and row.error_code in ERRORS:
        data["message"] = ERRORS[row.error_code]
    if row.status not in {"running", "cancel_requested"}:
        data.setdefault("ended_at", stamp(min(now(), row.expires_at.replace(tzinfo=timezone.utc))))
        for step in data["steps"]:
            if step["status"] == "running":
                step.update(status="interrupted", ended_at=data["ended_at"])
    return data


def advance(row, event):
    data = snapshot(row, include_outputs=True)
    if event.kind == "metrics":
        data["metrics"] = {
            key: event.data[key]
            for key in (
                "model_rounds",
                "tool_calls",
                "elapsed_ms",
                "model_input_characters",
                "usage",
                "authorization_checks",
                "timings_ms",
                "model_finish_reason",
                "sdk_finish_reason",
                "max_output_tokens",
            )
            if key in event.data
        }
    elif event.kind == "status":
        # Phase descriptions originate in the adapter, never provider output.
        message = event.data.get("message")
        if message in {"正在分析问题", "正在综合资料与工具结果"}:
            data["message"] = message
    elif event.kind == "tool_started":
        call_id = str(event.data.get("call_id", ""))[:128]
        if (
            call_id
            and len(data["steps"]) < 30
            and not any(s["id"] == call_id for s in data["steps"])
        ):
            label = LABELS.get(event.data.get("tool"), "查询业务数据")
            data["steps"].append(
                {
                    "id": call_id,
                    "label": label,
                    "detail": str(event.data.get("detail") or "")[:200],
                    "operation": str(event.data.get("operation") or "")[:1800],
                    "tool": str(event.data.get("tool") or "")[:128],
                    "status": "running",
                    "started_at": stamp(),
                }
            )
            data["message"] = label
    elif event.kind == "tool_finished":
        for step in data["steps"]:
            if step["id"] == event.data.get("call_id") and step["status"] == "running":
                step.update(
                    status="completed" if event.data.get("success") else "failed",
                    ended_at=stamp(),
                    summary=str(event.data.get("summary") or "")[:240],
                )
                output = event.data.get("output")
                if isinstance(output, dict) and isinstance(output.get("text"), str):
                    step["output"] = {
                        "text": output["text"][:12000],
                        "truncated": bool(output.get("truncated")),
                    }
                reference = event.data.get("evidence")
                if isinstance(reference, dict) and reference.get("kind") == "business":
                    data.setdefault("outputs", {})[step["id"]] = deepcopy(reference)
                    step["has_output"] = True
    row.progress_json = data
    return snapshot(row)
