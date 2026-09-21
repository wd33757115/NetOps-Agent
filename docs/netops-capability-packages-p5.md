<!-- document-status: archived -->
> 历史版本交付/设计记录；当时的部署状态、待办和测试数量不代表当前版本。当前状态见[实施状态](netops-architecture-implementation-plan.md)。

# P5 巡检完整业务包

当前状态：**P5 已完成阶段实施与交付验收；正常本地已安装，尚未启用常驻服务。**
在线/离线巡检、历史比较和当前证据重放均使用固定 Python 流程与 Celery；前端、API、业务实现、
任务和资源已归入 inspection 包，统一 App 准入。最终交付见文末 P5-C2/D。
以下 A、B、C1 中的“尚未完成”是各批次当时的边界，不代表当前状态。

## P5-A：离线巡检固定执行（2026-09-20）

本批完成单周期离线巡检、双周期历史比较的执行切换，P5 整体尚未完成。
继续遵循设计第 7.1 节：业务包中的固定 Python 调用配合 Celery，不增加 Workflow 适配器、
通用步骤引擎、任务表或新队列。

### 已实施

- `src/apps/inspection/backend/offline_tasks.py` 实现质量门禁、周期导入、状态归一化、
  变化检测、事件构建、报告生成与保存。历史比较先处理 baseline，再处理 current；
  两种离线任务均不更新在线当前状态，历史比较保留允许部分归一化的现有规则。
- 复用 `OfflinePatrolJobRecord` / `OfflinePatrolPeriodRecord` 的任务 ID、请求幂等键、
  attempt、状态、进度、结果和产物。新执行不创建 WorkflowRun；报告复用现有渲染器和 MinIO 下载机制。
- 重复请求返回已有任务，Celery 投递使用持久 task ID。重复/过期消息不能重复领取；
  Broker 投递失败保留 queued，Beat 每 60 秒补投。运行中任务不自动重做：记录超过 35 分钟未更新，
  且原 Worker 已释放执行锁时，标记中断并保留结果，供用户检查后重试。
- 每个任务使用 PostgreSQL 事务咨询锁，Worker 执行期间阻止重新提交和修改源文件/绑定等配置。
  取消立即记录 cancelled，在当前阶段返回后停止后续阶段；取消不会终止正在执行的底层 I/O。
  原 Worker 退出前重试返回冲突。阶段及导入结果写回核验任务 ID、请求键和状态，不能覆盖已取消任务。
- 旧普通离线预览消息检查当前 task ID，预览持有同一任务锁；过期预览不能覆盖后续执行。
- 普通离线导入拒绝没有固定任务执行凭据的 job 入口。规则样本仍由规则治理任务处理，
  不会被巡检执行器领取；样本投递也记录实际 task ID，供取消使用。
- `patrol-offline-current` 和 `patrol-history-change-event` 从可执行 Workflow 列表移除，
  引擎拒绝新启动，旧步骤消息返回明确的退役错误。模板和历史记录保留读取及取消用途。
- 离线工作台展示任务编号、当前阶段、已完成步骤和失败原因；只有历史 Workflow 记录才使用旧监视器。
- 修复 P4 灰度采集的 `launch_mode="task"`：真实 StartPatrolHandler 仅接受 workflow/direct，
  现改为 direct。测试经过真实 StartPatrolHandler，防止仅模拟适配器掩盖启动错误。

### 验证

- 后端定向 61 项通过；新增固定执行检查 14 项包含完整步骤顺序、单/双周期参数、
  报告保存失败、阶段失败、重复/过期投递、取消与导入写回、中断恢复、所有权、退役入口。
  其中事务锁使用真实独立 PostgreSQL 连接验证，未写入正常业务数据。
- 兼容回归 75 项通过：原 Workflow 管理、历史模板、巡检处理器、批次、聊天上下文及来源资格。
- 前端全部 200 项通过；最终调整后新增进度组件 3 项再次通过。生产构建、涉及前端文件 ESLint、
  新 Python 模块完整 Ruff、涉及旧模块关键错误检查、编译和 diff 空白检查通过。
- Celery 默认模块加载确认 execute_offline / recover_offline / preview 三个任务注册及恢复调度存在。
- 证据日志：`.tmp/p5-offline-backend.log`、`p5-offline-compat.log`、`p5-offline-final.log`、
  `p5-offline-frontend.log`、`p5-offline-frontend-final.log`、`p5-offline-build.log`、
  `p5-offline-lint.log`、`p5-offline-worker.log`。

执行链测试隔离了采集/归一化等既有服务及对象存储；本批没有启动实际 Broker/Worker/Beat 交付环境，
也没有连接设备。完整 App 的真实部署验收留在 P5 收尾，不能将本批测试视为 P5 全量验收。

### 后续边界与交付

- 在线巡检仍使用原 Workflow 完成采集后的状态、变化、事件与检查项结论；direct 当前仅负责采集。
  下一批需要接通完整固定业务流程，再切换在线入口与当前证据重放，不能只修改 launch_mode。
- 巡检后端领域实现、报告/事件/定时任务、前端和 API 的物理归包，以及 App 清单、自注册、
  统一 App 准入、Worker 再授权和整体停用排空，尚待实施。
- 本批 `inspection` 为实现目录，尚无可安装清单。离线任务暂由宿主既有 Celery 加载路径和
  `netops.patrol` 队列运行；收尾时归入清单。共享 `offline_jobs` / 任务锁暂在 core，
  P5 内完成与规则样本证据服务的边界整理，避免两个 App 互相依赖安装和授权。
- 未修改正常库 schema、启用 App 或启动正常常驻服务。规则治理包代码已修改，原已安装版本摘要
  不再代表当前源码；正式部署前须重建并按既有安装/升级流程更新注册摘要，不能直接启用旧记录。
- 部署此切换时先停止旧版本 Worker 的新任务准入并排空/取消旧离线 Workflow，再统一重启
  Gateway、Worker、Beat 和前端。旧版本正在执行的代码不具备本批写回保护，不能混用验收。

## P5-B：固定在线执行与批次状态基础（2026-09-20）

### 已实施

- 新增包内 `online_tasks.py` / `online_execution.py`：明确顺序执行采集、状态归一化、
  变化检测、事件和检查项结论，然后申请最终报告。没有通用节点解释器或 Workflow 步骤适配。
- 复用现有 PatrolRun 的 owner、metadata 和冻结采集计划保存执行身份、阶段、结果及终态；
  不新增任务表。Worker 只接收批次 ID / 计划 ID，从服务端重新读取冻结计划，
  校验任务 ID、设备、运行包及摘要，拒绝参数注入和失效计划。
- 内部 direct 生产派发已连接新任务；parser_validation 灰度采集继续使用原采集任务。
  新执行器仅支持冻结的在线生产检查项批次，不能拿来运行未冻结采集、规则样本或离线当前证据重放。
- 采集处理器支持延后任务完成标记，默认兼容旧调用。新链路采集后仍处于 running，
  事件及检查项结论成功后才写 completed；嵌套检查项结论失败不能被事件生成成功掩盖。
- 每个 task 使用 PostgreSQL 事务咨询锁防止同时执行。重复投递不重复采集；
  阶段写入及完成校验 owner。丢失执行所有权的旧 Worker 不能写回完成或失败检查项结论。
- 取消后停止后续阶段，保留已采集证据，结算未完成检查项。当前阶段的底层 I/O 不强制中止。
  旧 Worker 退出、执行状态收敛前，报告门禁保持关闭，不能仅凭采集结束或取消请求生成最终报告。
- 每分钟检查已投递但超过 2 分钟没有领取记录的目标，以原 task ID 补投；Broker 不可用时保留原目标。
  已运行记录超过 65 分钟未更新，且 Worker 已释放锁时，保留证据并结算中断失败，
  不自动重连设备重做。失效冻结计划直接落为目标失败，避免无限补投。
- `online_reads.py` 从真实 PatrolRun 读取状态，不伪造 WorkflowRun。批次状态计数、
  服务端筛选、设备状态、取消判定和报告终态判定支持 direct 任务；旧 Workflow 历史继续可读。
  前端批次设备表与刷新逻辑优先使用新的 execution_status，兼容旧字段。
- 最终报告派发实现迁入包内 `reports.py`，旧 Workflow 类只作兼容转接。
  现有检查项矩阵、证据和报告门禁保留。

### 验证与当前边界

- 后端去重 163 项通过：两组回归分别为 64 项和 115 项，包含重复覆盖；
  新增固定在线执行检查最终 17 项通过。覆盖步骤顺序、真实采集仓储的 reserved → collection
  状态交接、取消、重复投递、所有权失效、嵌套结论失败、补投、计划失效、中断与批次状态。
- 实际 PostgreSQL 独立连接的任务锁检查、active/completed/failed/cancelled 批次筛选通过；
  默认 Celery 模块加载确认两个在线任务与 Beat 恢复调度注册。
- 前端 200 项通过；涉及前端文件 ESLint、生产构建、新 Python 文件 Ruff、
  涉及旧模块关键错误检查、编译和 diff 空白检查通过。
- 日志：`.tmp/p5-online-backend.log`、`p5-online-contracts.log`、`p5-online-worker.log`、
  `p5-online-frontend.log`、`p5-online-lint.log`、`p5-online-build.log`、`p5-online-diff-check.log`。

公开 API 仍禁止 production/direct，默认在线生产入口尚未切换；原在线 Workflow 暂未退役。
原因是现有巡检汇总、设备证据详情及重试/重跑入口还读取 Workflow 记录，需作为下一批一起接通。
本批不能表述为“现有在线生产巡检已完全脱离 Workflow”。当前证据重放、完整归包、自注册、
App 准入与生命周期仍待实施。

业务阶段在新执行链测试中使用隔离服务；另外回归了既有采集器、运行包和报告契约。
尚未做真实设备或真实 Broker/Worker/Beat 的 P5 在线端到端部署验收。
未切换正常生产入口、启用巡检 App、迁移正常库 schema 或启动正常常驻服务。


## P5-C1：在线巡检结果读取与证据交付（2026-09-20）

### 已实施

- `online_reads.py` 从批次目标和实际 PatrolRun 读取执行事实，区分固定在线生产批次、
  历史 Workflow 与旧采集任务；排队但尚未领取的任务只返回持久 task ID，不伪造采集记录。
- 批次汇总复用现有质量、变化、事件和当前状态统计逻辑，读取新任务实际采集 ID 与阶段结果。
  固定任务不查询 WorkflowStep，不创建 WorkflowRun 或替代步骤记录。业务聚合实现暂在原文件，
  后续完整归包时迁移；本批没有新增通用执行层、数据库表或队列。
- 四个固定阶段展示真实完成、运行、失败和取消计数。判定门禁失败优先于已返回的阶段结果，
  防止事件构建返回成功而检查项结论失败时误报完成。没有持久记录的阶段耗时保持为空。
- 汇总支持排队、采集后仍在分析、准入失败及全部预检失败；未生成执行记录的批次保持诊断空态。
- 设备证据时间线按实际批次目标关联 PatrolRun，严格按批次和设备筛选；新任务领取后即使
  尚无快照也可见。旧 Workflow 的精确筛选仍保留，不能混入其他批次或其他设备的证据。
- 证据 API 增加独立的 execution 状态、时间、阶段、错误和 task ID；固定任务不借用
  workflow_status 表示成功。前端优先展示完整执行状态，采集 completed 不再提前显示整次巡检完成。
- 执行台账 CSV、原始 JSONL、事件 CSV 和原始 CLI ZIP 接通实际巡检记录与采集归档；
  台账保留历史列顺序并在末尾追加执行类型、任务 ID 和阶段，固定任务不填写 Workflow 步骤索引。
  原始包保留冻结报告清单与证据核对路径，设备目录同时包含实际执行身份和状态。

### 验证与剩余边界

- 后端两组分别 125 项、81 项通过；新增读取检查最终 12 项通过，合计去重 194 项。
  覆盖实际 SQLite 持久记录、批次/设备隔离、无快照任务、失败门禁、原始归档读取、
  历史 Workflow 兼容，以及检查项报告、领导报告和下载契约。
- 前端 202 项通过；生产构建、涉及文件 ESLint、新 Python 模块及测试 Ruff、
  旧模块关键错误检查通过。真实 PostgreSQL 的新增读取查询执行成功，验证过程只读。
- 日志：`.tmp/p5-online-reads-backend.log`、`p5-online-reads-reports.log`、
  `p5-online-reads-final.log`、`p5-online-reads-frontend.log`、`p5-online-reads-build.log`、
  `p5-online-reads-lint.log`、`p5-online-reads-postgres.log`、`p5-online-reads-diff-check.log`。

本批完成结果读取与交付衔接，公开 production/direct 入口仍未切换，在线 Workflow 尚未退役。
下一批须用现有冻结计划和批次机制接通失败重试/整批重跑、就绪检查及启动入口，
保留旧尝试证据与取消后的执行隔离，然后统一切换生产入口。当前证据重放、完整归包、
App 授权与整体生命周期仍未完成，P5 不能标记完成。

本批未启用 App、修改正常库 schema、连接设备或启动正常常驻服务；没有进行真实
Broker/Worker/Beat 的 P5 端到端部署验收。已有规则治理安装摘要仍需在正式部署前重建升级。

## P5-C2/D：生产入口切换、完整归包与交付验收（2026-09-20）

P5 本轮完成。P6 的 AgentRuntime/DSH 试验和 P7 的全局旧依赖清理未提前开展。

### 最终业务路径

- 在线生产入口直接提交固定巡检，使用服务端用户、请求幂等键、冻结范围和已发布运行包。
  同一请求返回原批次，改变范围后复用键返回冲突；冻结中断保留失败，不执行半份计划。
- 失败重试和整批重跑创建新批次，重新预检并冻结资产，保留旧尝试和原始证据；
  原 Worker 尚未退出时禁止重试。批次归档、取消、恢复、结果筛选均读取实际执行状态。
- 当前证据核验通过后，固定任务按已保存证明和冻结计划重放，使用预留 PatrolRun 身份，
  不连接设备、不创建 Workflow。撤销证明会阻止后续领取和阶段继续，保留已有证据。
- 普通离线/历史比较沿用现有作业和阶段持久进度；在线沿用批次目标、冻结计划和 PatrolRun。
  取消和中断保留已完成结果；报告门禁等待真实终态。没有新任务表、通用节点引擎或队列。
- 四个巡检 Workflow 模板停止接受新运行及旧步骤消息；历史详情和取消兼容保留。
  规则灰度采集使用规则包自己的派发/恢复任务，不要求启用巡检 App。

### 完整业务包与自注册

| 归属 | 实现及入口 |
|---|---|
| inspection/backend | 启动、重试、在线/离线执行、批次查询与聚合、当前证明、报告、风险、原始证据、定时任务 |
| inspection/backend/patrol、check_items | 迁入报告与检查项报告实现，随包携带 DOCX、绑定和投影 YAML 资源 |
| inspection/frontend | 独立巡检工作区、设备选择、离线工作台、证据、风险、报告签发与反馈组件及 API 客户端 |
| inspection/app.yaml | API、前端入口、13 个 Celery 任务、8 项调度、Agent 工具及生命周期钩子 |
| platform/inventory、platform/evidence | 共享设备 API、离线文件/作业、导入和执行锁；按服务端 purpose 隔离规则样本与巡检 |
| rule_governance/frontend | 从混合设备工作区拆出的资产管理页面，继续由规则 App 授权 |

应用路径 `/apps/inspection`，API 前缀 `/api/apps/inspection`；旧 `/apps/patrol` 和旧巡检 API
保留兼容。宿主旧模块只转接已迁入的实现；采集器、解析/规则求值、资产与存储仍为共享基础能力。
规则运行包通过已建立的公开读取契约消费，巡检用户不需要规则编辑权限。清单扫描负责自注册，
新增包仍须安装、构建部署及启用，不引入热插拔。

### 权限与生命周期

- 只检查 App 使用权，移除巡检内部 viewer/operator/admin 的动作限制，包括报告模板导入、
  签发和反馈处理。保留已有个人作业/文件所有权及证明核验的业务约束，不增加内部权限体系。
- 新旧 API、Agent 工具、报告下载和 Worker 起跑均检查当前 App 授权。Worker 使用持久化的
  提交用户；撤权后待执行任务记录失败，不能开始采集。SSE 心跳重新验证授权。
- 保存报告通过受保护的作业下载路由交付；通用产物接口不再签发巡检报告的绕行下载地址。
- App 执行持有共享 PostgreSQL 咨询锁，停用排空使用独占锁；先关闭准入，再等待请求和 Worker
  释放，结算未完成批次/作业/报告并保留数据。规则样本和规则采集批次不会被巡检排空修改。
- 定时报告恢复只领取巡检批次；旧手工 Beat 项被清单任务去重，避免重复调度。

### 验证证据

- 后端主回归 197 项通过（`.tmp/p5-backend-final.log`），覆盖启动、幂等、重试、冻结计划、
  在线/离线任务、当前证明与重放、批次、就绪检查、报告下载及风险 API。
- 收尾定向 32 项通过（`.tmp/p5-final/backend-last.log`），补充报告恢复的跨 App 隔离、
  退役模板、任务调度、App 排空及真实 PostgreSQL 共享/独占锁；与主回归有交集，不相加。
- BFF 授权、生命周期、包交付和既有权限回归 20 项通过；前端 41 个文件共 202 项通过。
  生产构建、相关前端 ESLint、新模块 Ruff、关键 Python 错误检查、编译和 diff 空白检查通过。
- 新建独立 PostgreSQL 数据库，从空库执行原迁移链至 `0101_patrol_report_templates`，
  使用独立 Redis、真实 Django BFF、FastAPI、生产前端产物及 Celery Worker/Beat。
  部署验收 1 项通过（107.282 秒）：安装幂等、摘要探测、启用、viewer 用户组授权、
  新/旧/直连 API、真实固定任务与重复消息、Worker 重启后 Beat 补投、撤权拒绝及停用排空。
  全程验证没有创建 WorkflowRun。测试进程结束后停止 Worker/Beat 和 HTTP 服务。
- 真实任务采用缺少证据的确定性输入验证质量门禁失败持久化；执行顺序、在线及重放正向流程
  由业务回归隔离底层服务验证。没有连接真实设备、提交模型请求或宣称真实巡检成功。
  不宣称全仓所有测试通过；P4 文档记录的既有运行包等基线失败仍保留。

最终证据目录 `.tmp/p5-final/`：`frontend-tests.log`、`backend-last.log`、`bff-regression.log`、
`migrations.log`、`deployment-test.log`、`deployment/`、`build.log`、`python-check.log`、
`diff-check.log`、`install.log`、`install-verification.log`。额外报告与兼容回归详见本轮
`.tmp/p5-focused-final.log` 等日志；不把多次重跑累计成独立测试数量。

### 本地交付及部署边界

- 正常业务库备份 `.runtime/app-package-backups/before-p5-20260920.dump`，47,409,059 字节，
  已通过 pg_restore 目录读取校验。没有新增或修改正常业务 schema。
- 正常本地已安装 `inspection 1.0.0`，并更新 `rule-governance 1.0.0` 的安装摘要；
  均为 `installed`、`desired_enabled=false`，源码、安装记录和生产前端包摘要一致。
- 将原有 2 名 active operator 的巡检执行权保留为直接 AppGrant；未向 viewer 扩权，
  规则原有授权保持不变。今后在用户/用户组的应用使用权限中统一管理。
- 正常启用前，先排空旧版本 Worker，部署本版 Gateway、BFF、前端、Worker 和 Beat，
  按已有 `app_packages verify/enable` 流程检查所有实例摘要及旧队列消费者，再开放访问。
  本轮未启动正常常驻服务，不混用新旧 Worker；真实设备生产联调属于环境启用后的验收。
- 回退先停用并排空本版任务，再恢复成套代码/构建/安装摘要及必要备份；不得把固定任务记录
  改写成 Workflow 或单独回退旧 Worker。历史 schema 和证据均保留，不运行 downgrade。
