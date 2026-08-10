# 检查项平台实施与验收矩阵

> - 对应方案：`docs/check-item-centered-patrol-platform-remediation-2026-07-27.md`
> - 最后校验：2026-08-08
> - 状态口径：`done` 已有代码、迁移、真实调用路径和相匹配的测试证据；`in_progress` 已部分落地；
>   `pending` 尚未达到阶段验收；`blocked` 存在工程阻断；
>   `blocked_by_asset_readiness` 表示平台行为正确，但生产资产矩阵尚未就绪。
> - 更新规则：不得仅凭文档、接口占位或单元测试 Mock 把条目标记为 `done`。
> - 42 项分层验收证据：
>   `docs/implementation/check-item-platform-e2e-evidence-matrix-2026-07-30.md`

## 当前发布判断

| 判断对象 | 状态 | 依据 |
|---|---|---|
| 平台工程能力 | in_progress | 检查项计划、raw-first EvidenceQualification、终态 Assessment、覆盖决定、RiskCase、AssetGap、原子 RuntimeBundle、对象存储报告、异步物化、正式签发、规则资产工作台，以及“技术恢复 -> 整改完成 -> 独立验收 -> 正式关闭”的风险治理闭环已落地；迁移 `0079` 新增离线 CurrentAuthority 请求账本、独立候选批次、来源/失败设备完整性台账、精确 Contract 冻结引用、同链重放和五阶段 UI。合成新鲜 CurrentAuthority 已通过真实 BFF/FastAPI/PostgreSQL/Celery/Redis/MinIO 与五类报告纵切，并验证历史、同人、幂等冲突、正文篡改拒绝、prepare/attest 并发 exactly-once、Worker 重投递无重复事实、一成功一失败的范围受限报告、Inventory UUID 证据对账及全目标未知分母；真实浏览器又完成上传、Inventory 绑定、独立双账号、空计划 fail closed、批准后 Beat stale、整页刷新保持、延迟调度和恢复续跑；冻结 RuntimeBundle 的 Active 切换隔离及 Manifest/版本/摘要篡改前置拒绝已有回归。迁移 `0080-0082` 又补齐候选精确样本证据、Job 样本计数和 current/retired attempt 生命周期，审批不再扫描可变 Dataset。S6850 四样本已经完成 ParserBundle 生成、逐轮持久化、阶段心跳、独立初审、v2 审批包、sanitized 规则链 dry-run 和事务 RuntimeBundle 回放；46 条真实变化命中 4 条事件、0 collision，生产资产零写入 | W0.4b 与复杂 ParserBundle 工程门禁已达成；下一关键路径是人工 Parser/规则/RuntimeBundle 审批、真实设备 `live_capture`、现场 current、baseline route、甲方内容验收和两个生产周期 |
| 当前生产资产 | blocked_by_asset_readiness | 2026-08-08 产品目标审计确认 Active CheckItemRuntimeBundle 为 0；Parser 为 active 3、pending approval 5、rejected 18。待审批项为 H3C MSR5620 `display cpu`、`display interface`、`display interface brief`、`display transceiver diagnosis interface`，以及 H3C S6850 `display transceiver diagnosis interface`。Active Contract 3、draft Contract 6；Active ChangePolicy/EventRule 各 11，但 AssessmentPolicy 与 TextComparisonProfile 均为 0，不能把旧规则数量当作检查项运行包就绪 | 工程师按审批包复核五个 Parser 的忠实解析范围；不得把窄范围或 absent-only 结果扩张为完整光功率能力。Parser 独立审批后继续生成、复核和审批 Contract、CurrentStateRule、Policy、Event、TextComparisonProfile 与 RuntimeBundle |
| 当前在线采集环境 | blocked_by_environment | 当前仅有 `192.0.2.40` 文档地址的工程夹具 Inventory 记录，没有已确认的真实生产设备、凭据或站点范围 | 导入并人工确认预发布真实设备与凭据后，才执行只读 `live_capture`、断连和站点限流演练 |
| 缺资产批次的诚实交付 | done | 2000 台 × 8 项形成 16000 个 `unable_to_determine`、0 个 `healthy`，并可生成 final ReportManifest 和五类交付物 |
| 单批次正式交付 | engineering_done / real_human_content_pending | 迁移 `0084` 已应用；`ReportContentReviewService`、API/UI 与 `ReportRelease` 强制门禁已通过真实 BFF/FastAPI/Celery/Redis/PostgreSQL/MinIO v1/v2 纵切。机器全量对账、确定性复核清单、五类对象字节级 SHA-256 重验、内容接受、独立签发、旧版 `released + superseded` 冻结及清理均通过；对象删除、摘要漂移和验收后篡改均以 409 fail closed。1000 条清单完成服务端分页、第 20 页读取、幂等重放、重新登录恢复和真实浏览器刷新验收。测试账号只证明机制；真实甲方内容验收和独立签发仍未完成 |
| 最终产品目标 | in_progress | 8.35 的当前数据库审计结果为 `7 blocked + 1 external_evidence_required`，`product_goal_achieved=false`。真实五条黄金资产审批、甲方生产矩阵、新鲜 current、两个生产周期、正式内容验收、多厂商规模证据和治理退出门禁均未被历史样本或测试账号替代；机器阻断清零后仍须由不可变外部证据逐项确认责任主体。任何单个工程纵切、报告或容量结果均不得替代 |

W0.4b 最新校正（2026-08-01）：真实浏览器已经完成当前证据文件上传、Inventory 唯一绑定、独立双账号
复核和 Celery Beat 自动处理。Active RuntimeBundle 缺失时，后端与前端以
`authority_production_plan_incomplete` fail closed，保存设备级预检失败和规则资产修复入口，
`launched_count=0` 且没有 Workflow。8.1 又在同一浏览器链路验证批准后停止 Beat、整页刷新、恢复任务延迟
调度与 Beat 恢复自动续跑：基础设施阻断期间请求保持 `approved`、批次保持
`awaiting_current_plan`，没有设备业务失败或 Workflow；恢复后才暴露真实资产缺口。solo Worker 执行恢复
任务时只信任当前任务实际 delivery queue，避免远程自检等待自身造成假阴性，API 侧仍执行完整检查。
计划只读取冻结 Bundle version ID；冻结后 Active 切换不改变本批次，Manifest/版本/摘要缺失或篡改在
Parser 前返回 `runtime_manifest_invalid`。CurrentAuthority、重放、恢复任务、生产准入与心跳组合
`31 passed`，Runtime Manifest 定向 `6 passed`，前端 `21 passed`，Vite `3387 modules` 通过；W0.4b 标记为
`engineering_verified`，但这些证据均不构成真实网络现状或生产资产审批。

Parser 候选证据校正（2026-08-02）：审计发现原四个未发布候选没有数据库级候选到样本关联，离线来源
重试后审批页可能读取不同的 Job 当前样本。迁移 `0080` 将旧未发布候选安全拒绝，`0081` 修复并由数据库
维护 `sample_count`，`0082` 把旧 attempt 样本标记为 `retired`。Active Parser 未改变。当前 H3C MSR5620
`display cpu`、`display interface brief` 和 absent-only 的 `display transceiver diagnosis interface`
均已形成证据协议 v1 候选，但仍为 `pending_approval`；后者不具备光功率 dBm 能力。S6850 两设备、
两周期、四样本随后完成 ParserSpec R6、五 record-set ParserBundle、逐轮落库、超时恢复和独立 Reviewer，
形成候选 `4fe4184d-e9c7-4a72-95c6-4ee7ae08def6` 与 v2 审批包。审批包冻结 4 份样本、508 条记录、模板和
样本摘要；候选仍为 `pending_approval`，未自动发布。

Parser 能力声明校正（2026-08-02）：新增 `parser-check-item-capability/v1` 纯领域投影，并接入审批包、
Parser comparison API 和人工审批抽屉。投影把 Parser Evidence、Canonical Mapping 和 Runtime Assembly
拆成三张证明单：ParserSpec 字段角色只用于判断输出形态，`canonical_candidate` 明确标记为未复核提示；
只有 ObservationContract 显式 observation type 才能证明标准映射，只有 Active RuntimeBundle 才能声明
生产资产就绪。当前统一审批包冻结 4 个候选、7 份样本、534 条记录，Runtime parity 与激活治理预检均
通过；MSR absent-only 候选被准确标记为缺少 metric/threshold 角色和 RX/TX 标准观测。该标记不会误杀其窄范围
Parser 审批，但会阻断完整光功率 RuntimeBundle。前后 Active RuntimeBundle 仍为 0。

Parser 字段契约校正（2026-08-02）：新增 `parser-field-contract/v1`，comparison API、人工审批抽屉、
v2 审批包和 RuntimeBundle 装配统一以实际 TextFSM/ParserBundle 头字段对账冻结 ParserSpec。装配侧同时
检查 ObservationContract 的源字段、transform、identity dimension、跨 record-set join 与阈值引用，
消除“Contract 声明了运行时根本不存在的字段”这一生产漏洞。数据库只读审计的 `3 active + 4 pending`
共 7 个 Parser 全部 `aligned`；其中真实四候选冻结 `7` 份不可变样本、`534` 条记录。S6850 五
record-set Parser 与 sanitized 光功率 Contract 的跨表引用对账为
`0 blocker`。实现明确不把该客观完整性校验扩大为本地业务语义门禁：LLM 初审与工程师直审继续决定
最终字段和解析结果是否满足运维需求。Active RuntimeBundle 保持 `0`，没有自动投产。
字段契约与 Contract 源引用阻断已分别绑定 `repair_parser_asset` 和
`repair_observation_contract` 唯一修复动作；负向回归证明页面不会把 Parser 漂移错误导向规则修复，
也不会让 Contract 悬空引用回到模板生成。修订只形成新候选，后序 RuntimeBundle 必须重新装配。

8.18/8.20 产品闭环补强（2026-08-02）：主方案进一步冻结当前结论的完整上下文、评估策略来源优先级、
Implementation 唯一选择、ComparisonContext 基线资格和取消/晚到回调语义。取消 API/BFF/UI、Workflow/step
终态锁定、后续分发阻断、子 Workflow 传播、Celery 协作式 revoke 和未完成目标结算已落地，并通过单元、
真实 PostgreSQL 竞态注入、BFF 和浏览器交互验证。该项目前为
`engineering_advanced / production_device_fault_matrix_pending`：Worker 硬终止/重投递、全目标 UTD 结算、
千台批量查询复杂度和五类制品对账已有真实依赖证据；真实设备断连、实际命令超时、串并行任务中断和
千台在线取消仍需在 Phase 4 预发布故障矩阵中证明。
在此之前不得把已有 Workflow 完成路径或报告能力标记为生产运行韧性完成。

8.19 风险口径与运营闭环补强（2026-08-02）：主方案新增 EventOccurrence、RiskOccurrence/RiskEpisode
和 ReportClaim 的分层身份与计数，领导“问题数”不得直接读取事件数；Assessment 必须冻结实测值、
期望状态、阈值、策略和证据组成的 AssessmentBasis。跨检查项根因关联、风险去重和生产反馈均只能通过
版本化策略或人工复核形成，LLM 不具有合并、降级、关闭或改写历史的权限。场景 38/39 已完成历史工程
集成，场景 41 已完成工程主链，场景 42 已完成受治理反馈工程闭环；场景 40 的真实故障矩阵、场景 41 的
网络 SME 门禁和场景 42 的预发布反馈/回滚证据仍未关闭，不能用已有领域测试或组件 Mock 冒充完成。

8.23 判断依据资产化补强（2026-08-02）：`TargetPolicySnapshot`、固定来源优先级、有效期/摘要校验、
实体级 `AssessmentBasis` 及其向 CheckAssessment、ClaimLedger、API、UI 和五类制品的投影已完成工程
实现。独立 `AssessmentPolicyProfile/Version/Approval/Binding`、Repository/API 和基础“判断策略”控制面
随后完成，迁移 `0086/0087` 已在真实 PostgreSQL 应用。

8.24 产品目标反向校准（2026-08-02）：主方案把跨厂商统一收敛为“来源字段、canonical observation、
判断策略、报告投影”四层合同，明确 TextFSM Value 可以随输出兼容范围不同，统一报告列只能由
ObservationContract 与 ReportProjectionProfile 形成。适用性正式成为 AssessmentPolicy 角色，freshness
和 ComparisonProfile 保持独立合同；两条生产线只通过 Active RuntimeBundle/策略绑定与受治理缺口反馈
交接。最短实施顺序固定为判断策略产品化 -> 黄金资产人工审批 -> 新鲜 current，
局部工程证据不得跨级声明甲方产品目标达成。

8.25 判断策略工程闭环（2026-08-02）：迁移 `0088-0090` 已应用；ApplicabilityPolicy、合法空输出、
全目标影响预览、Inventory 选择器、巡检 Basis 只读下钻、历史来源草稿化、双读影子、目标级切换和
回滚已经实现。后端聚焦回归 `34` 项、真实 PostgreSQL 并发测试 `1` 项、前端工作区测试 `8` 项及 Vite
生产构建（`3389 modules transformed`）通过。场景 38 通过真实 BFF/FastAPI/PostgreSQL/Celery/
RabbitMQ/Redis/MinIO 纵切和五类制品对象回读，摘要为
`.tmp/assessment-policy-scenario38-full-stack-e2e/summary.json`。该证据来自历史 `lixian`，且明确
`production_network_claim=false`；它关闭 `I` 层工程门禁，不关闭浏览器控制面、新鲜生产 current、
真实人工审批、甲方内容验收或独立签发门禁。

8.26 Parser 生成回路与可回退工作面（2026-08-06）：生产生成链已改为客观执行取证后交给独立
LLM Reviewer 语义复核；Reviewer 不通过时，当前模板、全部冻结样本的逐样本实际解析结果和结构化
诊断会回送 Writer，最多三轮。自动推测的记录数、字段价值和边界不再作为本地语义否决条件；三轮后
存在可执行候选时保留 AI 异议进入人工直审，不自动激活。Parser 控制面新增五阶段可点击导航、
“返回上一步/下一步/回到当前进度”和持久化 attempt 详情，可查看每轮 TextFSM、执行事实、解析表及
Reviewer 结论；导航不倒退任务状态，修订继续创建新 revision/attempt。后端聚焦回归 `39 passed`，
前端组件 `11 passed`，Vite 生产构建通过。真实浏览器以 `system.cpu_usage / display cpu / H3C
MSR5620` 待审批候选完成复验：五个阶段均可直接进入，能逐步返回并回到当前进度，刷新后仍可查看
3 条持久化候选记录、每轮 TextFSM、客观执行证据、3 条实际解析记录和 Reviewer 结论；390 px
移动视口无横向溢出，桌面视口控制台无 error/warning。该验证没有批准或激活候选。

8.27 规则资产六阶段工作面（2026-08-06）：ObservationContract、ChangePolicy 与 EventRule 生产详情
已统一为“来源与 Parser、安全上下文、候选生成、验证与回放、工程复核、发布审批”六个可点击阶段，
提供“返回上一步/下一步/回到当前进度”。各阶段直接读取持久化 Bundle Step、AgentAttempt、
EngineeringDraft 和 Replay 证据；历史导航只改变页面选择，不触发生成、复核或审批 mutation。后端规则
资产聚焦回归 `136 passed`，前端 Parser/规则资产七文件回归 `27 passed`，Vite 生产构建通过
（`3389 modules transformed`）。真实浏览器当前 CPU 生产单元因 Parser 仍待人工审批而准确停在
“尚未创建规则生成种子”，没有伪造后续六阶段数据；填充态六阶段已由组件与工作台回归覆盖，真实资产
浏览器纵切仍受有意保留的 Parser 人工发布门禁阻断，本轮未批准或激活任何生产资产。

8.28 生产比较基线接线（2026-08-06）：新增 `ProductionBaselineCandidateRepository`，Effective Plan 在
`production_current` 下按全部设备与 CheckItemVersion 批量读取历史事实，不再永远依赖空的调用方参数。
候选对生产用途、终态 Assessment、EvidenceQualification、唯一 Snapshot/PatrolRun、冻结
RuntimeManifest/RuntimeBundle/ComparisonContext、来源资格与摘要完整性 fail closed；结构化结果冻结
canonical/identity/ObservationContract 事实合同，文本结果冻结 Implementation/ComparisonProfile。
迁移 `0091` 增加查找索引。运行时文件 `49 passed`，其中真实服务纵切证明第二周期自动选中上一周期，
影子用途、资格摘要篡改、合同漂移和同时间双基线均拒绝；200 目标空历史查找只产生 1 条 SELECT。
场景 39 提升为 `engineering_unit_verified / real_dependency_matrix_pending`，不得据此声称已经完成真实
PostgreSQL、多厂商 Active 资产漂移、新鲜生产双周期或甲方变化结论验收。

8.29 场景 39 历史工程集成补证（2026-08-06）：
`scripts/test/check_item_comparison_context_full_stack_e2e.py` 已使用真实 PostgreSQL 和两类真实输出签名
完成事务纵切。输入为 `lixian/Q2/Q3` 的两台 H3C MSR5620 `display version` 与桌面 Q1/Q2 的两台
Cisco NEXUS9000 `show version`；覆盖首次观测、唯一最近基线、同时间双基线歧义、60 天过期、
Contract/RuntimeBundle 事实合同漂移以及零/多 CollectionImplementation。零/多实现均形成可对账的
`unsupported` 终态 Execution，不分发命令；无可比较基线关闭 Change/Event 权限但不伪造“没有变化”。
同时修复 UTC 与 `+08:00` 等价时刻摘要不一致，并完成 Change/Event 精确 lineage 的 PostgreSQL
round-trip。聚焦回归为 Parser `55 passed`、RuntimeManifest/Change/Event `52 passed`、Assessment/
Reporting `88 passed`。摘要：`.tmp/scenario39-comparison-context-e2e/summary.json`，数据库前后不变。
场景 39 提升为 `integration_verified_historical_evidence / production_current_pending`；该夹具明确
`production_network_claim=false`，不代表真实 Active 资产审批、新鲜生产双周期、正式制品或甲方验收。

8.30 检查项目录产品化（2026-08-08）：规则资产控制面新增“检查项目录”，工程师可在 UI 管理
CheckItem 到厂商、设备族、平台、型号/软件版本正则、命令优先级、fallback 与 Parser 模式的关系。
修改形成不可变 CollectionImplementation 修订，先按活动 Inventory 预览影响，再提交并由独立管理员
审批；歧义、证据漂移和 Active RuntimeBundle 引用均 fail closed。YAML 仅保留 bootstrap seed 职责，
不得覆盖 UI 发布版本。迁移 `0092`、目录治理领域/API 测试、工作台组件测试与生产构建通过；真实
Inventory 的浏览器修订、影响预览和独立审批仍是预发布证据门禁。

8.31 生产反馈闭环（2026-08-08）：迁移 `0093` 新增不可变 ProductionFeedback 和治理待办。反馈精确
绑定 Execution、Assessment、Event/Risk occurrence、证据摘要和 RuntimeBundle 版本；提交人与复核人
职责分离，确认后只能形成离线规则样本、非 Active AssessmentPolicy 修订或 AssetGap 数据质量待办，
不会创建 RuleGenerationJob、改写历史事实或自动发布规则。设备巡检页新增“质量反馈”工作面和结论
证据入口；Rollout evaluator 只读取候选版本、冻结目标和阶段窗口内的已确认反馈，按样本充分性计算
误报率并复用暂停/回滚策略。后端相关回归 `31 passed`、前端组件 `2 passed`、Vite 生产构建通过。
状态为 `engineering_done / production_feedback_pending / rollout_evidence_pending`，真实预发布反馈、候选
回放、阈值暂停、上一 Bundle 回滚和观察窗口仍未完成。

8.32 采集实现发布闭环（2026-08-08）：迁移 `0094` 冻结目录变更的 rollout、候选 Bundle 和原子切换
证据。待审批 CollectionImplementation 可装配草稿 RuntimeBundle；v3 灰度按 baseline/candidate 分别
冻结实现、命令与 Snapshot；最终审批在一个事务内同时切换实现和运行包。UI 只展示范围匹配的已完成
灰度活动，现网支持回退、范围不全、摘要漂移和职责冲突均 fail closed。领域测试 `4 passed`、Rollout/
Capture `22 passed`、目录 API 聚焦测试、前端工作台 `12 passed` 和生产构建通过。状态为
`engineering_done / real_inventory_browser_rollout_pending`，不得替代真实 Inventory、`live_capture`、
独立生产审批和现场观察窗口。

8.33 `lixian` v3 双轨复验（2026-08-08）：CPU、接口状态和 S6850 光功率已经使用独立 baseline/candidate
CollectionImplementation 与 RuntimeBundle 完成 6 目标、12 Snapshot、12 Projection 的事务影子回放。
统一 TextFSM 输入层删除非语义行尾填充后，黄金回放由 476 Observation/20 Change/0 Event 恢复为
632 Observation/48 Change/4 Event；双轨 Parser 执行成功率均为 100%。Campaign 仍因接口 expected-state、
覆盖决定和光功率适用性未批准而暂停，状态明确为 `execution_evidence_complete=true / rollout_eligible=false /
acceptance_status=fail_closed`。该结果关闭执行层缺陷，同时证明检查项判断策略必须由 UI 配置、影响预览和
独立审批补齐，不能由验收脚本硬编码为生产事实。证据位于
`.tmp/check-item-rollout-stage-lixian-e2e-20260808-v3-final/summary.json`。

8.34 废弃链路退出证据（2026-08-08）：迁移 `0095` 已应用，新增 payload-free、append-only 的
`LegacyPathObservationRecord`。Parser 旧调度、旧 Command Profile 激活、命令中心顶层列表、历史批次
结果消费和旧报告投影均通过统一观测器写 Prometheus、结构化日志和 PostgreSQL；本地真实 PostgreSQL
写入、窗口计数和清理已验证。`export_runtime_evidence.py` 可按候选导出两个发布周期的调用数；审计器
进一步要求七项 gate 分别绑定不可变 URI/SHA-256、两个不重叠 release cycle 且 `call_count=0`。
命令级证据详情与离线任务逐命令进度不属于待删路径。治理/观测及受影响回归共 `150 passed`，当前状态
为 `engineering_done / observation_window_pending / removal_gated`；观测功能上线不等于零流量已被证明。

8.35 最终产品目标可执行审计（2026-08-08）：新增
`src/core/check_items/product_goal_audit.py` 和
`scripts/test/check_item_product_goal_audit.py`，将 2.23.1 的八项 `ProductGoalAchieved` 门禁投影为
机器可读审计，但有意禁止数据库计数自动批准工程证据、甲方内容验收或正式签发。当前 PostgreSQL
事实为 20 个 Active CheckItem、38 个 Active CollectionImplementation、3 个 Active Parser、
5 个待审批 Parser、0 个 Active RuntimeBundle；Inventory 只有一台无凭据引用的 `192.0.2.40`
工程设备，`production_network_claim=true` 的 final Manifest、已接受内容复核、正式生产签发和灰度活动
均为 0；五个废弃候选尚无退出就绪项，且观察台账已有 1 次旧命令中心读取。审计结果为
`7 blocked + 1 external_evidence_required`，`product_goal_achieved=false`。证据位于
`.tmp/product-goal-audit-20260808/current-state.json`；纯判定单测 `3 passed`，Ruff 通过。该审计只拒绝
虚假完成声明，不代替真实资产审批、生产采集、甲方验收或所有者退出决定。审计随后补齐
`--external-evidence` 合同：只有机器阻断清零，且每项外部证据具备不可变 URI、SHA-256、生产责任人、
带时区复核时点及 `test_evidence=false` 时才可提升为 `passed`。

浏览器增量复验：判断策略路由、空状态、27 条历史来源盘点、来源盘点/影子对账/受控切换三阶段导航、
1280 桌面和 390 移动布局均正常，控制台无错误。由于隔离场景清理后当前库中策略资产为 0，本轮未把
空状态冒充全栈验收；已填充策略的影响预览、Inventory 绑定、切换和回滚数据路径继续保持待验。

8.11 产品目标收敛（2026-08-02）：主方案已冻结唯一产品真值图，structured、normalized diff 与
evidence 三类结果只在 Coverage/Execution/Assessment 层汇合；命令成功、Parser 通过、Workflow 终态、
零 Event、历史回放和文件生成均不再作为健康替代口径。`G8_REPORT_SCHEMA` 已纳入主方案唯一目标矩阵，
运行配置的规范化与变化语义明确由 `TextComparisonProfile` 冻结，不再错误要求结构化 ChangePolicy。
当前最短路径保持为“五条黄金候选证据 -> 独立人工发布 -> Runtime 装配 -> `lixian` 影子复验 -> 真实
current 与双周期 -> 内容验收/签发 -> 多厂商灰度和旧路径退出”，不新增第三条生产线。

8.11 工程证据回填（2026-08-02）：五条黄金纵切均已形成发布前历史/影子证据。CPU 四份真实样本形成
36 Observation、2 Change、0 真实 Event；接口状态形成 72 Observation、0 Change/0 Event；两者的事件
触发/恢复只以合成边界证明可达，且日流量估算明确标记 `sparse_history/unreliable`。S6850 光功率维持
524 Observation、46 Change、4 周期事件和 0 collision。设备版本 Q2/Q3 均保留完整
`7.1.064, Release 0821P18` 并形成历史信息事实；运行配置真实 SW1 周期对形成 41 行新增、4 行删除、
1 条 normalized Change 与 `configuration_change@1` 投影。所有专用回放均回滚，生产 Active
RuntimeBundle 仍为 0；该证据只关闭发布前工程疑问，不关闭人工审批、生产装配和当前网络门禁。

8.12 内容验收落地（2026-08-02）：正式报告交付不再从 `ReportManifest final` 直接进入签发。迁移
`0084` 新增不可变 `ReportContentReviewRecord/ItemRecord`；服务端先重验 v5 Manifest/source、
ClaimLedger、Target/Execution/Assessment、EvidenceQualification/CoverageDecision、readiness 与五类制品摘要，
再冻结“100% P0/P1、未知、排除、恢复、整改、关闭 + 每个检查项至少一条追溯项 + 普通事实每层最多
3 条”的确定性复核计划。复核决定不可覆盖，拒绝必须形成新的事实或投影版本；内容验收绑定精确
review plan 与 artifact SHA-256，`ReportRelease` 额外绑定 `content_review_id + acceptance_digest` 并要求
独立签发人。后端 19 项、前端 5 项和生产构建已通过；该落地时点只标记为
`content_acceptance_implemented`，随后完成的真实依赖纵切见 8.13；甲方人工验收仍由场景 37 保留。

8.13 内容验收补证（2026-08-02）：迁移 `0084` 已在当前 PostgreSQL 应用；真实 `lixian/Q2` 经
BFF、FastAPI、Celery/Redis、PostgreSQL 和 MinIO 完成 v1/v2 物化、内容接受、独立签发、五类制品
精确下载和范围化清理。全栈复验发现并修正两项语义：内容机器对账以冻结 `target_pairs` 为实际执行
分母，`ScopeDecision` 只负责范围治理；旧 v1 被标记 `superseded` 后仍保持原 `historical_result`、
`formal=true`、内容接受、签名与制品字节不变。

8.14 内容对象完整性与规模恢复性补证（2026-08-02）：内容验收和签发现在都会从 MinIO 流式重读
Markdown、DOCX、XLSX、JSON 与证据包实际字节，不能只信任 Manifest 元数据。真实全栈故障注入分别
删除 evidence 对象、替换 evidence 字节、在内容接受后替换 Markdown 字节，preview/start/release 均
返回可定位的 `object_missing` 或 `digest_mismatch`，写接口统一 409，恢复原字节后 v1/v2 正常完成。
另一个真实依赖夹具冻结 1000 条内容复核事实，BFF/API 第 1 页与第 20 页各返回 50 条且集合不重叠，
同一批决定重放不重复计数，关闭会话并重新登录后继续至 1000/1000。真实浏览器在 1280×720 下验证
首屏 50 条、第 20 页读取、无弹窗横向溢出及整页刷新恢复。该阶段升级为
`engineering_done / real_human_content_pending`；剩余门禁只有真实甲方对生产 current 报告的内容决定
和不同主体的正式签发，工程脚本不会替代该决定。

## 最终产品目标追踪

| 目标 ID | 当前工程证据 | 仍未完成的产品门禁 |
|---|---|---|
| `G1_SCOPE` | per-device TargetCheckMatrix、Profile -> Effective Plan 和 cursor 分页已验证 | 冻结首批甲方真实 Inventory 目标矩阵 |
| `G2_EXECUTION` | CheckExecution/Assessment 一对一、失败层和部分失败语义已有领域与交付栈证据 | 完成真实设备 Workflow 的首次及双周期批次 |
| `G3_CURRENT_RISK` | CurrentStateRule、Finding、RiskCase、时效/身份/覆盖和结论权限合同已验证；CurrentAuthority 已冻结离线来源、失败设备、范围摘要并另建生产候选批次；浏览器上传、Inventory 绑定、独立复核、空计划 fail closed、批准后平台故障投影、刷新保持、恢复续跑和冻结资产漂移均已验证 | 使用真实人工批准的 Active RuntimeBundle 形成新鲜 `production_current` 事实 |
| `G4_CHANGE_RECOVERY` | ComparisonContext、Change/Event、历史风险待复核、本批 EventOccurrence、服务端恢复候选重验和幂等确认已完成真实 BFF/FastAPI/PostgreSQL 纵切；覆盖不全时恢复失败关闭 | 使用 Active RuntimeBundle 和新鲜生产双周期事实验收 |
| `G5_REMEDIATION` | RiskRemediation 计划、执行、完成、独立验收、正式关闭、职责分离、幂等审计、UI 和报告投影已完成真实依赖纵切 | 由真实责任人、独立验收人、关闭人和签发人完成生产业务闭环 |
| `G6_TRACEABILITY` | 同一 Manifest 五类制品、MinIO 实际字节、原始输出与证据索引摘要已全栈验证；显式 ContentAccepted、冻结抽样、不可变决定、精确摘要绑定、对象缺失/漂移 fail closed、1000 条服务端分页与重登录恢复、BFF/API 双账号职责分离、v1/v2 独立签发和 superseded 旧版冻结均已通过真实依赖与浏览器纵切 | 由甲方真实主体对生产 current 报告完成内容验收和独立签发；自动化测试账号不得替代 |
| `G7_SCALE` | 20 个真实检查项、39 个实现对分页和 `2000 x 20` 容量 SLO 已验证 | 完成真实多厂商目标矩阵、灰度和两个生产周期 |
| `G8_REPORT_SCHEMA` | Canonical 长表、通用当前观测表、同源五类制品、ClaimLedger 和 `ReportProjectionProfile v1` 已实现；当前 20 个检查项均有精确 Profile，CPU/内存/接口/光功率/版本/配置等领域表只从冻结 canonical fact、Assessment 或 Change 派生 | 使用真实 Cisco/H3C 同检查项生产事实完成甲方 Excel 内容复核与签发；继续证明新增厂商只增加 Parser/Contract/Implementation，不修改报告分支代码 |
| `G9_RISK_TRUTH` | RiskCase、RiskOccurrence、EventOccurrence、恢复和整改生命周期已有基础；场景 38/39 已通过历史样本真实依赖工程纵切，场景 41 的跨检查项关联和场景 42 的不可变生产反馈工程链路已完成 | 完成场景 40 的真实故障矩阵、场景 41 的真实 SME 门禁和场景 42 的预发布反馈/回滚证据，并在 Active 资产的新鲜双周期 current 报告中复验，由甲方复核统计口径 |

## 总体状态

| 阶段 | 状态 | 当前证据 | 下一验收门 |
|---|---|---|---|
| Phase 0 基线与架构决策 | done | ADR-008；`lixian` v1 manifest；关键行为保护测试 | 继续保持语料摘要和方案版本同步 |
| Phase 1 检查项目录与采集实现 | done | 0058；目录、Resolver、per-device plan、命令去重和 63 项相关测试 | 在 Phase 4 真实批次中持续对账 |
| Phase 2 NTC 模板优先复用 | done / verified_single_record / verified_parser_bundle / blocked_by_human_gate | NTC Resolver 已冻结包版本、许可证、原始/派生模板与摘要、平台映射、逐行兼容转换和逐样本执行证据；真实 H3C `lixian` 单 record-set 样本已完成 BFF/MinIO/Celery/Workflow/PostgreSQL、DeepSeek 初审和生产 ParserRuntime 审批复放。S6850 四样本又完成五 record-set ParserBundle、逐轮持久化、阶段心跳、独立 AI Worker、全样本回放和 v2 审批包；ParserRuntime parity 通过 | 工程实现已关闭；真实候选仍须工程师语义复核及独立发布，该人工门禁计入生产资产就绪而非 Parser 生成缺陷 |
| Phase 3 标准语义、Assessment 与运行包 | in_progress / assessment_policy_integration_verified / scenario39_historical_integration_verified / browser_navigation_verified / populated_data_path_pending | 0059/0060/0062/0063/0064/0065/0066/0085/0086/0087/0088/0089/0090/0091；Finding、Assessment、Risk、Comparison、raw-first EvidenceQualification、CoverageDecision、RuntimeBundle；规则来源到独立审批、RuntimeBundle 激活与 Resolver 读取的隔离真实全栈已完成。AssessmentPolicy 的阈值、期望状态和适用性治理资产、全目标影响预览、Inventory 选择器、巡检 Basis 下钻、历史草稿迁移、双读影子、目标级切换/回滚及场景 38 已完成工程验证；场景 39 已用真实 PostgreSQL 与 H3C/Cisco 历史样本关闭工程 `I` 层 | 使用隔离已审批策略完成浏览器影响预览、Inventory 绑定、巡检 Basis、切换/回滚数据路径；随后由真实工程师审批 CPU/接口/光功率生产资产与 RuntimeBundle，并在新鲜 Active Bundle 双周期中复验场景 39 |
| Phase 4 运行时切换 | in_progress / cancellation_engineering_advanced | check-item preflight/freeze、per-binding fallback、raw Snapshot 先落库、完整采集后按 Execution 冻结资格、冻结 Parser 读取、实体覆盖、终态 Finalizer、集合对账、direct 生产入口禁用和 global/site/device 租约已实现；CurrentAuthority 同链重放会重验请求、资格、源文件、raw hash 和范围摘要，并按 RuntimeBundle 冻结精确 ObservationContract ID/摘要；生产 direct 已完成真实 BFF 409、数据库与 RabbitMQ 无副作用验收，真实 Redis/RabbitMQ/PostgreSQL fencing 演练通过。取消链已具备 API/BFF/UI、终态锁定、晚到成功/失败拒绝、完整 UTD 分母、五类制品、1000×2 常数级结算和真实 RabbitMQ/Celery Worker 硬终止重投递 | 完成预发布真实设备断连、实际命令超时、串并行任务中断和千台在线取消压测，报告必须继续保留取消/未知分母 |
| Phase 5 UI 与报告切换 | engineering_done / production_content_pending | 五类同源制品、MinIO 回读、异步报告请求账本、状态轮询、purpose-aware v5 Manifest、raw evidence set、ClaimLedger、中文检查项展示、安全 cursor 和 `2000 × 20` 容量已通过；`ReportProjectionProfile v1` 覆盖 20 个检查项。场景 41 已将领导问题数、RiskEpisode、影响设备、影响实体和 EventOccurrence 分列，五类制品消费同一投影；风险详情支持关联提议、独立批准/拒绝、撤销和过期提示，旧报告的接口/光功率自动猜关联已删除。显式内容验收与独立签发的真实依赖纵切保持通过 | 由网络 SME 复核真实跨检查项关联策略；随后执行新鲜 `production_current`，由甲方内容验收人与不同签发人决定，自动化测试不得替代 |
| Phase 6 文档和废弃代码清理 | in_progress / document_governance_done | 75 份 Markdown 已全量登记；20 份 superseded 文档均在首屏显示机器可校验的历史告警并链接唯一后继方案。CI 同时阻断未登记文档、断链、Active 索引引用历史文档和缺失/错误后继告警；严格审计四类违规均为 0。5 个废弃代码候选仍有 owner、替代方案、截止日期和退出门禁 | 读取真实流量、任务、写入、替代覆盖和回滚证据后才允许物理删除兼容代码；文档治理完成不得替代运行时退出证据 |
| Phase 7 灰度与收尾 | in_progress / engineering_feedback_done / engineering_dual_implementation_done / blocked_by_asset_readiness | Campaign/StageAttempt、六阶段状态机、冻结摘要、路由、门槛、暂停/恢复/回滚、StageExecution/Outbox/Worker/Evaluator、`capturing` 状态、baseline/candidate 独立命令与 Snapshot、受治理 legacy route、目录与 RuntimeBundle 原子切换、不可变生产反馈、独立复核、治理待办和反馈阈值评估已落地；历史 `lixian` 候选影子执行 7/7、报告对账与回滚通过；平台仍明确显示 0 Active RuntimeBundle | 重新生成并人工审批首批 Parser 及规则链；使用真实预发布事件关闭场景 42 的反馈提交、候选回放、阈值暂停和上一 Bundle 回滚证据后才允许超过 20% 灰度，再完成真实 `live_capture`、baseline route、5/20/50/100% 与两个生产周期对账 |

## 需求到证据

| 需求 | 权威实现或计划落点 | 测试或验证 | 状态 |
|---|---|---|---|
| 检查项是业务主身份 | `docs/adr/008-check-item-business-identity.md` | 架构评审 | done |
| 稳定资产身份与中文业务展示分离 | `src/core/check_items/presentation.py` + API presentation projection | Active Catalog 20/20 术语覆盖；未知检查项安全回退；生产工作台、巡检 Profile、运行包覆盖与装配抽屉、批次结果、风险详情和报告共用投影；Catalog 原名仍保留为资产元数据。38 项后端重点回归、111 项跨模块回归、工作台/覆盖页组件测试、生产构建与真实浏览器复验通过 | done |
| 版本化检查项目录 | `src/core/check_items/assets/catalog.v10.yaml` | `test_top_catalog_is_check_item_centered_and_complete`；光功率事件约束按实际 Policy 阈值角色派生，Alarm-only 设备不再被强迫生成 Warning 事件；故障/恢复类别、严重度、恢复配对与严重告警独占优先级完整 | done |
| CheckItem/Implementation 版本不可原地修改 | `src/core/check_items/catalog.py` | `test_catalog_bootstrap_is_idempotent_and_versions_are_immutable` | done |
| 规范化 CheckItem/Implementation/Parser 关系 | `netops_check_item_implementation_links`、`netops_collection_implementation_parsers` | 0058 migration + metadata create | done |
| 身份不足不猜测 | `CheckItemImplementationResolver` | missing family、ambiguous candidate tests | done |
| 一条命令服务多个检查项只采集一次 | `build_shadow_plan()` | NX-OS CPU/Memory `show system resources` 去重测试 | done |
| 执行完成不等于健康 | `CheckExecutionRecord` + `CheckAssessmentRecord` | completed + unable_to_determine test | done |
| 当前状态风险不依赖历史基线 | `CurrentStateFindingService` + `CheckItemExecutionService` | 首次巡检可形成 Finding/RiskCase，不伪造 Change；生命周期测试 | done |
| ComparisonContext 冻结基线选择 | `ComparisonContextService` + `ProductionBaselineCandidateRepository` + `netops_comparison_contexts` | 首周期、自动 previous-success、用途/资格/摘要/合同拒绝、同时间歧义、过期、零/多实现、Contract/Bundle 漂移、UTC 等价摘要、H3C/Cisco 真实历史输出与 200 目标批量查询 | done / verified_real_postgresql_historical_evidence / production_current_pending |
| 当前状态时效与零记录语义 | CheckItem freshness/empty-result policy + EvidenceQualification/CoverageDecision | stale、unknown time、structured zero-record、approved not-applicable 和报告披露测试已存在；原始资格不读取 `observation_count`，Parser 失败形成 `not_evaluable` | done |
| 设备身份与证据资格 | `EvidenceQualificationRecord` + 0063 | identity conflict fail-closed、不可变摘要、真实 `lixian` E2E；真实导入保留同 IP 0.95 与同名 0.85 的有序候选，未确认时执行 409，人工选择精确 Inventory UUID 后同一事务立即重算为 ready；资格按 CheckExecution 覆盖完整选中 Snapshot 集合，Parser 失败仍可追溯 | done / verified_full_stack |
| CLI 输出质量先于 Parser | Implementation output-quality policy + EvidenceQualification | 分类器覆盖 truncated/paged/device error/empty；Importer 已实现“持久化全部 raw → 按 Execution 冻结 Qualification → Parser → 单次附着派生结果”，未完成流式片段不提前解析；47 项导入/Runner 与 33 项 Assessment 回归通过 | done |
| 实体覆盖分母与完整性 | `CoverageDecisionRecord` + 0063 | fixed/inventory/derived/unbounded/unknown 决策、分母引用和报告披露 | done |
| 当前风险与历史待复核风险分离 | RiskCase verification status + CheckAssessment | 失败或过期证据不刷新 last_seen；报告 current/not_reverified/recovery_pending 分区 | done |
| 风险恢复、整改验收与正式关闭分离 | `RiskCaseService` + 风险生命周期 API + `RiskCaseWorkspace` + RiskOccurrence/RiskRemediation + 迁移 `0072-0074` | 服务端候选重新校验同一设备/检查项/实体的成功 Assessment、Qualification、Coverage、冻结 RuntimeBundle、恢复规则与证据；技术恢复、整改完成、独立验收和正式关闭分步执行。Occurrence、Remediation 与动作幂等键绑定不可变 episode，数据库禁止写入无 episode 的生命周期事实；关闭后复发即使复用上一轮客户端幂等键，也形成独立整改与关闭事实。真实依赖纵切覆盖候选阻断、职责分离、两类 conflict、幂等 occurrence/审计和报告对账；领域/报告/API 回归 `81 passed`、前端组件 `3 passed` | done / verified_full_stack / production_human_gate_pending |
| 当前批事件发生与聚合事件分离 | `NetworkEventOccurrenceRecord` + CheckExecution Finalizer + ReportManifest/Reporting | 当前 Change 关联的 fault/recurrence/recovery occurrence 作为批次 event_count 与报告事实；聚合 NetworkEvent 只补生命周期身份。回归证明当前 recovery occurrence 不被旧 fault 投影或旧 run 归属替代 | done |
| 事件恢复不受乱序事实回退 | 既有 NetworkEvent/Occurrence 生命周期 | `test_late_recovery_and_fault_do_not_regress_projection` | done |
| 目标设备在启动时冻结 | `PatrolBatchRecord` + `PatrolBatchDeviceRecord` | `tests/unit/test_patrol_batch_manifest.py` | done |
| 仅终态批次请求正式报告 | `request_terminal_report()` | `tests/unit/test_patrol_batch_manifest.py` | done |
| 报告事实摘要可重建 | `PatrolBatchSummaryProjection.source_digest` | `tests/unit/test_patrol_batch_projection.py` | done |
| 缺资产只登记治理任务，运行时不调用 LLM | `AssetGapService` + runtime invariant | 幂等 gap、服务端分页/搜索与唯一 remediation action；真实 AssetGap 读取及生产 Change 缺口直接生成 409 前后，Parser Job、RuleGenerationJob、模型调用和 Workflow Run 均不变；黄金运行回放模型调用为 0 | done / verified_full_stack |
| 规则资产只能由显式离线样本任务启动 | `/api/v1/parser/asset-source-jobs` + `OfflinePatrolJob.purpose=rule_asset_source` + `source_scope.py` | AssetGap 只返回包含 gap、检查项、实现和命令的预填深链，不创建 Job；真实浏览器到达治理页并自动打开预填规则样本抽屉。Parser 调度和 Dataset Context 对缺失、非完成、用途错误或未验证来源 fail closed；真实 BFF/MinIO/Celery/Workflow/PostgreSQL 导入闭环通过；RuleGenerationContext 和候选 Bundle 只接受当前 `offline_job_id` 的证据，其他来源或无来源历史上下文不能串入；手工 legacy Provider 接口保留 | done / verified_full_stack |
| 规则样本任务可恢复交互 | `OfflinePatrolPanel` 规则来源模式 + 持久化 OfflinePatrolJob + `OfflinePatrolJobService.retry()` | `draft/uploading/failed` 均复用原任务继续；失败重试恢复到最近可达配置阶段，不复制 Job。真实浏览器已从关闭抽屉的原任务继续上传 `lixian/Q2`，完成 1 文件/1 设备/27 命令预检、将未匹配身份明确确认为外部设备、启动处理并等待真实 Worker 导入完成；任务状态、流水线阶段与资产缺口随服务端事实刷新。同任务 URL 页签同步与后端 `source_job_tab=sources` 契约已有 11 项组件和 4 项后端回归 | done / verified_browser_worker |
| Parser 资产身份与输出变体分离 | `RuleAssetCommandUnitRecord` + `command_asset_units.py` | `asset_key` 只由 vendor/profile/canonical command 决定；当前来源的 distinct output signatures 作为验证变体集合，不拆分重复资产。真实 BFF/API/PostgreSQL 对账验证 1 个命令资产、1 个结构变体、vendor/model/search 筛选和分页边界；已有 Active Parser 时 Parser Job 与模型调用均无增长 | done / verified_full_stack |
| 规则生成来源在最终发布前持续可信 | `require_current_rule_generation_context()` + Parser dispatch/context/persister/revision/automation/approval | 冻结 source manifest、source scope digest、sanitized context digest；文件、Dataset、PatrolRun、Snapshot 或上下文摘要漂移后，工程修订、自动化发布资格评估与最终审批均拒绝；资格评估只准备人工审批证据，不执行发布 | done |
| 当前状态规则是一等生产资产 | `current_state_rule` + `CurrentStateEvaluator` + RuntimeBundle assembly | 健康类目标缺少 current-state scope 规则时装配阻断；首次无基线仍可形成 Finding/RiskCase，Change 保持不适用 | done |
| Parser 候选证据与生产运行时一致 | `ParserCandidateSampleEvidence` + ParserSpec evidence v2 + approval pack v2 | 候选创建事务冻结精确 sample link、角色/期望、sanitized/raw 摘要和 Manifest；审批比较、导出和激活不再扫描可变 Dataset。迁移 `0080` 已拒绝无关联的旧未发布候选，`0081/0082` 已校正 current 样本计数与重试生命周期；S6850 v2 审批包使用生产 ParserRuntime 对 4 份不可变样本执行五个具名 record set，得到 508 条记录，runtime parity 与激活治理检查均通过 | core done / migration applied / ParserBundle verified / blocked_by_human_gate |
| Parser 语义复核、修订与发布分离 | `netops_parser_spec_revisions` + `retry-dispatch` + `record_semantic_review()` + `/activate` + 服务端 actor governance | 每次 LLM/人工 ParserSpec 变更冻结 R 版本、完整 JSON、摘要、操作者、原因和被淘汰候选；修订只拒绝未发布候选并保留 Active Parser；服务端一次性重试调度在状态变化前后重验门禁，失败回到带原因的终态。复核冻结模板/Contract/ParserSpec/样本/解析结果摘要且不改候选状态；摘要漂移失效；同一操作者发布拒绝；发布写独立 Audit 与 Outbox。真实 `lixian` 样本经 BFF/API/MinIO/Celery/Workflow/PostgreSQL 完成 R1 `contract_conflict`、R2 成功、R3 失效传播、旧候选激活 409 和新候选生产 Runtime 复放；新候选仍由人工语义复核阻断。隔离候选另用双账号完成 `pending_approval -> review -> active -> Resolver` | core done / revision verified_full_stack / approval verified_full_stack / blocked_by_human_gate |
| NTC 来源、兼容转换与升级治理 | `ntc_resolver.py` + `ntc_upgrade.py` + Parser 审批抽屉 | 原始模板、最终候选、包版本、许可证、platform mapping、逐行转换和双摘要不可变冻结；`ntc-platform-compat/v2` 只做 TextFSM Value 标识符大小写规范化及可信 catch-all Error 转换，不写入 canonical 字段；生成、审批比较与激活共用客观门禁合同。真实 H3C 样本经 BFF/Worker/DeepSeek/生产 Runtime 形成 8 条记录并进入待审批；升级差异永不自动替换 Active Parser | done / verified_full_stack / blocked_by_human_gate |
| 原子 CheckItemRuntimeBundle | `RuntimeBundleService` + 0060 | 依赖摘要、兼容拒绝、preview/draft/activate/rollback、冻结读取和数据库不变性测试 | done |
| 按检查项职责派生资产需求 | `CheckItemVersion.result_mode/assessment_mode` + canonical roles + `asset_requirements.py` + `RuntimeBundleAssemblyPlanner` | `asset-requirement-profile/v1` 已区分 structured、normalized_diff、hash_only、evidence_only，并作为同一冻结协议贯穿 Dataset Context、条件 Workflow、Contract/Policy/Rule 生成、persister、Bundle read model、工程复核、审批与装配；按 observation type 校验 CurrentState/Change/Event 角色，非必需资产显式 `not_required` 且混入时 fail closed。周期、设备、记录数量及预计事件量门槛仅在画像要求历史回放时生效，Contract-only 单周期证据不会被阻断；控制面已统一按“历史回放门槛”表达。规则域 62 项、跨模块 211 项、Workflow 36 项回归及前端 lint/build 通过 | done |
| 离线任务资产视图服从检查项画像 | `OfflineRuleAssetPipeline` 内嵌 `RuleAssetProductionWorkbench` + `/parser/asset-production/check-items/` | 移除任务详情中的旧命令级完整链视图；`offline_job_id` 只限定数据范围，不再误触发首条详情抽屉。组件回归验证 `device.version` 只展示 Identity、Parser、Contract、RuntimeBundle，`not_applicable` 的 CurrentState、Policy、Event、Comparison 不出现；真实浏览器在 `lixian/Q2` 任务中确认 20 个检查项聚合、4 个未归类单元阻断、`display version` 可作为兼容实现展开，且版本检查项未虚构 Policy/Event 缺口 | done / verified_browser |
| 规则资产发布职责分离 | `RuleAssetAutomationService` + `RuleAssetApprovalService` + 服务端 actor governance | 自动化模式仅保留 `disabled/generate_only`；旧 `auto_publish` 安全降级；自动化资格结果固定 `publish_authority=human_only`。真实 BFF/API/Worker/PostgreSQL 隔离纵切验证候选创建 actor 冻结、发布前审批拒绝、创建人自审拒绝、复核人自批拒绝和独立审批成功 | done / verified_full_stack / blocked_by_real_human_gate |
| 规则策略权威来源与确定性编译 | `change-policy-generator` + `event-rule-generator` + CheckItem policy/event constraints | ChangePolicy 确定性编译批准阈值、恢复阈值、deadband、比较模式和持续时间；EventRule 确定性编译批准事件类型、严重度、故障/恢复配对和 canonical observation 归属；隔离 CPU 双周期经七步 Workflow 生成 1 Policy 与 2 EventRule，并通过回放和发布 | done / verified_full_stack |
| 规则工程复核与发布审批分离 | `RuleAssetApprovalService._approve_bundle()` + actor governance | 候选创建人、工程复核人和发布审批人三方分离；真实 API 纵切验证三类 409 拒绝和独立审批后 Resolver 读取 Active 资产 | done / verified_full_stack / blocked_by_real_human_gate |
| RuntimeBundle 生产装配控制面 | `RuntimeBundleAssemblyPlanner`、assembly preview/assemble/activate/rollback API、`CoverageBackfillWorkspace` | preview 无写入、stale digest 拒绝、草稿幂等、激活审计与 API 409 合约；真实 BFF/API/PostgreSQL 纵切完成 draft、创建人自激活拒绝、独立激活和 Active Resolver 读取。另以 Active Catalog 20 个检查项、39 个实现对做每页 7 条的六页三方对账；子资产存在但无 Bundle 仍为待装配；所有 blocker 均返回稳定 remediation/action，浏览器可沿精确 `check_item_id` 深链进入规则样本导入 | done / verified_full_stack |
| RuntimeBundle 是唯一生产绑定 | `RuntimeBundleAssetRecord` + `CheckItemRuntimeBundleService` | RuntimeBundle 冻结全部子资产，运行时按 Bundle 读取；兼容命令投影已进入受治理的只读退出周期 | done |
| 检查项 Profile 到设备 Effective Plan | Profile/Binding API + `CheckItemEffectivePlanService` + 设备 Effective Plan HTTP | 隔离 `configuration.running` Profile 经真实 BFF/API/PostgreSQL 完成创建、刷新、影响预览、激活和显式设备绑定；端点返回精确 Profile 版本、检查项、采集命令和不可变 Runtime Manifest ID/digest。修复 PostgreSQL JSON selector 比较及设备端点只读 legacy 计划的问题；测试后 Profile、设备、Bundle、ComparisonProfile 和审计全部清理 | done / verified_full_stack |
| 实现范围不宽于 Parser 证据范围 | `runtime_bundle_assembly.parser_compatibility()` | 厂商级 Implementation + 型号级 Parser 被 fail closed；兼容证据摘要变化使 preview 失效 | done |
| EventRule 显式归属 canonical observation | `event_rule_observation_types()` + RuntimeBundle 装配 | 仅接受 `condition_json.observation_types`，缺失、越界和外部映射不一致均阻断 | done |
| shared-command observation projection | RuntimeBundle `observation_projection` + Contract canonical superset 校验 | 已审批 canonical 超集只投影本检查项所需 Observation；快照内重复事实去重、冲突阻断和黄金多记录样本已验证 | done |
| fallback 可执行集合与韧性 | RuntimeBundle `executable_commands` + Effective Plan 裁剪 + ReportManifest 投影 | full/reduced/none 单测通过；真实双设备 Workflow 批次只执行完整备用路径，不可执行高优先级命令留痕为 `asset_unavailable_at_plan_time`；JSON、Markdown、Excel 同时披露 `reduced`，运行前后 Active 资产一致 | done / verified_full_stack |
| 首巡当前风险与历史变化分离 | CheckItem ComparisonContext + Change Detector action scope + CurrentStateRule | 真实 BFF/API/Celery/Workflow/PostgreSQL/Redis/MinIO/SSH 纵切中，首观测 CPU 91% 形成 Finding、RiskCase 和 `at_risk` Assessment，但 Change/Event 为 0；同批连接失败设备形成 `unable_to_determine`，五类制品同源 | done / verified_full_stack |
| 内置 EntityIdentityPolicy 可用于装配 | Gateway 启动幂等 bootstrap + RuntimeBundle 冻结版本 | 浏览器首次发现 preview/assemble 死锁后修复；启动日志确认 Active v1，预检不再返回 `entity_identity_policy_missing` | done |
| Active Parser 不冒充运行就绪 | `RuntimeBundleService` + Effective Plan | 当前 3 个 Active Parser、0 个 Active Bundle；缺 Bundle 形成 AssetGap；事务内完整 Bundle 可跑通真实样本 | done |
| 覆盖页不再用旧规则阶段冒充就绪 | `catalog_health()` + `/check-item-runtime-bundles/health` + `CoverageBackfillWorkspace` | 浏览器实测 Active Catalog 20 个检查项、39 个实现对、0 Active Bundle、39 个缺口、0%；真实 API/PostgreSQL/Resolver 的全部六页稳定键和计数一致；接口失败显示无法判断；装配抽屉按 AssetRequirementProfile 区分必需与不适用资产，缺失新资产、已有不兼容资产和多候选歧义分别进入唯一可执行下一步 | done / verified_full_stack |
| 快照内 canonical 事实对账 | `state.normalizer._reconcile_snapshot_observations` | 重复值去重、冲突值阻断单测；MSR5620 12 条记录投影为 2 条 Observation | done |
| 完整软件版本投影 | `semantic_projection.concat` | `7.1.064 + 0821P18` 投影为 `7.1.064, Release 0821P18` | done |
| Comparison schema reference 不截断 | 0062 + `ComparisonContextRecord` | 66 字符真实 catalog reference 可冻结；有损 downgrade 被阻断 | done |
| 每个计划设备 x 检查项均有 Assessment | Start preflight + CheckItemRunFinalizer + terminal gate | 成功、错误绑定、失败阶段、混合设备结果和终态集合对账单测；失败设备只影响对应 Assessment | done |
| 生产巡检只有一个权威编排入口 | `/api/v1/devices/patrol/launch` + `StartPatrolHandler` | `mode=production + launch_mode=direct` 在 preview、readiness、数据库和队列前返回 409；真实 BFF 验证 PatrolBatch/ExecutionPlan/WorkflowRun/巡检审计与 `netops.patrol` 队列前后不变；非生产 diagnostic direct 仍有单测 | done / verified_full_stack |
| 报告同源输出 Markdown/DOCX/Excel/JSON/证据包 | `ReportManifestService` + `CheckItemReportMaterializer` + 同源 BFF 下载 | 确定性重建、事实摘要、无事件不等于健康、证据包不含 raw CLI、原子挂载测试；真实 `lixian` 报告经 BFF 下载后，五类字节分别与 Manifest SHA-256 及 MinIO 对象一致，未签发下载不再重新渲染 | done / verified_full_stack |
| 版本化跨厂商报告投影 | `src/core/check_items/report_projection.py` + `report_projections.v1.yaml` + 独立 `zh-CN` Presentation Catalog + ReportManifest 冻结快照 | 当前 20 个 CheckItemVersion 的 legacy 引用均解析到精确 `profile@1`；Profile 校验 canonical observation、维度、单位、状态与证据列，禁止厂商命令和 Parser Value 参与投影。Manifest 冻结 Profile/Presentation 摘要；Excel 只读冻结快照生成领域宽表，未消费事实仍保留在“当前观测”。跨厂商 CPU 同列、无事实目标保留、摘要篡改拒绝和真实 Manifest 冻结路径共 40 项报告回归通过 | core done / real cross-vendor production content acceptance pending |
| 报告用途约束当前状态措辞 | `ReportManifest.report_purpose` + purpose-aware projection + 0067 | v3 Manifest 在批次创建时冻结用途；v2 缺用途报告安全降级为 shadow；历史/影子/Parser/规则回放不能渲染当前健康；Markdown/DOCX/XLSX/JSON/API/UI 同口径；28 项报告/批次回归通过 | done |
| 报告用途来源证明 | `report-authority-context/v2` + 2.19.6/2.19.7 | `source_kind` 由 `patrol_mode + target_type` 独立派生，不读取 `report_purpose`；采集模式、观测时间范围、Inventory、目标/Runtime Manifest 和终态资格随 source facts 冻结并独立验摘要；签发前重算来源、authority、raw evidence 与 artifact 摘要。缺失、篡改或用途冲突的 current context 降为 `current_authority_blocked`；API/UI 展示阻断原因。离线 Q2 E2E 返回 `historical_result`、authority=`historical`、`current_state_authoritative=false` | done |
| 离线当前现状证明与独立生产批次 | `PatrolCurrentAuthorityService`、`PatrolCurrentAuthorityReplayService`、迁移 `0079`、current-authority API/BFF/UI | prepare 冻结源文件、Snapshot、PatrolRun、命令级 CollectionResult、逐设备结果、身份和三层范围摘要；正文按当前字节重算 SHA-256，同人证明、历史意图、未入账设备、源文件关联和摘要漂移 fail closed。合成新鲜工程夹具经真实 BFF/FastAPI/PostgreSQL/Celery/Redis/MinIO 验证历史拒绝、同人拒绝、幂等冲突、正文篡改拒绝、prepare/attest 并发 exactly-once、Worker 重投递计数不变、独立证明和新 `production_current` 批次。冻结两目标分别形成 `informational` 与 `unable_to_determine`，Evidence 以 Inventory UUID 对账，结论事实终态但整体健康权限关闭；浏览器验证批准后基础设施故障、刷新保持和恢复自动续跑，冻结 RuntimeBundle 漂移矩阵在 Parser 前 fail closed；五类制品 SHA-256/MinIO 三方一致。证据明确 `production_network_claim=false` | engineering_verified / production_evidence_pending |
| 网络结论风险与完整性分离 | `build_check_item_report_view()` + 2.19.2 结论合同 | `risk_conclusion`、`assessment_completeness`、`comparison_readiness`、`authority` 已作为稳定结构进入 JSON、Markdown、DOCX、XLSX、API 与签发 UI；历史事实仍不得提升为当前健康；21 项报告、5 项事务和签发组件测试通过 | done |
| 原始输出与证据索引分权交付 | raw-output export + evidence package + `raw_evidence_set_digest` + 0067 | 原始输出只保留 `.txt` 并附设备、登录失败、输出缺失和校验清单；证据包无 raw CLI 正文。v3 Manifest 冻结 Snapshot/raw/source file/run/dataset 集合摘要，下载按精确 `report_manifest_id` 和 `(PatrolRun, source_file_hash)` 全集合 fail closed；真实 `lixian/Q2` 经 BFF/FastAPI/PostgreSQL/MinIO 验证 operator 403、admin 200、原始文件字节一致和内部 sidecar 排除 | done / verified_full_stack |
| 报告截止时刻与迟到事实 | `ReportManifestRecord.report_cutoff_at/fact_ingestion_high_watermark` + 0065 | 双边界、无变化幂等和迟到事实新版本的 31 项重点测试通过；专用 `REPEATABLE READ` 事务和 `2000 × 20` 报告实测通过。真实 `lixian/Q2` 经 BFF/API/Celery/PostgreSQL/Redis/MinIO 形成 v1/v2，v2 冻结 4 条迟到事实，v1 五类对象和签发摘要保持不变 | done / verified_full_stack |
| 报告异步物化 | `request_terminal_report()` + `PatrolReportMaterializationRequestRecord` + Celery report task + materialization status API/UI | 迁移 `0071` 持久化 `(batch_id, idempotency_key)` 请求账本、同键终态重放、确定性 task id、旧 Worker fencing、完成态防回退、BFF 协议头转发、对象存储/仓储失败保旧与孤儿清理；重点回归 `31 passed`、BFF `7 passed`、前端构建通过。真实 BFF -> FastAPI -> PostgreSQL -> Celery/RabbitMQ -> Redis -> MinIO 故障注入验证请求状态 `completed/failed/completed` 和显式新键重试 | done / verified_full_stack |
| 服务端职责分离 | `src/common/governance.py` + Parser/Rule/RuntimeBundle/ReportRelease | 规范化 actor identity 与 59 项重点回归通过 | done |
| 事实冻结与正式签发分离 | `ReportReleaseRecord` + 0063 + patrol API/UI | 精确 artifact digest 校验、批次身份拒绝、审计和 MinIO 回读；真实双账号全栈验证 v1/v2 独立签发、旧签发不可变、版本列表和精确版本下载。自动化只证明工程机制，真实甲方内容复核仍保留 | done / verified_full_stack / blocked_by_real_human_gate |
| 显式内容验收与独立签发 | `ReportContentReviewService` + `ReportContentReviewRecord/ItemRecord` + 0084 + patrol API + `ReportReleasePanel` | v5 Manifest 机器全量对账、风险分层确定性抽样、每检查项追溯项、服务端分页/筛选、不可变决定、退回修订、精确五类制品摘要验收、内容验收人与最终签发人职责分离；后端 `19 passed`、前端 `5 passed`、生产构建 `3387 modules`；真实依赖 v1/v2 验证内容接受、独立签发、旧版结论/正式性/制品冻结和清理 | integration_done / verified_full_stack / real_human_pending |
| 报告读取无副作用与精确版本下载 | `ReportManifestService.list_for_batch()` + workflow report download API + BFF + `ReportReleasePanel` | GET 只读取已物化 Manifest 和对象存储制品，不再调用 build；支持 `report_manifest_id`、Manifest/版本响应头、服务端版本历史和旧版本下载；未挂载制品 fail closed。后端定向 15 项、BFF 3 项、前端签发面板 3 项和生产构建通过 | done / verified_full_stack |
| 确定性交付物字节级可复现 | Office ZIP/core properties 归一化 + artifact SHA-256 | 延时重建 XLSX 摘要一致；五类交付物同 Manifest 重建一致 | done |
| 甲方正式交付内容合同 | 2.19.3 + `CheckItemReportMaterializer` + `ReportReleaseRecord` | Markdown/DOCX 已统一十章；变化与事件显示当前值、阈值、阈值来源和规则版本；风险包含当前值、判据、责任人和期限。`check_item_report_content_acceptance.py` 固定生成 2 项风险、2 项事件、历史待复核、恢复待确认和 `parse_failed` 无法判断的五类同源制品；17 条 Claim 与所有业务明细逐行对账，证据包输出 Claim JSON/CSV。技术附表共 15 页，包含 CPU、接口状态、接口光功率三个由冻结 `ReportProjectionProfile` 生成的统一领域页；投影摘要非空，采集路径韧性不再默认为 `0/0`，数值与单位分列，关键值正确且公式错误为 0。DOCX 无障碍审计 high/medium/low 均为 0；本机无 LibreOffice/Word，因此风险版 DOCX 的逐页视觉复核仍属于真实签发门禁，不能冒充甲方验收 | done / excel_visual_verified / production_human_gate_pending |
| 风险技术恢复、整改验收与正式关闭 | `RiskCaseService` + 迁移 `0072-0074` + 风险 API/UI + 报告投影 | 真实 BFF/FastAPI/PostgreSQL 纵切验证覆盖不足阻断、恢复确认、完成登记人与验收人职责分离、正式关闭、两类 conflict、episode-bound occurrence/remediation、幂等审计和影子报告；后端 `81 passed`、前端 `3 passed`、生产构建通过 | done / verified_full_stack / production_human_gate_pending |
| 批次检查项明细 cursor 分页 | `CheckItemBatchResultService` + `/check-results` | cursor v3 已绑定 HMAC、TTL、规范 Base64URL、批次、筛选 scope、稳定 keyset 和首屏 total；深 offset 超过 1000 时拒绝并要求使用 `next_cursor`；7 项合同测试通过。真实 FastAPI/PostgreSQL 用例对 1,105 条结果连续翻至尾页，无遗漏、无重复，后续页 `COUNT=0`，非法游标与深 offset 均返回 400，事务整体回滚 | done / verified_http_postgresql |
| 2000 台 × Top 八项服务端分页与报告 | `check_item_runtime_capacity_benchmark.py` | 16000 结论；摘要 0.63 秒；100 条页 P95 0.805 秒；报告 69.3 秒；全部回滚无库污染 | done |
| 2000 台 × 20 个真实检查项最终容量 | `check_item_runtime_capacity_benchmark.py` | 第二次完整运行：40000 执行、40000 Assessment；预览 32.27 秒、冻结 122.36 秒、报告事实 38.26 秒、五类制品 225.58 秒、总计 434.60 秒；所有 SLO 通过并完整回滚 | done |
| 全局/站点/设备三级租约 | `src/core/patrol/concurrency.py` + `PatrolTool` + Redis fencing token + PatrolRun owner token | 42 项单元/集成回归通过；真实 Redis 验证旧 token 失效、新 token 单调递增且旧 owner 不能删除新租约；RabbitMQ 重投递与 PostgreSQL 旧 owner 拒写通过 | done |
| 持久化 RolloutCampaign 控制器 | `src/core/check_items/rollout.py`、迁移 `0068`、Parser API 和 `RolloutCampaignWorkspace` | exact scope、职责分离、失败暂停/恢复、资产漂移 fail-closed、重复 context、完整六阶段和稳定 5% 路由回归通过；前端组件与构建验证 | done |
| RolloutStage 可信执行器 | `src/core/check_items/rollout_execution.py`、`rollout_capture.py`、迁移 `0069/0070`、Celery Outbox/Worker/Evaluator 和执行证据 UI | 19 项 rollout/capture 领域测试覆盖同源双轨、完整多命令周期、实时采集两段状态机、缺命令阻断、首次无基线、严重差异、幂等重放、fencing 和不可变证据；巡检入口 14 项及 Parser API 64 项回归通过；真实候选 `snapshot_replay` 覆盖 4 场景、7 目标、14 Snapshot/Projection，执行与 Assessment 100%、0 unable-to-determine、报告对账和回滚通过 | verified_snapshot_replay / implemented_live_capture / integration_pending |
| `live_capture` 只读证据采集 | `ProductionPatrolCaptureAdapter` + `rollout-live-capture` Workflow + reconciler | 冻结逐设备命令；复用统一 Patrol handler；强制 `parser_validation/shadow_validation`；采集 Workflow 无 State/Change/Event 写步骤；完整 Snapshot 核对和缺命令失败测试通过 | implemented / real_device_integration_pending |
| 受治理 legacy baseline | `RolloutLegacyRouteService` + `RuntimeBundleCompatibilityAdapter` + 显式 Registry | route/config/dependency 摘要、精确检查项/实现作用域、Active RuntimeBundle 依赖与漂移 fail-closed 回归通过；不支持任意动态 import | verified_automated / site_route_freeze_pending |
| `lixian` Q1/Q2/Q3 多型号回归 | v1 manifest：Q1 2/2/54、Q2 6/6/152、Q3 6/6/162 | 黄金资产 16/16、56 样本、1236 Parser 记录、0 LLM 调用；发布前候选回放又覆盖 CPU 36 Observation/2 Change、接口状态 72 Observation/0 Change、S6850 光功率 524 Observation/46 Change/4 周期 Event，以及运行配置 1 条 normalized Change；真实样本未越过事件边界时如实保留 0 Event，触发/恢复另做合成可达性验证；全部回滚且不激活生产资产 | engineering_regression_done / production_asset_gate_pending |
| `lixian` 新检查项运行时成功路径 | `check_item_lixian_runtime_e2e.py` | 真实 MSR5620 Q2/Q3 `display version` 均为 12 Parser rows -> 型号与完整软件版本 2 Observation -> confirmed/complete/stale + fixed 1/1 coverage -> informational/complete Assessment -> MinIO 五类制品 -> immutable release；`Release 0821P18` 未丢失，报告权限保持 `offline_historical`；含真实 Workflow Run/Step，回滚后数据库与对象存储不变 | done |
| 新鲜当前周期生产报告 | CurrentAuthority + `report_purpose=production_current` + Active RuntimeBundle + fresh EvidenceQualification | CurrentAuthority 的独立候选批次与同链重放已实现；`lixian` 只能作为历史/影子和拒绝路径证据，尚无经人工冻结目标矩阵的新鲜真实生产批次与正式签发 | blocked_by_asset_readiness_and_fresh_evidence |
| 生产检查项 RuntimeBundle 覆盖 | `CheckItemRuntimeBundleService` | 当前 Active Bundle 为 0；应形成 AssetGap 和无法判断，不得伪造健康 | blocked_by_asset_readiness |
| M0/M1/M2/M3 产品投产里程碑 | 2.19.4 + 发布证据矩阵 | M0 大部分工程能力已有证据；M1 固定为 CPU、接口状态、光功率、设备版本和运行配置五条结果纵切，仍受真实资产审批和 current 批次阻断；M2/M3 不得由黄金回放或容量测试替代 | in_progress |
| 批次与最终产品双层验收合同 | 2.23.1 `ContentAccepted` + `ProductGoalAchieved`；2.19.8 结论权限合同 | 单一服务端策略已对 Target/Execution/Assessment/Qualification/Coverage/Snapshot/RiskCase 做精确对账；Evidence 优先使用冻结 Inventory UUID，外部别名不再破坏终态一致性；`unable_to_determine` 统计覆盖所有 included 结果模式，非健康类失败同样阻止整体健康。v4 Manifest 冻结同一决定，五类制品、API、UI 与签发重验共用；105 目标对四类边界场景及 CurrentAuthority 两目标混合场景经真实 BFF/API/PostgreSQL/Celery/Redis/MinIO 验证。工程结论合同已完成，但真实内容验收、签发、生产周期和多厂商规模证据仍独立保留 | conclusion_contract_done / verified_full_stack / product_goal_in_progress |
| 文档治理与断链门禁 | `docs/document-governance.yaml` + `scripts/docs/audit_docs.py` | 75 份文档全部登记，未登记 0、坏链 0；README 已移除 superseded 推荐入口，并新增 `governed-index` 状态门禁，active 索引链接到 superseded/archived 文档时 CI 失败；治理测试通过 | done |
| 废弃代码安全退出 | `docs/implementation/deprecations.yaml` + `src/observability/deprecations.py` + `scripts/deprecations/export_runtime_evidence.py` + `scripts/deprecations/audit_deprecations.py` + 迁移 `0095` | 5 个候选均有 owner、替代方案、日期、回滚策略和七项退出门禁；调用由 Prometheus、结构化日志和无载荷 PostgreSQL 台账记录。审计强制逐 gate 不可变 URI/SHA-256、至少两个不重叠发布周期及零调用；命令中心候选已收窄为旧顶层列表，不误删必要的逐命令证据下钻。当前 1 个 observing、2 个 read_only、2 个 write_disabled，真实观察周期、零写入、替代覆盖和回滚证据尚未完成 | engineering_done / observation_started / removal_gated |

## Phase 0 保护对象

| 保护对象 | 当前权威实现 | 迁移原则 |
|---|---|---|
| 当前状态规则评估 | `src/core/workflows/patrol_batch_service.py:evaluate_current_state` | 提升为正式阶段并持久化 Finding，不重写规则语义 |
| Event 生命周期 | `NetworkEventRecord`、`NetworkEventOccurrenceRecord` | RiskCase 首期从现有相关键投影，不复制一套冲突生命周期 |
| 批次目标快照 | `PatrolBatchRecord`、`PatrolBatchDeviceRecord` | 扩展检查项 target manifest，不改写历史目标 |
| 终态报告请求 | `src/core/patrol/batch_manifest.py:request_terminal_report` | 正式报告仍只能由全终态批次触发 |
| 报告重建摘要 | `PatrolBatchSummaryProjection.source_digest` | ReportManifest 纳入目标、资产、比较和事实摘要 |
| 命令中心兼容读 | Command Profile、legacy batch readers | shadow 指标达到退出门禁前只读保留 |

## 最近验证（2026-07-30）

### 自动化回归

下表中的完整套件数字是本轮改动前的最近基线，不代表 0065、异步报告和 cursor 优化后的全量套件已经
重新通过。最新增量证据单独列出，二者都不得被改写为“最终验收完成”。

| 验证面 | 结果 |
|---|---|
| 后端完整单元测试历史基线 | `1107 passed`；最新改动后待重跑 |
| 后端重点回归 | 报告/Manifest/Assessment/批次终态 `69 passed`；断连、超时、重投递、fencing、Parser 失败语义增量 `31 passed`；此前三级并发完整增量 `42 passed` |
| cutoff/high-watermark 重点回归 | `31 passed` |
| cursor/runtime 重点回归 | `33 passed` |
| 异步报告与单一巡检入口重点回归 | 后端异步报告 `31 passed`，Django BFF `8 passed`，报告下载与渲染 `26 passed`，巡检启动定向 `5 passed`，前端生产构建通过；真实异步故障注入、五类同源下载、v1/v2 迟到事实独立重签和生产 direct 无副作用拒绝脚本均通过 |
| 服务端职责分离重点回归 | `59 passed` |
| 前端单元测试 | 历史完整基线 `26` 个测试文件、`84 passed`；CurrentAuthority/离线巡检最新定向 `2` 个文件、`21 passed` |
| 前端静态检查 | ESLint、UTF-8 源码扫描通过 |
| 前端生产构建 | Vite 构建通过，最新转换 `3387` 个模块；检查项中文展示投影构建后真实页面复验通过 |
| 取消终态、晚到回调与报告分母增量 | Manifest、取消、PostgreSQL 故障注入与 Assessment 组合 `77 passed`；Workflow/Worker/重投递/fencing 扩展组合 `91 passed`。未启动取消、凭据失败和命令超时目标均形成带精确原因的 UTD，已完成事实保留，五类 MinIO 制品按完整分母生成；晚到成功/失败均被拒绝。CheckItem Finalizer 已按命令级 CollectionResult 在 Parser 前区分 `command_timeout`、`snapshot_persistence_failed` 和真实 `parse_failed`；失败 Workflow 又按稳定失败码与实际失败 Skill 映射 collection/normalization/comparison/assessment，并从巡检步骤取回 Snapshot Run 冻结资格与 CoverageDecision。该阶段含 fallback 和混合结算的定向回归 `7 passed`、Workflow 阶段/取消回归 `14 passed`、采集/Runner/故障注入组合 `45 passed`。规模门禁以常数级 SELECT 结算 `1000 台 × 2 检查项 = 2000` 个 UTD，重复执行新增 0 条。隔离真实 RabbitMQ/Celery 队列完成 Worker A 硬终止、Worker B 重投递、旧 attempt abandoned 和新 attempt completed，队列与数据库清理为 0；真实浏览器已验证取消入口。预发布真实设备断连、实际命令超时和千台在线取消压测仍待完成 |
| 风险恢复、整改验收、正式关闭与本批事件归属增量 | 风险/Assessment/报告/API 组合 `81 passed`；风险工作台组件 `3 passed`；Vite 生产构建通过；真实 BFF/FastAPI/PostgreSQL 纵切通过。覆盖候选阻断、冻结规则重验、episode-bound 幂等确认、技术恢复、独立验收、正式关闭、关闭后复发隔离，以及 recovery occurrence 归入当前批次 |
| 浏览器 E2E | `20 passed`，覆盖生产工作台、发布资产、覆盖回填、审计、设备巡检导航 |
| 浏览器可访问性 | `4 passed`，桌面与移动端 axe 门禁通过 |
| 视觉回归 | `20 passed`，覆盖桌面、平板和移动端 |
| 数据库迁移 | `heads` 与本地 PostgreSQL `current` 均为 `0090_assessment_policy_cutover`；`0080-0084` 完成不可变候选样本、attempt、多记录集和内容验收，`0085` 增加 RiskEpisodeAssociation，`0086-0088` 增加判断依据快照、策略资产与适用性治理，`0089/0090` 增加历史影子和目标级切换/回滚 |
| CurrentAuthority 增量回归 | 服务、同链重放、CollectionPolicy、Scope/Plan、StateNormalizer、设备巡检 runner、Assessment、离线任务和报告结论组合 `194 passed`；CurrentAuthority、重放、恢复任务、生产准入与 Beat 心跳组合 `31 passed`，Runtime Manifest 定向 `6 passed`。临时基础设施阻断保持 `approved / awaiting_current_plan` 且不生成设备失败事实，整轮候选共享一次探测；solo Worker 只信任当前任务实际 delivery queue。prepare/attest 并发与 Worker 重投递真实依赖已通过；浏览器完成空计划 fail closed、批准后 Beat stale、整页刷新、延迟调度和恢复续跑；冻结 Bundle 精确版本与篡改失败合同已验证。离线巡检与 CurrentAuthority 面板 `21 passed`、生产构建通过 |
| RolloutCampaign/StageExecution 增量回归 | rollout/capture 领域回归 `19 passed`；巡检 handler/runner `14 passed`；Parser API 管理 `64 passed`，含 `start=202`、公共 result 删除、执行范围隔离、实时模式透传和旧路径管理员合约；前端生产工作台 `10 passed`，含详情抽屉内显式选择 `live_capture` 并启动阶段；生产构建转换 `3386` 个模块 |
| 报告用途、版本与 raw evidence 契约 | 后端报告/批次回归 `28 passed`；PostgreSQL Manifest/下载/风险/签发回归 `48 passed`；精确版本下载定向回归 `15 passed`、原始输出/报告定向回归 `37 passed`、BFF 全组 `10 passed`、前端签发面板 `3 passed`；Vite 构建通过 |
| 内容验收对象完整性与规模增量 | 后端 `23 passed`，覆盖对象缺失、摘要漂移、验收后篡改、API 409、1000 条分页/幂等/恢复；报告相关扩展套件 `61 passed`；前端签发面板 `6 passed`，含第 20 页真实服务端参数；Vite 转换 `3387` 个模块。两条真实依赖脚本和浏览器 1280×720 复验通过，临时批次、Review、1000 条 item、账号与 MinIO 对象残留均为 0 |
| 结论权限与失败事实场景 22/25/26/27/30/35 | 报告、Manifest、Assessment、批次与风险回归 `103 passed`，结论策略 `10 passed`，报告投影定向回归 `28 passed`；十一个隔离批次共 112 个目标对，覆盖零记录、截断、实体 48/40、三类 Parser 失败、历史风险未复核及四级健康表达；五类制品、API、签发页、Manifest 摘要与 MinIO 字节一致 |
| Parser 失败证据链 | 重点套件合计 `38 passed`：原始 Qualification 保留、Coverage 为 `not_evaluable`、Assessment 为 `unable_to_determine`，报告事务分支同时通过 |
| ParserSpec 双摘要、运行时一致性与生命周期治理 | `76 passed`：source/compiled ParserSpec 摘要、合法占位类型、生产 ParserRuntime 复放、语义复核摘要失效、复核/发布职责分离、独立发布审计、旧候选失效、Contract 退役与 Rollout 状态回归通过 |
| ParserSpec 修订与一次性重新生成增量 | 后端 Parser 治理/API/生成/错误分类组合 `114 passed`；前端 `ParserGenerationStep` `8 passed`。真实 `lixian` 纵切通过 BFF/API/MinIO/Celery/Workflow/PostgreSQL 完成不可满足 R1、R2/R3 修订、重调度、旧候选 409、生产 Runtime 8 条记录复放和范围化清理；错误码为 `contract_conflict`，新候选保持 `pending_approval` 并由人工门禁阻断 |
| Parser 候选证据与 attempt 生命周期增量 | `ParserCandidateSampleEvidence`、current/retired 样本、权威 PatrolRun 对账、删除保护、候选失效和审批只读精确证据组合 `150 passed`；CPU、接口状态和 MSR absent-only Job 均已生成 v1 证据候选。S6850 光功率 Job 已在 ParserSpec R6 上完成 12 个持久化 attempt、五 record-set 候选、全样本执行和独立 Reviewer，形成 4 样本/508 记录 v2 审批包。迁移 `0080-0082` 已应用，Active Parser 始终为 3 |
| NTC 客观执行与人工直审 | `47 passed`：NTC 身份映射、包版本/许可证、原始/派生模板摘要、可信兼容转换、逐样本字段与记录证据、零记录客观语义、platform 映射消失时生成 `unavailable` 阻断报告、跳过生成模型及 AI 复核失败转人工直审；审批抽屉组件 `3 passed`，Vite 生产构建通过 |
| 规则来源完整性 | `33 passed`：显式离线来源、文件与 Dataset/Run/Snapshot Manifest、sanitized context digest、工程修订、自动化发布资格评估和最终审批前重校验 |
| AssetRequirementProfile 端到端合同 | 规则域 `62 passed`、跨模块 `211 passed`、Workflow `36 passed`：Contract-only 路径跳过 Policy/Rule/Replay；健康路径按 observation type 校验 CurrentStateRule、ChangePolicy 和所需故障/恢复 Event；多余资产、角色缺失、来源/画像漂移均 fail closed |
| 人工发布权 | 自动化相关回归证明 legacy `auto_publish` 降级为 `generate_only`、机器门禁输出 `human_only`、证据型单样本可进入人工复核但不会发布；前端仅显示“人工触发/自动准备候选”，lint 与生产构建通过 |
| 2000 × 20 容量 | `passed`：40000 执行单元；总耗时 434.60 秒；结果页 P95 0.136 秒；报告事务为 `REPEATABLE READ`；证据 `.tmp/check-item-capacity/runtime-2000x20-20260729-v3.json` |

### 真实样本与运行时证据

| 验证项 | 结果 | 证据 |
|---|---|---|
| `lixian` Parser 基准 | `16/16` 资产通过，`56` 份样本、`1236` 条记录，`0` 次 LLM 调用 | `.tmp/simple-parser-lixian-benchmark-20260729/simple-parser-lixian-benchmark.json` |
| 规则资产来源导入与归组真实全栈 | 真实 `lixian display version` 经 BFF、MinIO、Celery 和 Workflow 导入，形成 1 文件、1 外部设备、1 命令、12 条结构化记录；来源详情、生产工作台 API 与 PostgreSQL 对账为 1 个稳定命令资产、1 个当前来源结构变体，vendor/model/search 与分页边界通过；Parser Job 与模型调用均无增长；跨来源 Context/Bundle 被拒绝，测试数据和对象全部清理 | `.tmp/rule-asset-source-import-full-stack-e2e/summary.json` |
| NTC Parser 候选真实全栈 | 真实 `lixian display device manuinfo` 经 BFF、MinIO、Celery、Workflow 和 PostgreSQL 创建 Parser Job；命中 NTC 9.1.0 模板，DeepSeek 初审通过，生产 ParserRuntime 对 `1/1` 样本解析 `8` 条记录。候选保持 `pending_approval`，`approval_ready=false`，唯一 blocker 为尚未接受人工语义复核；BFF 删除理由与全部范围化清理通过 | `.tmp/parser-ntc-candidate-full-stack-e2e/summary.json` |
| ParserSpec 修订与重新调度真实全栈 | 同一真实 `lixian` 输出先提交不可满足 R1，Worker 精确写入 `contract_conflict`；R2 修订生成成功，R3 再修订后旧候选变为 `rejected` 且激活返回 409，最终候选由生产 ParserRuntime 解析 8 条记录并等待人工语义复核。临时用户、离线任务、Workflow、PatrolRun、Parser Job、候选、文件夹、文件和 MinIO 对象全部清理，存活 Job/候选均为 0 | `.tmp/parser-spec-revision-full-stack-e2e/summary.json` |
| S6850 ParserBundle 与 v2 审批包 | `lixian` Q2/Q3 两台 S6850 的 4 份真实样本经 Workflow、独立 AI Worker、DeepSeek、生产 ParserRuntime 和 PostgreSQL 完成 R6；候选冻结 5 个具名 record set、508 条记录、逐样本解析表、模板/样本摘要和 Reviewer 结论。审批包状态为 `ready_for_human_review`，候选保持 `pending_approval`，未执行激活 | `.tmp/parser-candidate-approval-pack-s6850-r6/manifest.json` |
| S6850 sanitized 规则链 dry-run | 只读取 4 个结构化样本组、508 条记录，`raw_cli_included=false`；形成 presence、Rx、Tx 三个 Policy 和 6 个 fault/recovery EventRule。设备输出仅有 Alarm 阈值，因此规则为 Rx/Tx Critical + Recovered，没有虚构 Low Warning；Event 错误为 0 | `.tmp/s6850-r6-rule-chain-dry-run-v7.json` |
| S6850 事务 RuntimeBundle 回放 | 候选资产仅在外层事务中临时装配；2 台设备、4 份样本形成 524 条 Observation、46 条 Change、4 条周期事件和 4 条当前事件。历史回放命中 4 条 Change、0 collision；日事件估算因只有一个比较周期标记 `one_day_floor`、`reliable=false`、`sparse_history=true`。数据库前后均为 Active Parser 3、Contract 3、Policy 11、EventRule 11、RuntimeBundle 0 | `.tmp/s6850-r6-runtime-replay-v3/summary.json` |
| Parser 复核与独立发布真实全栈 | 隔离 H3C 候选经生产 ParserRuntime 对 `1/1` 样本解析 2 条记录；复核前发布和同人发布均返回 400；人工直审保持 pending，第二账号发布后 Active Parser、Active Contract、两条审计、Outbox 与 Resolver 一致；模板、Contract、Outbox、Snapshot、规则种子和临时用户残留均为 0 | `.tmp/parser-semantic-review-full-stack-e2e/summary.json` |
| 规则资产与 RuntimeBundle 真实全栈 | 隔离 CPU 双周期经 BFF/API/Celery/七步 Workflow/PostgreSQL 生成 1 Contract、1 Policy、2 EventRule；发布前审批、创建人自审、复核人自批和 RuntimeBundle 创建人自激活均被 409 拒绝；独立复核、审批、激活后两类 Resolver 读取冻结资产；清理成功且生产 Active 资产摘要前后完全一致 | `.tmp/rule-asset-runtime-bundle-full-stack-e2e/summary.json` |
| RuntimeBundle 控制面对账与修复真实全栈 | Active Catalog 20 个检查项、39 个实现对经真实 BFF/API/PostgreSQL 以每页 7 条遍历 6 页，状态筛选、搜索、越界空页和 Resolver 计数一致；当前 0 ready / 39 missing 被如实展示。子资产存在但 Bundle 缺失仍为待装配；阻断装配返回 409 且数据库零写入，每个 blocker 均有稳定 remediation/action。浏览器从 `configuration.running` 装配抽屉进入携带精确检查项的规则样本来源页 | `.tmp/runtime-bundle-control-plane-full-stack-e2e/summary.json` |
| 设备身份冲突与 AssetGap 治理真实全栈 | 真实 `lixian/Q2` CLI 经 BFF/MinIO/Celery/PostgreSQL 形成同 IP 与同名两个匹配候选；未确认执行 409，人工选择后立即 ready。AssetGap 通过只读控制面进入携带精确范围并自动打开的规则样本抽屉；直接生产缺口生成被 409 拒绝，四类治理计数全程不变，全部夹具清理 | `.tmp/asset-gap-identity-control-full-stack-e2e/summary.json` |
| 检查项 Profile 与 Effective Plan 真实全栈 | 真实 BFF/API/PostgreSQL 完成 Profile 保存刷新、影响预览、激活、disabled→active 设备绑定和设备 Effective Plan；计划冻结精确 Profile/检查项/命令/Runtime Manifest，生产 Active Bundle 前后均为 0 | `.tmp/check-item-profile-full-stack-e2e/summary.json` |
| 异步报告真实全栈 | BFF/API/Celery/PostgreSQL/Redis/MinIO 首次成功、同键重放、MinIO 故障后失败保旧、新键重试成功；请求账本依次为 `completed/failed/completed`，测试数据与对象全部清理 | `.tmp/patrol-report-async-full-stack-e2e/summary.json` |
| 报告与原始输出同源下载真实全栈 | 真实 `display version` 形成 12 条 Parser 记录和 2 条 canonical Observation；五类 BFF 下载字节逐项等于 Manifest SHA-256 与 MinIO 对象。原始输出按精确 Manifest 下载，operator 403、admin 200，源 `.txt` 字节一致，仅含四份 TXT 清单且排除 `.collection.json`；证据包无 raw CLI，测试数据、对象和临时账号全部清理 | `.tmp/check-item-report-download-full-stack-e2e/summary.json` |
| 迟到事实与报告版本重签真实全栈 | 真实 `lixian/Q2 display version` 形成 v1 并由独立账号签发；cutoff 后补录事实生成摘要不同的 v2，`late_fact_count=4`；v1 被取代后五类字节、MinIO 对象与原签发保持不变，v2 必须独立签发；版本列表和精确版本响应头一致，全部隔离数据、账号和 10 个对象清理 | `.tmp/report-revision-release-full-stack-e2e/summary.json` |
| 报告对象完整性故障注入 | 同一 v1/v2 真实依赖纵切在内容验收前删除 evidence 对象、替换 evidence 字节，并在内容接受后替换 Markdown 字节；对象缺失、摘要漂移和验收后漂移分别被 preview/start/release 识别，写接口均 409。恢复原始字节后内容接受、独立签发和下载继续成功，10 个对象及全部夹具清理 | `.tmp/report-revision-release-full-stack-e2e/summary.json` |
| 1000 条内容验收分页与恢复 | 真实 BFF/API/PostgreSQL/MinIO 冻结 1000 条复核事实；第 1/20 页各返回 50 条且不重叠，同一 50 条决定重放后计数仍为 50，重新登录后恢复并推进至 1000/1000。真实浏览器验证首屏、第 20 页、刷新恢复及 1280×720 弹窗无横向溢出；夹具结束后 1000 条 item、5 个对象和全部临时事实清理 | `.tmp/report-content-review-scale-full-stack-e2e/summary.json` |
| 结论权限、失败事实与实体覆盖真实全栈 | 真实 `lixian` CPU 输出构造 11 个隔离 `production_current` 批次、112 个目标对；部分未知、全部不适用、未关闭历史风险、全部健康、实体覆盖 48/40、零记录、截断、三类 Parser 失败及历史风险本批未复核分别得到受控结论；中文失败层贯穿 Markdown/DOCX/XLSX，机器码保留在 JSON/技术附表，五类制品、API、签发页与同一 v4 Manifest 决定一致，55 个对象及全部测试事实清理为 0 残留 | `.tmp/report-conclusion-permission-full-stack-e2e/summary.json` |
| 风险恢复、整改验收与正式关闭真实全栈 | 受控双周期事实经真实 BFF/FastAPI/PostgreSQL 先因 Coverage 不完整阻断恢复，补齐后依次完成技术恢复、整改完成、独立验收和正式关闭；自验收和错误“无需整改”关闭均返回 conflict，同键重放只保留 1 条 resolved 与 1 条 closed occurrence，且 occurrence 绑定当前 episode；领域回归验证关闭后新 episode 可复用同一客户端键而不覆盖旧事实。Markdown 和 11 表 Excel 同源投影，夹具清理后生产 Active 资产摘要不变 | `.tmp/check-item-risk-closure-full-stack-e2e-episode-v2/summary.json` |
| 生产 direct 禁用真实全栈 | BFF 返回 409；PatrolBatch 0→0、ExecutionPlan 0→0、WorkflowRun 10→10、巡检启动审计 14→14；`netops.patrol` 队列消息 0→0、消费者 1→1 | `.tmp/check-item-direct-launch-rejection-e2e/summary.json` |
| NTC 升级冻结/比较 | 真实 NX-OS `show interface status` 脱敏样本解析 `140` 条记录；freeze/compare 均成功，`unchanged=1`、`automatic_replacement_allowed=false` | `.tmp/ntc-upgrade-regression-e2e/baseline.json`、`.tmp/ntc-upgrade-regression-e2e/report.json` |
| 历史黄金 Parser 审批包 | 2026-07-29 的 `4` 个候选、`16` 份样本、`677` 条记录曾通过运行时复放，但未冻结候选到样本关联；2026-08-02 已失效，只作为历史工程回归，不可审批 | `.tmp/parser-candidate-approval-pack-20260729`；由 v2 审批包替代后才恢复生产审批资格 |
| 黄金运行时回放 | 2026-07-31 使用真实 `lixian/Q2-Q3` 重跑：Catalog v8 下 `3` 个场景、`10` 份样本、`3` 台设备、`128` 条 Observation、`2` 条真实 Change；CPU 2 类、接口 2 类、光功率/模块 8 类事件边界均可达，数据库前后不变 | `.tmp/check-item-golden-runtime-replay-20260731-v10` |
| Event 可达性 | 真实样本为 `0` Event；合成边界样本验证 CPU 2 类、接口 2 类、光功率/模块 8 类事件均可达 | 同上 |
| 可信 StageExecution 影子复验 | 2026-07-31 使用真实 `lixian/Q2-Q3` 重跑：`4` 场景、`7` 目标、`14` Snapshot/Projection；执行成功率和 Assessment 覆盖率均 100%，0 collision，报告对账、数据库回滚与 MinIO 清理通过；用途保持 `shadow_validation` | `.tmp/check-item-rollout-stage-lixian-e2e-20260731-v3/summary.json` |
| 无生产基线边界 | 11 个比较域均为 `not_applicable`；只证明候选绝对质量，不能证明优于旧链路 | 同上 |
| 真实历史报告 E2E | Catalog v8 下 `display version` 产生 `12` 条 Parser 记录、`2` 条标准 Observation；五类制品与独立签发通过；用途为 `offline_historical`，四维结论为风险未知、评估完整、暂无基线、历史采集时点权限；authority context 冻结独立来源类型及 Inventory/Target/Runtime 摘要，十章领导版内容、A4 两页视觉 QA、Excel 十表视觉 QA 和事务回滚通过 | `.tmp/check-item-lixian-e2e/20260730-report-visual-qa-v6/summary.json` |
| 可信结论 | 结论代码为 `historical_result`，`assessment_pattern_code=informational_only`，`current_state_authoritative=false`；正式签发没有把历史证据提升为当前健康。authority v2 的来源类型由 `patrol_mode + target_type` 独立派生，不再由用途反推 | 同上 |
| 报告交付物 | 五类交付物完成渲染、签名与事务内发布；测试结束后数据库回滚 | 同上 |
| 领导版 DOCX 视觉 QA | 本机 Office/WPS 排版引擎导出 2 页 A4 PDF，并逐页检查 PNG；无大面积空白、表格截断、文字重叠和后台英文状态，页码与重复表头正常 | `.tmp/check-item-lixian-e2e/20260730-report-visual-qa-v6/word-render/pages` |
| 技术附表 XLSX 视觉 QA | 既有真实 `lixian` 十表工作簿和新增风险生命周期十一表工作簿均由 `artifact-tool` 逐表渲染；十一表关键区值正确、公式错误 0，技术恢复、整改验收和正式关闭列无截断或错位 | `.tmp/check-item-risk-closure-visual-qa-v1` |
| 生产隔离 | 全局原有 Active Parser/Contract/Policy/Rule 数量前后不变；Active RuntimeBundle 前后均为 `0`，没有绕过人工审批激活目标资产 | 同上 |
| 当前生产前置条件核验 | Parser Template 为 Active `3`、Pending Approval `4`、Rejected `18`；CandidateSampleEvidence `19`；Active RuntimeBundle `0`。四个待审批候选均未获人工复核/发布，Inventory 仅有文档地址 `192.0.2.40` 的工程夹具，不是现场目标 | PostgreSQL 只读聚合，2026-08-02 |

黄金运行时回放中，真实样本事件数为 `0` 是可信结果，不代表事件链不可达。事件规则的触发与恢复由合成边界样本单独验证；平台不得为了展示链路而伪造生产事件。

### 治理与人工验收

| 验证项 | 结果 |
|---|---|
| 文档治理 | `75` 份受治理文档；active `37`、superseded `20`、archived `13`；未登记 `0`、坏链 `0`、索引状态违规 `0` |
| 废弃项治理 | candidates `5`、observing `1`、read_only `2`、write_disabled `2`；当前无候选满足全部物理删除门禁 |
| 浏览器人工验收 | 既有生产工作台、规则来源任务、资产列表、RuntimeBundle 装配、中文检查项目录及桌面/移动布局已经过浏览器验收；缺口与阻断项保持唯一下一步且失败关闭。2026-08-02 新增的 AssessmentPolicy 全目标影响预览、Inventory 选择器、巡检 Basis 下钻、历史影子和目标级切换/回滚尚需单独完成真实浏览器复验，不能沿用旧页面验收结论 |

### 发布结论

平台主骨架已成立，但工程验收和生产资产投产均未全部完成，当前不能标记为“平台完成”或
“已完成生产发布”：

1. 四个当前黄金 Parser 已统一形成 v2 审批包；旧四候选不得恢复审批。审批材料必须同时查看字段角色、
   目标标准观测与能力边界，不得把 Parser review status 当作 check-item runtime readiness。
2. 工程师按实际输出范围人工复核，并由不同的独立发布人审批新候选；MSR absent-only 模板可以作为窄
   Parser 资产发布，但不能满足完整光功率目标。随后冻结首批目标设备、型号和检查项矩阵。
3. 从已审批 Parser 重新生成并审批对应 Contract/CurrentStateRule/ChangePolicy/EventRule/RuntimeBundle，再使用已审批版本复验现有 `lixian` Q2/Q3
   影子证据；事务内候选演练不能替代资产审批。
4. 使用已实现的 `live_capture` 完成真实浏览器、API、Workflow/Worker、PostgreSQL、Redis/Celery、
   MinIO 和下载回读闭环，并验证断连、超时、重投递和采集中取消。
5. 使用 Active RuntimeBundle 执行至少一个新鲜当前周期真实批次，生成用途为 `production_current` 的
   诚实报告；关键检查项无法判断时不得声称整体健康。
6. 使用已实现的 legacy baseline Registry 冻结现场实际旧路径；若旧路径不是 Active RuntimeBundle，
   新增受治理 adapter 后再按灰度范围运行两个真实巡检周期，核对 Change、Event、风险和报告证据链。
7. 达到退出指标后，再删除命令中心兼容读和废弃代码。

其中人工审批、目标范围冻结和正式签发是有意保留的生产安全边界；真实全栈闭环和生产灰度周期
仍是明确的工程缺口，不能用“等待人工”或已经存在的控制器页面掩盖。当前环境还没有任何
`NetworkDevice`，因此真实在线采集不是尚未调用一个按钮，而是缺少合法目标和凭据；不得用
`lixian` 历史样本或合成设备冒充现场 `live_capture`。
