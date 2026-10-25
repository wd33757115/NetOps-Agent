"""Skills and Workflows the assistant may run when the user asks it to.

They reach the model the DSH way: each turn writes a skill root in the DSH skill format
(``<name>/SKILL.md``), the official dsh-skill plugins list it in the session's skill
catalog, and the model loads one with DSH's ``skill`` tool before it runs anything through
``run_skill`` or ``start_workflow``. Only accounts that may execute (admin, operator) get a
catalog. A Skill is in it unless its SKILL.md says ``disable-model-invocation: true`` (the
DSH field); a Workflow when it has a CHAT.intent.yaml and is published. Everything runs as
the asking user: Skill governance (rollout) and every App action a Workflow step calls
check that user.
"""

import json
import shutil
from pathlib import Path

import yaml

from pydantic import BaseModel, ConfigDict, Field

from src.platform.tools.registry import Tool

RUN_SKILL = "assistant__run_skill"
START_WORKFLOW = "assistant__start_workflow"
READ_RUN = "assistant__read_workflow_run"
NAMES = frozenset({RUN_SKILL, START_WORKFLOW, READ_RUN})
# What goes back to the model stays well inside one evidence snapshot (24 KB).
MAX_RESULT_BYTES = 12000
MAX_TEXT = 3000


class RunSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str = Field(min_length=1, max_length=100)
    params: dict = Field(default_factory=dict)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_id: str
    name: str
    current: bool = False


class StartWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: str = Field(min_length=1, max_length=64)
    context: dict = Field(default_factory=dict)
    # Filled by the server from the conversation's attachments; never shown to the model.
    attachments: list[Attachment] = Field(default_factory=list)


class ReadRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)


def chat_skills(user_id):
    """Model-invocable Skills (DSH ``disable-model-invocation``) this user may run now."""
    from src.core.skills.resolver import SKILLS_ROOT
    from src.platform.kernel.registry import migrated_skill_app
    from src.skill_system.governance.rollout import is_skill_executable
    from src.skill_system.metadata import parse_skill_md

    found = []
    for path in sorted(Path(SKILLS_ROOT).glob("*/SKILL.md")):
        try:
            meta = parse_skill_md(path)
        except Exception:
            continue
        if not meta.model_invocable or not meta.enabled or migrated_skill_app(meta.name):
            continue
        if not is_skill_executable(meta.name, user_id=user_id)[0]:
            continue
        found.append({
            "name": meta.name,
            "description": meta.description,
            "when_to_use": meta.when_to_use,
            "instructions": meta.instructions,
            "inputs": [
                {"name": item.name, "required": item.required, "description": item.description}
                for item in meta.inputs
            ],
        })
    return found


def chat_workflows():
    """Published Workflows with a chat entry, and the context each one takes."""
    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.workflows.metadata_repo import is_plugin_chat_active
    from src.core.workflows.registry import list_templates

    templates = {template.name: template for template in list_templates()}
    found = []
    for intent in get_chat_intent_registry().all_intents():
        template = templates.get(intent.workflow)
        if template is None or not is_plugin_chat_active(intent.workflow):
            continue
        required = list(dict.fromkeys([*intent.required_context, *template.test_required_context]))
        optional = [
            key for key in dict.fromkeys([*template.test_context, *intent.context_defaults])
            if key not in required
        ]
        found.append({
            "name": template.name,
            "description": intent.description or template.description,
            "steps": " → ".join(step.label or step.name for step in template.steps),
            "required": required,
            "optional": optional,
            "defaults": dict(intent.context_defaults),
            "files": dict(intent.files),
        })
    return found


def fill_files(workflow, context, attachments, user):
    """File fields as netdisk references: named, or chosen from the attachments by type.

    A value naming an attachment (its file name) becomes that file. An empty field takes
    the one attachment of a fitting type, preferring the current message's; with two or
    more candidates nothing is guessed. Every netdisk file must be the user's to read.
    Returns the fields that stay ambiguous, with their candidates.
    """
    from src.platform import files

    ambiguous = {}
    for field, endings in workflow.get("files", {}).items():
        fitting = [item for item in attachments if item.name.lower().endswith(tuple(endings))]
        value = context.get(field)
        if value not in (None, ""):
            named = [item for item in attachments if item.name == str(value).strip()]
            if named:
                context[field] = files.reference(named[-1].file_id)
            continue
        current = [item for item in fitting if item.current]
        candidates = current or fitting
        if len(candidates) == 1:
            context[field] = files.reference(candidates[0].file_id)
        elif candidates:
            ambiguous[field] = [item.name for item in candidates]
    for field, value in context.items():
        file_id = files.file_id_of(value)
        if file_id:
            files.describe(user, file_id)  # 404 or 403 when not the user's to read
    return ambiguous


def can_execute(user):
    return user.can_execute_skills()


def _skill_file(directory, name, description, body, when_to_use=None):
    front = {"name": name, "description": description}
    if when_to_use:
        front["whenToUse"] = when_to_use
    (directory / name).mkdir()
    (directory / name / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n\n" + body,
        encoding="utf-8",
        newline="\n",
    )


def _parameters(items):
    lines = [
        f"- `{item['name']}`{'（必填）' if item['required'] else ''}"
        + (f"：{item['description']}" if item.get("description") else "")
        for item in items
    ]
    return "\n".join(lines) or "- 无"


def _skill_body(skill):
    return "\n\n".join([
        f"# Skill {skill['name']}",
        skill["description"],
        "## 在问答中执行",
        f"用户要求时调用 `mcp__netops__{RUN_SKILL}`：`skill` 填 `{skill['name']}`，`params` 填：",
        _parameters(skill["inputs"]),
        "参数只取用户给出的内容；缺少必填项时先问用户。结果、下载链接和证据编号在工具返回里。",
        "## Skill 说明",
        "以下是这个 Skill 自己的说明，用来理解它做什么；执行只用上面的工具。",
        skill.get("instructions") or "",
    ]) + "\n"


def _workflow_body(flow):
    context = [{"name": key, "required": True} for key in flow["required"]]
    context += [{"name": key, "required": False} for key in flow["optional"]]
    defaults = "、".join(f"`{key}`={value}" for key, value in flow["defaults"].items())
    return "\n\n".join([
        f"# Workflow {flow['name']}",
        flow["description"],
        f"步骤：{flow['steps']}",
        "## 启动",
        f"用户明确要求时调用 `mcp__netops__{START_WORKFLOW}`：`workflow` 填 `{flow['name']}`，"
        "`context` 填：",
        _parameters(context),
        *([f"未给出时使用默认值：{defaults}。"] if defaults else []),
        *([
            "需要的文件（" + "、".join(f"`{key}`：{'/'.join(endings)}" for key, endings in flow["files"].items())
            + "）请用户在聊天里上传附件；平台按类型自动使用，不用填写这些字段。"
            "有多个同类附件时工具返回 ambiguous，向用户确认后把选定的文件名填进对应字段。"
        ] if flow.get("files") else []),
        "参数只取用户给出的内容，不编造工单号或文件地址；工具返回 `missing` 时向用户询问。"
        "同一请求只启动一次。",
        "Workflow 在后台运行：告诉用户运行编号，完成后用户会收到站内通知；"
        f"用户问进度时调用 `mcp__netops__{READ_RUN}`。",
    ]) + "\n"


def write_skill_catalog(directory, skills, workflows):
    """A DSH skill root for this turn: the Skills and Workflows this user may run."""
    directory = Path(directory)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    for skill in skills:
        _skill_file(directory, skill["name"], skill["description"], _skill_body(skill),
                    skill.get("when_to_use"))
    taken = {skill["name"] for skill in skills}
    for flow in workflows:
        # A Workflow keeps its own name unless a Skill already has it.
        name = flow["name"] if flow["name"] not in taken else f"workflow-{flow['name']}"
        _skill_file(directory, name, f"启动 Workflow：{flow['description']}", _workflow_body(flow))
    return directory


def _bounded(value):
    """Long text is cut; a result still too large keeps only its outline."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…（已截断）"
    if isinstance(value, dict):
        return {key: _bounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded(item) for item in value[:50]]
    return value


def _downloads(artifacts):
    from src.core.workflows.artifacts import collect_download_links

    return [
        {"filename": link["label"], "download_url": link["url"]}
        for link in collect_download_links(artifacts=artifacts or {})
    ]


def _require(user):
    if not can_execute(user):
        raise PermissionError("当前账号没有执行权限（需要管理员或运维人员角色）")


def run_skill(user, body):
    from src.core.skills.executor import SkillExecutionError, execute_skill

    _require(user)
    skill = next((item for item in chat_skills(user.user_id) if item["name"] == body.skill), None)
    if skill is None:
        raise PermissionError(f"Skill {body.skill} 没有开放给问答助手，或当前账号不可用")
    missing = [
        item["name"] for item in skill["inputs"]
        if item["required"] and body.params.get(item["name"]) in (None, "", [], {})
    ]
    if missing:
        return {"skill": body.skill, "executed": False, "missing": missing,
                "message": f"执行前还需要：{'、'.join(missing)}"}
    params = {key: value for key, value in body.params.items() if not str(key).startswith("_")}
    try:
        result = execute_skill(body.skill, {**params, "_user_id": user.user_id})
    except SkillExecutionError as exc:
        return {"skill": body.skill, "executed": True, "success": False, "error": str(exc)}
    downloads = _downloads(result.get("artifacts"))
    hidden = {"artifacts", "download_url", "skill_name", "execution_id", "trace_id", "duration_ms"}
    fields = _bounded({key: value for key, value in result.items() if key not in hidden})
    reply = {"skill": body.skill, "executed": True, "result": fields, "downloads": downloads}
    if len(json.dumps(reply, ensure_ascii=False, default=str).encode()) > MAX_RESULT_BYTES:
        outline = {key: fields.get(key) for key in ("success", "message", "summary", "error")}
        reply["result"] = {**outline, "notice": "结果较大，只保留摘要"}
    return reply


def start_workflow(user, body):
    from src.core.workflows.engine import WorkflowEngine
    from src.core.workflows.registry import get_template, resolve_active_steps

    _require(user)
    workflow = next((item for item in chat_workflows() if item["name"] == body.workflow), None)
    if workflow is None:
        raise PermissionError(f"Workflow {body.workflow} 没有开放聊天启动，或尚未发布")
    accepted = set(workflow["required"]) | set(workflow["optional"])
    supplied = {
        key: value for key, value in body.context.items()
        if key in accepted and value not in (None, "", [], {})
    }
    ignored = sorted(set(body.context) - accepted)
    context = {**workflow["defaults"], **supplied}
    ambiguous = fill_files(workflow, context, body.attachments, user)
    if ambiguous:
        return {"workflow": body.workflow, "started": False, "ambiguous": ambiguous,
                "message": "有多个可用的文件，请用户确认每一项用哪一个：" + "；".join(
                    f"{field}：{'、'.join(names)}" for field, names in ambiguous.items())}
    missing = [key for key in workflow["required"] if context.get(key) in (None, "", [], {})]
    if missing:
        return {"workflow": body.workflow, "started": False, "missing": missing,
                "message": f"启动前还需要：{'、'.join(missing)}"}
    run_id = WorkflowEngine.start(body.workflow, context, source="chat", user_id=user.user_id)
    steps = resolve_active_steps(get_template(body.workflow), context, run_id=run_id)
    return {
        "workflow": body.workflow,
        "started": True,
        "run_id": run_id,
        "ticket_id": context.get("ticket_id"),
        "steps": " → ".join(step.label or step.name for step in steps),
        "ignored_fields": ignored,
        "message": "已在后台启动；完成后发送站内通知，进度可用 assistant__read_workflow_run 查询",
    }


def read_workflow_run(user, body):
    from src.core.workflows.repository import get_workflow_run, list_workflow_steps

    _require(user)
    run = get_workflow_run(body.run_id)
    if run is None or str(run.user_id or "") != str(user.user_id):
        raise PermissionError("运行不存在，或不是当前账号启动的")
    steps = list_workflow_steps(body.run_id)
    artifacts = {}
    for step in steps:
        artifacts.update(step.output_artifacts or {})
    return _bounded({
        "run_id": run.id,
        "workflow": run.template_name,
        "status": run.status,
        "ticket_id": run.ticket_id,
        "error": run.error_message,
        "started_at": run.created_at,
        "completed_at": run.completed_at,
        "steps": [
            {
                "name": step.step_name,
                "runs": step.skill_name,
                "status": step.status,
                "message": (step.result or {}).get("message"),
                "error": step.error_message or (step.result or {}).get("error"),
            }
            for step in steps
        ],
        "downloads": _downloads(artifacts),
    })


TOOLS = [
    Tool(
        "run_skill",
        "按用户要求执行一个开放给问答助手的 Skill，同步返回结果和下载链接",
        RunSkill,
        run_skill,
        True,
    ),
    Tool(
        "start_workflow",
        "按用户要求在后台启动一个开放聊天启动的 Workflow，返回运行编号",
        StartWorkflow,
        start_workflow,
        True,
    ),
    Tool(
        "read_workflow_run",
        "查询当前账号启动的 Workflow 运行的状态、步骤和下载链接",
        ReadRun,
        read_workflow_run,
    ),
]
