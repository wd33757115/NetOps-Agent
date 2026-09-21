# NetOps 目标架构建议（v0，待确认）

日期：2026-09-16　基线：main / codex/knowledge-v1.1 = 5d5f505（知识库改动回退之后）

## 0. 回退结论

- `codex/knowledge-v1.1` 没有任何提交，指向与 main、origin/main 相同的 5d5f505。Codex 的知识库工作全部是未提交的工作区改动：src 下改了 9 个文件、新增 79 个，tests/knowledge 新增 75 个，另有 Alembic 0102~0117、Django bff 0002~0010、BFF knowledge_* 视图、前端 Knowledge* 页面、scripts/knowledge 以及 204KB 的 docs/knowledge-implementation.md。
- 代码回退只需 `git stash -u`。**数据库不能靠 downgrade 回退**：0102~0109、0112 等多个迁移的 downgrade 直接 raise RuntimeError，Django 0002 的 RunPython 没有 reverse。更要紧的是，这些迁移在 `django.auth_user / auth_group / auth_user_groups` 上装了触发器，代码回退后如果不清理，用户管理相关写操作会出错。需要先执行清理 SQL。

## 1. 总体判断

现有代码的主要问题是**入口多、归属散**，而不是缺少框架：
- 前端：`appRegistry.ts` 手写 5 个应用；`App.tsx` 手写路由；巡检与设备管理共用 15 万字节的 `DeviceOperationsWorkspace`；规则工作台直接复用 `components/skills/ParserControlTab`。
- BFF：`bff/urls.py` 约 290 行，逐个接口写代理视图（workflows、parser、devices、skills 等）。
- 网关：`main.py` 手动 include 14 个 router，另外直接挂着 chat、rag、itsm、tasks、conversations 等端点。
- Agent：LangGraph Supervisor v2（pre_process → supervisor → orchestrator → skill_executor → aggregator / knowledge_qa），叠加 skill_system 路由、SKILL.md、Workflow DSL、插件市场，本质上是“意图路由 + 调 Skill”。知识问答节点只是 top-4 检索加截断 3000 字，再调用一次 LLM。
- 防火墙页面调用的是 `/skills/firewall-policy-generator/test-run/`，上传借用了聊天 thread，超时设为 340 秒。

所以方向是：**模块化单体 + 能力包清单 + 平台内核自注册**，Agent 作为能力包的另一个调用方。暂不拆微服务，也不做运行时插件市场。

## 2. 能力包（App Package）

### 2.1 目录

```
netops/
├── platform/                     # 平台内核，只放公共设施
│   ├── kernel/                   # 包加载、清单校验、注册表、依赖检查
│   ├── identity/                 # 用户、组、应用授权（取代散落的角色判断）
│   ├── gateway/                  # FastAPI 入口，按清单挂载各包 router
│   ├── agent_runtime/            # Agent 执行器接口 + 实现（见 §4）
│   ├── jobs/                     # Celery 公共配置、任务状态、事件流
│   ├── files/                    # MinIO 文件服务（上传、下载、制品）
│   └── web/                      # React 外壳：登录、桌面、Dock、通知、公共 UI
├── apps/
│   ├── inspection/               # 网络巡检
│   ├── rule-governance/          # 巡检规则治理（含设备资产）
│   ├── firewall-policy/          # 防火墙策略生成
│   ├── official-documents/       # 公文写作
│   └── assistant/                # 开放式问答 / 诊断（见 §4）
└── deploy/
```

每个包：

```
apps/firewall-policy/
├── app.yaml                      # 清单（唯一的注册来源）
├── backend/
│   ├── api.py                    # router = APIRouter()，不写前缀
│   ├── service.py                # 业务入口：页面和 Agent 都只调这里
│   ├── models.py                 # 只映射本包 schema
│   ├── tasks.py                  # Celery 任务
│   ├── tools.py                  # Agent 工具（薄适配，调用 service）
│   └── contract.py               # 暴露给其他包的稳定接口（可选）
├── migrations/                   # 本包独立的 Alembic 分支
├── frontend/
│   ├── index.ts                  # export default defineApp({...})
│   └── pages/ components/ api.ts
├── skills/firewall-policy/SKILL.md   # 给模型的使用说明（可选）
└── tests/
```

### 2.2 清单示例

```yaml
id: firewall-policy
name: 防火墙策略生成
version: 1.0.0
icon: shield
db_schema: firewall
requires:
  - rule-governance: ">=1.0"      # 如需读取设备/地址归属
backend:
  router: apps.firewall_policy.backend.api:router     # 挂到 /api/apps/firewall-policy/
  tasks: apps.firewall_policy.backend.tasks
  tools: apps.firewall_policy.backend.tools:TOOLS
migrations: migrations/
frontend: frontend/index.ts
agent:
  skills: skills/
  default_exposed: true
```

### 2.3 自注册如何实现

| 层 | 机制 | 新包接入时 |
|---|---|---|
| 后端 API | 启动时扫描 `apps/*/app.yaml`，与 `platform.installed_apps` 表中已启用的包取交集，统一 `include_router(prefix=/api/apps/{id})`，并挂上“应用授权”依赖 | 无需改 main.py |
| Celery | 按清单 `autodiscover_tasks`，队列名取 `app.{id}` | 无需改 celery_app |
| 数据库 | Alembic 多分支：`version_locations` 由清单生成，每个包一个 branch label | `netops apps enable X` 执行本包迁移 |
| BFF | 用一个通用代理 `/api/apps/<app_id>/<path>` 替换逐接口代理视图，负责认证、校验应用授权并转发身份 | 无需改 urls.py |
| 前端 | Vite `import.meta.glob('/apps/*/frontend/index.ts')` 构建期收集；外壳调用 `GET /api/platform/apps` 获取“已启用且当前用户有权使用”的包，两者取交集后生成桌面图标和路由 | 需要重新构建前端（说明清楚，不做运行时微前端） |
| Agent | 工具注册表按清单加载 `tools`；每轮只把当前用户有权使用的包的工具交给模型 | 无需改 Agent |

新包接入流程：复制目录 → `netops apps enable firewall-policy`（校验依赖、建 schema 和角色、跑迁移、写 installed_apps）→ 重启后端 → 重建前端。

### 2.4 包之间的依赖

- 只允许依赖对方的 `contract.py`（或 `packages/contracts`），禁止 import 对方 `backend/*` 内部或直接读对方的表。用 import-linter 在 CI 里强制。
- 巡检 → 规则治理：通过 `get_runtime_bundle(check_item_ids, device_scope)` 获取冻结的 TextFSM、适配器、事件规则运行包。这正是现有“冻结运行包”模型，只需要把边界固定下来。

### 2.5 对“设备管理放进规则治理”的意见

可以放进去，但建议作为规则治理包里一个**单独子模块 `assets`**，并在 contract 中提供只读的设备查询接口。原因：巡检、防火墙（地址归属）、诊断 Agent 都要读设备；如果它们依赖规则治理的内部实现，以后就拆不开。

## 3. 权限与数据库

### 3.1 应用级授权（只控制“能不能用”）

```
platform.app_grants(app_id, principal_type: user|group, principal_id, granted_by, granted_at)
```

- 管理员默认拥有全部应用；普通用户经“用户或组”获得应用授权。
- 由三处统一执行：网关挂载依赖（`/api/apps/{id}/**`）、BFF 通用代理、Agent 工具过滤。
- 现有 `netops.configure` 并入“规则治理”应用授权；admin/operator/viewer 只保留“平台管理员 / 普通用户”两级。
- 需要说明：应用内部不再区分角色，但**数据归属不是权限**。例如公文 `owner_id`、个人对话、个人布局仍然按本人过滤，否则 A 能看到 B 的草稿。巡检、规则等团队共享数据则不按用户过滤。

### 3.2 参考 Linux 的数据库设计

| Linux | PostgreSQL 对应 |
|---|---|
| 用户 `firewall` + 家目录 `/home/firewall`（700） | 角色 `app_firewall` 拥有 schema `firewall`，其他角色无 USAGE |
| 组，`chmod 750` 给组读 | 被依赖的包创建 `contract_*` 视图，`GRANT SELECT` 给依赖方角色 |
| root | 迁移角色 `netops_migrator`（只在 enable/升级时使用） |
| `/usr/lib` 公共库 | schema `platform`（用户、组、授权、文件索引、任务状态），各包只读 |

- 运行时每个包使用自己的 SQLAlchemy engine（独立 DSN，或同一连接池上 `SET ROLE app_x`），越界访问会被数据库直接拒绝，而不只是代码约定。
- **终端用户不映射成 PG 角色，也不上 RLS**：权限只到应用级，数据库只负责“包与包之间”的隔离。
- 现有 0001~0101 的表都在 public。第一阶段新建 schema 和角色，按包 `ALTER TABLE ... SET SCHEMA`（元数据操作，不搬数据），同时加 `search_path` 兼容；按包逐个迁移，不一次性全改。

## 4. 开放式问答 / 诊断

### 4.1 为什么上一轮方向错了

上一轮做成了一个完整的 RAG 平台：知识空间、授权纪元、生成切换、查询租约、发布审核、所有权转移、客户通道……共 16 个 Alembic 迁移、9 个 Django 迁移，还往认证表里加了触发器。复杂度都花在知识库治理上，没有花在“回答网络问题”上。

### 4.2 建议：Agent 带工具检索，不做重型 RAG

参考编码 Agent 读代码库的方式：**先搜索，再读原文，然后查实时数据，最后引用证据回答。**

资料侧（assistant 包内，schema `assistant`）：
- 导入：文件存 MinIO，一次性转成带标题层级的 Markdown（docx/pdf/xlsx 可用 markitdown 或 docling），按章节切成 `doc_sections(doc_id, path, heading, text, tsv)`。
- 检索：PostgreSQL 全文检索 + `pg_trgm`（中文可加 zhparser，或入库前用 jieba 分词）。先不上 Qdrant 和 reranker；评测证明召回不够时，再加 pgvector。

工具（全部只读，由服务端带入用户身份）：

| 工具 | 来源包 | 说明 |
|---|---|---|
| `search_docs(query, filters)` | assistant | 返回章节 ID、标题路径、摘要片段 |
| `read_section(section_id, range?)` | assistant | 读取原文章节，可翻页 |
| `find_devices(query)` | rule-governance.assets | 按名称、IP、角色、站点查找设备 |
| `get_check_results(device, check_item?, since?)` | inspection | 最新检查结论与事实 |
| `list_changes(device?, since)` / `get_event(id)` | inspection | 变化与事件 |
| `get_evidence(evidence_id)` | inspection | 原始 CLI 证据片段 |
| `request_targeted_collection(device, commands)` | inspection | **写操作**：只创建待确认请求，由用户在界面确认后才执行 |

诊断流程写进 system prompt 和 `assistant/SKILL.md`：明确现象 → 定位设备 → 取最近的检查/变化/事件 → 查 SOP/手册 → 形成假设 → 标注证据 ID 与章节 ID → 给出建议，不直接执行变更。

评测：先收集 30~50 个真实问题，每题标注应引用的章节或数据。每次改动都跑这组题，再决定是否需要向量、重排或更复杂的方案。

## 5. DeepSeek Harness（dsh）评估

### 5.1 先澄清

你本机的 `~/deepseek-harness` 是 tylerbuilds/deepseek-harness（“MorpheOS Code”，批处理/语料 TUI），**不是** deepseek-ai 官方的 DSH。下面评估的是官方 DSH（`@deepseek-ai/dsh`、`deepseek-harness-sdk`）。

### 5.2 事实（依据公开文档，2026-09）

- 状态：developer preview，版本 0.1.5-rc.1，README 明确“会有不兼容变更”，且未经过安全审计。
- Python SDK 为每个 harness 实例拉起一个 `dsh` 子进程，通过 stdio JSON-RPC 通信；接口是 `run()`，以及低层 `prompt/subscribe`。
- 已知限制：没有协议版本协商；**没有 cancel**（只能杀进程）；**服务端不会向客户端发请求**，因此无法在工具执行中途回调到我们的 UI 做审批；`sdk-minimal` 的 shell 是 danger-full-access。
- 扩展方式：TS 插件（Cordis），或 MCP client 挂载外部 MCP 服务器；有 skills、hooks、subagent、Web UI。

### 5.3 结论

| 目标 | 可行性 |
|---|---|
| 替换 LangGraph 做“开放式问答/诊断”的执行器 | **可以试验**，但要放在自有接口后面 |
| 用 dsh 调度整个系统（巡检、规则、长任务） | **不建议**。固定流程和长任务要有持久状态、重试、审计，应留在各包的 service + Celery 中 |
| 直接用 dsh Web 替换 NetOps 前端 | 不建议。它面向本地单用户，与我们的登录、应用授权、桌面不相容 |
| 利用 dsh 生态 | 收益有限：生态以编码、PPT、研究为主，NetOps 的价值在我们自己的工具上 |

### 5.4 建议做法

```
platform/agent_runtime/
├── base.py        # AgentRuntime 接口：run(session, messages, tools, identity) -> 事件流
├── native.py      # 默认实现：DeepSeek function calling 循环（OpenAI 兼容），进程内直接调用 Python service
└── dsh.py         # 试验实现：DSH SDK + NetOps MCP 服务器
```

- 移除 LangGraph 的收益明确：现在的 Supervisor 图、skill_system 路由、ExecutionPlan 和工作流 Chat Intent，都可以由“模型 + 工具列表”替代。
- 默认用 `native`：身份、取消、流式事件、审计都在我们进程里，工具就是 Python 函数，代码量不大。
- `dsh` 试验的安全要求：
  1. 自定义 profile，**去掉 shell 和文件工具**，只挂 NetOps MCP 工具；
  2. 每次运行签发短期、限定应用范围的令牌，通过环境变量交给 MCP 服务器。**用户身份绝不能由模型填写**；
  3. 写操作一律返回“待确认”，由 NetOps 界面确认，弥补没有审批回调的问题；
  4. 设置子进程池和超时，并行度按实例数控制。
- 试验期 1~2 周，用 §4 的评测题集对比 native 与 dsh：回答质量、延迟、成本、失败可控性。数据决定去留。

## 6. 分阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 回退知识库改动（代码 + 数据库），main 测试恢复通过 | 现有 e2e 通过 |
| P1 | 平台内核：清单加载、`installed_apps`、`app_grants`、通用 BFF 代理、前端 glob 注册、`/api/platform/apps` | 旧应用照常运行 |
| P2 | 试点：公文写作迁成包（边界最清楚），验证 schema 与角色、迁移分支、前后端自注册 | 复制包、启用、重建即可使用 |
| P3 | 防火墙策略包：`/runs` 接口、任务持久化、受控上传，去掉对 test-run 的依赖 | 页面和工具走同一 service |
| P4 | 规则治理包（含 assets 子模块）及 contract | 巡检只经 contract 获取运行包 |
| P5 | 巡检包（最大），拆分 DeviceOperationsWorkspace | 旧 URL 兼容跳转 |
| P6 | assistant 包 + `agent_runtime.native` + 各包 tools；同时进行 dsh 试验 | 评测题集基线 |
| P7 | 下线 LangGraph Supervisor、skill_system、Workflow 市场和插件发布相关代码 | 删除依赖 |

## 7. 需要你确认的问题

1. 回退：是否确认同时清理数据库（执行清理 SQL）？
2. 设备资产：放在规则治理包内的 `assets` 子模块（带只读 contract），还是单独做一个包？
3. Agent 执行器：默认 native、dsh 作为试验，还是直接只做 dsh？
4. P1 之后先试点公文包，还是先做防火墙包？
5. Django BFF：保留并改成通用代理，还是评估合并进 FastAPI（减少一层）？
