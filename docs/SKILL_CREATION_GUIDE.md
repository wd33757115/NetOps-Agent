# NetOps Skill 开发指南

核对日期：2026-09-27。这里的 Skill 是本项目的执行能力，通过 SKILL.md 声明元数据、输入输出和指令；真正的设备操作或文件生成仍需要已注册的执行实现。

## 与业务应用的边界

网络巡检、规则治理、防火墙策略生成、公文写作统一从工作台应用进入。`/skills` 不再提供 Parser 控制面；旧 `/skills/parser` 地址（含详情路径、查询参数和锚点）跳转至 `/apps/rule-governance`。

迁移后重复的 19 个全局 `src/skills/*/SKILL.md` 已删除，包括巡检旧步骤、规则生成器、防火墙及工单生成、公文写作注册项。应用包内部的 Skill 文档、生成器参数定义、公文参考资料和模板仍在使用，继续保留；共享资源及仍被引用的兼容 Python 模块不属于全局 Skill 注册。旧工作流定义保留用于历史读取，不再作为可执行模板提供。

全局 Skills 当前保留配置对比、设备备份、ITSM 回调和结果分析等独立工具。重新加载会移除已删除本地 Skill 的注册及向量缓存；Catalog 同步清理失效的本地发现记录，保留执行历史和外部目录注册项。

## 创建与验证

从仓库根目录执行：

```powershell
venv/Scripts/python.exe scripts/create_skill.py -n my-skill -d "我的新技能" -c network
venv/Scripts/python.exe scripts/validate_skill.py src/skills/my-skill/SKILL.md
```

创建器生成脚手架，不会凭一段 Markdown 自动实现业务。完善脚本、权限、返回契约和测试后再启用。管理端重新加载会同步目录；部署时 FastAPI 与 Worker 都应使用相同 Skill 版本。

## 定义内容

元数据以 [SkillMetadata](../src/skill_system/metadata.py) 和入口[解析器](../src/core/skills/resolver.py)为准。最小示意：

```yaml
---
name: my-skill
version: 1.0.0
description: 生成一份可审查的示例结果
category: network
triggers: [生成示例结果]
inputs:
  - name: source_id
    type: string
    required: true
    description: 已授权的来源标识
outputs:
  - name: result
    type: object
    description: 结构化结果
entry_script: scripts/run.py
entry_output: none
execution_mode: async
celery_queue: netops.default
min_permission_level: operator
enabled: false
fallback_to_rag: false
---
```

校验器要求 name、version、description、category、triggers；仅由工作流或 API 调用的内部 Skill 可以显式声明 `triggers: []`，不应为了文档校验新增自然语言触发词。tags、author、inputs/outputs、enabled、fallback_to_rag 为推荐字段。正文必须有标题、核心能力、工作流程、输出格式、示例和注意事项；核心原则、输入说明、安全规范、实际执行说明为推荐章节。具体要求以[校验脚本](../scripts/validate_skill.py)为准，不把旧指南中的固定 token 数或章节数量当接口契约。

`entry_script` 显式指向 Skill 目录内的入口。解析器仍兼容 references 和标准文件名查找；新 Skill 应声明入口和 `entry_output`，避免依赖文件名猜测输出类型。

## 执行与产物

默认异步能力由 Worker 执行，队列必须有消费者。FileBasedSkill、执行器和 runner 负责参数、工作目录、进程与产物契约；特殊同步能力仍使用明确注册的 handler。

新实现遵循 [SkillExecutionResult](../src/core/skills/result.py)，区分 success、error、partial、skipped，并提供结构化数据、错误和 ArtifactRef。文件产物使用平台附件引用，不向浏览器直接返回服务器绝对路径。输入必填、类型、所有权及作用域必须在执行边界真正验证，不能仅写在提示词里。

不要在一个 Skill 内硬编码另一个 Skill 的调用顺序。多步过程通过 Workflow 的输入表达式和产物依赖编排；短链聊天通过 ExecutionPlan 聚合。观测 trace 字段由平台传递，不属于业务 CLI 参数。

## 权限与失败

Skill metadata、平台用户角色和业务能力各有职责，需同时核对。`hidden` 只影响可见性，`enabled` 只表示启用，二者都不能替代权限。设备写入、资产审批和发布遵守对应领域服务门禁。

超时取执行配置，不统一硬写 300 秒。失败保留可诊断原因，是否回退 RAG 由场景决定；RAG 回复不能把失败的设备操作或规则发布标成成功。

## 验证与接入

测试至少覆盖输入错误、权限、成功结果、业务失败及产物可读性；有外部副作用时使用隔离目标。单纯格式校验不证明脚本可执行，也不证明实际设备质量。

业务功能通过工作台 App 和当前 React 页面接入，Skill 保持底层执行能力。不要为每个 Skill 新建系统级左侧菜单。

参考 [巡检 App Skill](../src/apps/inspection/SKILL.md)、[TextFSM 生成指南](parser-generation-guide.md)，但输入和输出必须按当前需求定义。通用工程约定见[编码规范](05_编码%20&%20工程规范.md)。

## 业务包与当前问答

新业务能力应先放入独立 App 包，SKILL.md 可附带使用说明；Agent 接入采用包的工具契约。当前问答由 DSH 选择并执行工具，旧 Skill 的触发词/目录并不自动获得新聊天入口。不要重新建立 Supervisor 图、RAG 回退或让巡检/规则任务依赖 Workflow。参见[能力包](capability-packages-guide.md)和[问答](assistant-guide.md)。
