<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ITSM Workflow 插件开发指南

新增一类 ITSM 工单时，**无需修改** `engine.py`、`main.py` 业务逻辑或 Celery 任务实现。只需在 `src/workflows/itsm/<插件名>/` 下添加插件包，并实现对应 Skill 脚本。

## 目录结构

```
src/workflows/itsm/<plugin-name>/
├── WORKFLOW.yaml        # 多步编排定义（Skill 链 + 表达式输入 + on_complete）
├── ITSM.webhook.yaml    # 可选：ITSM Webhook 路由与 context 映射
└── CHAT.intent.yaml     # 可选：聊天触发规则

src/skills/<skill-name>/
├── SKILL.md             # entry_script、entry_output、celery_task
└── scripts/run.py       # CLI 入口，支持 --params params.json
```

## 1. WORKFLOW.yaml

```yaml
name: my-itsm-workflow
description: 示例 ITSM 流程
version: "1.0"

steps:
  - name: step_one
    skill: my-skill-a
    inputs:
      ticket_id: ${context.ticket_id}
      workflow_run_id: ${run.id}

  - name: step_two
    skill: my-skill-b
    inputs:
      ticket_id: ${context.ticket_id}
      prev_result: ${steps.step_one.result.some_field}
      config_file_key: ${steps.step_one.artifacts.config_zip.file_key}
      config_files_url: ${steps.step_one.artifacts.config_zip.download_url}

on_complete:
  message: 流程已完成
  notify_each_step: false    # 单步产物类流程建议 true（见下文）
  notification:
    title: "工单完成 (${context.ticket_id})"
    body: "所有步骤已执行。"
    level: success
```

### 表达式

| 表达式 | 含义 |
|--------|------|
| `${context.ticket_id}` | Webhook/聊天写入的运行上下文 |
| `${run.id}` | 当前 Workflow Run ID |
| `${steps.<step_name>.result.*}` | 某步 Skill 返回 JSON |
| `${steps.<step_name>.artifacts.<key>.file_key}` | MinIO 对象键（供下游 Skill） |
| `${steps.<step_name>.artifacts.<key>.download_url}` | 平台产物下载地址（可为鉴权 API 地址） |

常见 artifact key：`config_zip`（策略 ZIP）、`change_excel`（变更工单 Excel）、`analysis_report`（LLM 报告）。任意 Skill 只要在结果中提供 `artifacts.<key>.download_url`，平台即可自动收集并在通知中展示。

### 单步 Workflow（仅 firewall 等）

若只有一步（如 `firewall-policy-generator`），聊天触发后**不会**在对话流中同步返回下载链接，产物通过**站内通知**交付。建议：

```yaml
on_complete:
  notify_each_step: true
  notification:
    body: "策略配置包已生成，请在步骤完成通知中下载。"
```

### 多步链路与 LLM

典型防火墙变更：`firewall-policy-generator` → `itsm-change-ticket-writer` →（可选）`itsm-callback`。

需要 LLM 解读时**显式增加** `llm-result-analyzer` 步骤；向导不会默认插入 LLM。含 LLM 时可在 `CHAT.intent.yaml` 使用 `require_any_secondary` 区分触发话术。

长流程建议 `notify_each_step: true`。

## 2. ITSM.webhook.yaml

```yaml
route_key: my-ticket-type
workflow: my-itsm-workflow
accepted_message: 已受理，正在处理
legacy_paths:
  - /api/v1/itsm/webhook/my-ticket-type

context_mapping:
  ticket_id: ticket_id
  ticket_title: ticket_title
  policy_file_url: policy_file.url
  callback_url: callback_url
```

Webhook 入口：`POST /api/v1/itsm/webhook/{route_key}`

平台在启动时扫描所有 `ITSM.webhook.yaml`，自动注册路由。

## 3. CHAT.intent.yaml

```yaml
workflow: my-itsm-workflow
priority: 50
description: 聊天触发说明
required_context: [ticket_id]

match:
  require_any:
    - 防火墙
    - 变更工单
  require_all: []
  require_any_secondary: []   # 可选，与 LLM 流程区分

response_template: |
  [OK] 已启动 Workflow `{run_id}`
  - 工单: {ticket_id}
```

### 聊天激活条件

须**同时**满足：

1. 匹配 `require_any` / `require_all` / `require_any_secondary`
2. 满足插件声明的 `required_context`；本例要求当前消息提供工单号，其他插件可声明不同字段或不要求工单号
3. 插件在治理中状态为 **published**（draft / review 不会在聊天中激活）

Supervisor 通过 `find_matching_intents()` 匹配，并按 Intent 映射及检查必需上下文。Webhook 触发的会话使用 `ITSM.webhook.yaml`，勿与聊天 Intent 混用 `auto_if_source`。

## 4. Skill 脚本约定

入口脚本由平台 `execute_skill()` subprocess 调用：

```bash
python scripts/run.py --params /tmp/params.json --output-dir /tmp/out
# 或 file 模式：-o /tmp/output.xlsx
```

- 平台会将 URL/MinIO `file_key` 下载为本地路径后再写入 `params.json`
- stdout **最后一行**必须是 JSON，例如 `{"success": true, "manifest": {...}}`
- 防火墙类 Skill 可在 JSON 中带 `_local_zip` 路径，平台会上传 MinIO 并填充 `artifacts.config_zip`

## 5. 通知与下载链接

| 配置 | 说明 |
|------|------|
| `notify_each_step: true` | 每步完成发站内通知（单步 firewall / 长流程推荐） |
| `notify_each_step: false` | 默认，仅流程结束或失败时通知 |
| `notify_on_failure: true` | 失败时通知（默认开启） |

平台从步骤 `artifacts.*.download_url` 和结果顶层的 URL 字段收集 `http(s)` 或 `/api/` 链接，写入通知 `payload.downloads`：

```json
{
  "downloads": [
    {
      "key": "config_zip",
      "label": "firewall_policies_REQ001.zip",
      "url": "/api/artifacts/download/example"
    }
  ]
}
```

前端通知铃铛根据 `downloads` 渲染可点击链接；无需在 YAML 中硬编码 `config_zip_url` 等字段名。上例地址仅示意，实际使用平台返回值；下载依赖对象存储及对应 API 的身份校验，不手工拼接对象存储地址。

## 6. 发布检查清单

- [ ] WORKFLOW.yaml 引用的 Skill 已在 Skills 页启用
- [ ] `${steps.*}` 步骤名与 YAML 中 `name` 一致
- [ ] CHAT.intent.yaml 与步骤链（是否含 LLM）对齐
- [ ] 治理中**发布**插件后聊天触发才激活
- [ ] 单步产物类 Workflow 设置 `notify_each_step: true`
- [ ] 保存后热重载或重启 Gateway；Celery Worker 运行中
- [ ] 向导「试跑 Workflow」或 Skills 页「开发指南」运行监控验证

## 7. 验证

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/test_workflow_plugins.py tests/unit/test_itsm_webhook_plugins.py -q
```

完整集成验证使用 `scripts/test/e2e_itsm_workflow.py`；先核对参数并准备隔离服务与测试数据，回调步骤可能向外部工单系统发送请求。单元测试无需先重启服务。

## 参考实现

| 场景 | 路径 |
|------|------|
| 防火墙三步骤（策略 + 变更工单 + 回调） | `src/workflows/itsm/itsm-firewall-change/` |
| 防火墙 + LLM 分析 | `src/workflows/itsm/itsm-firewall-llm-analysis/` |
| Skill | `firewall-policy-generator`、`itsm-change-ticket-writer`、`itsm-callback`、`llm-result-analyzer` |
