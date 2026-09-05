# Supervisor 路由与 Skill 执行

核对日期：2026-09-06。聊天使用 [graph_v2.py](../src/agents/supervisor/graph_v2.py)，网关通过 `get_supervisor_graph()` 加载固定 v2 图。没有可切换回 v1 的启动开关。

## 聊天执行路径

1. 预处理从用户消息匹配 Skill，过滤弱匹配和知识问答误匹配，按需加载指令。
2. Supervisor 优先判断已发布可用 Workflow 的 Chat Intent。
3. 无可执行 Skill 时进入知识问答；有明确触发短链时使用启发式 ExecutionPlan，否则由模型生成结构化计划。
4. Orchestrator 按依赖派发 Skill，只有成功完成的上游可满足下游依赖。
5. 聚合器合并本轮结果，通过 SSE 返回；失败是否进入 RAG 取决于计划及 Skill 配置。

匹配、参数规划和执行授权是不同阶段。模型选中某个 Skill 不代表用户获得其权限，也不能把 RAG 回答当作设备动作完成。

## ExecutionPlan 与 Workflow

| 路径 | 定义与用途 | 结果 |
| --- | --- | --- |
| ExecutionPlan | 聊天中短链 Skill 组合，按依赖顺序或并行执行 | 同轮聚合结果 |
| Workflow | WORKFLOW.yaml 声明固定步骤、输入与恢复策略，API/Webhook/Chat Intent 启动 | 持久 run/step 状态，后台 Worker 推进 |

Workflow 启动返回“已启动”不是流程已完成。前端读取持久运行状态及 SSE 进度，步骤产物由平台归档。通用进度与 Trace 见[观测指南](observability-guide.md)。

## 插件上下文

Chat Intent 通过 `required_context` 声明必需业务字段，通用匹配器不默认要求工单号。ITSM 插件可要求 ticket_id，巡检插件按自身输入定义。上下文从已验证用户、上传与请求状态中构造，不能只凭模型文本绕过业务必填项。

Workflow 上下游通过 `context` 与步骤产物表达式关联。`build_chat_workflow_context` 是共享构造入口，旧 ITSM 专用构造副本和未挂载 Script Executor 已移除。

## Skill 加载

`bootstrap_skills` 以 SKILL.md 为来源同步可执行注册与 SkillSystem 元数据。路由、渐进式指令加载、缓存和权限在各模块中协作；新 Skill 的具体执行入口和返回契约见[开发指南](SKILL_CREATION_GUIDE.md)。

当前业务 App 不必都经聊天 Supervisor 执行。公文编辑器、巡检任务和规则自动制作有自己的业务 API；不要为了复用聊天图改变其版本、权限和发布边界。

## 代码与验证

- [Supervisor](../src/agents/supervisor/graph_v2.py)、[启发式短链](../src/agents/supervisor/heuristic_plan.py)
- [Chat Intent](../src/core/plugins/chat_intent.py)、[Workflow 引擎](../src/core/workflows/engine.py)
- [Skill 启动](../src/skills/bootstrap.py)、[注册](../src/skills/registry.py)
- [图回归](../tests/agents/test_graph_v2.py)、[通用上下文回归](../tests/unit/test_chat_workflow_context.py)

默认值、路由阈值和启用状态以代码和当前 Catalog 为准，不维护另一份固定 Skill 数量或旧关键词表。
