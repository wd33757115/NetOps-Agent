# 规则资产控制面与领导版巡检报告改造方案

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-command-adapter-simplification-remediation-2026-08-10.md)。

## 1. 项目目标

本次改造不以“增加若干页面”或“补几条规则”为目标，而是建立两条可证明闭环：

```text
脱敏结构化样本
  -> LLM 候选生成
  -> 确定性校验反馈
  -> 有界 Agent Loop 修订
  -> 历史回放与碰撞检查
  -> Contract / Policy / Rule 整包审批
  -> 单事务原子发布

巡检范围与执行事实
  -> 当前状态规则评估
  -> 周期变化与事件
  -> 数据可信度和资产覆盖度
  -> 风险分级与处置建议
  -> Markdown 技术报告 + DOCX 领导报告
```

最终交付必须满足：

- ObservationContract、ChangePolicy、EventRule 作为同一业务语义的资产包治理，不能出现半套资产上线。
- LLM 只生成候选；验证器、回放、审批和运行时均为确定性代码。
- 自动生成链路默认使用 LLM Agent Loop。手工内置规则 Provider 仅用于应急、兼容和测试，必须显式选择，不能静默降级。
- 报告能够说明“检查了什么、成功检查了多少、发现了什么、哪些结论暂时不能下、下一步做什么”。
- “无 Change/Event”不等于健康；首次巡检已经存在的异常必须通过当前状态评估识别。

## 2. 方案讨论与决策

### 2.1 规则资产发布粒度

| 方案 | 优点 | 主要问题 | 决策 |
|---|---|---|---|
| 三类资产独立 Job、独立审批 | 实现简单 | 可出现 Contract 为 draft、Policy 已 active 的不一致状态 | 否决 |
| 生成后直接覆盖 active | 操作快 | 无法审计、回放、回滚，LLM 错误直接进入生产 | 否决 |
| RuleAssetBundle 整包治理 | 语义一致、可回放、可原子发布 | 需要 Bundle 门禁和兼容旧 Job | 采用 |

Bundle 使用现有 RuleGenerationJob 存储，`job_type=RULE_ASSET_BUNDLE`，子 Job 仍保留以兼容既有审计和专家入口。正常操作只审批 Bundle；Bundle 子 Job 禁止单独激活。

### 2.2 生成方式

| 方案 | 主要问题 | 决策 |
|---|---|---|
| 按命令、厂商硬编码规则 | 新型号需要持续改业务代码，无法泛化 | 仅保留手工 Provider 接口 |
| LLM 一次生成后人工兜底 | 弱模型容易输出语义错误或不合法 DSL | 否决 |
| LLM + 确定性验证反馈的有界循环 | 可让较弱模型根据精确错误逐轮修订，失败可诊断 | 采用 |

Agent Loop 每轮只接收脱敏结构化记录、字段统计、上一轮候选和机器反馈。最大轮数受控；到达上限仍不通过时进入 blocked/rejected，不自动启用内置规则。

### 2.3 报告判定方式

报告拆分两条互补证据：

- **当前状态**：把最新 StateObservation 作为 initial fact 交给 active EventRule 评估，识别首次采集时已经存在的 down、低光功率、高利用率等风险。
- **周期变化**：使用 Change/Event 说明何时发生变化、是否恢复、是否反复。

报告结论同时包含：

- `assessment_status`：正常关注、需关注、风险、重大风险、无法判定。
- `trust_status`：充分、部分、不足。

只有范围、采集、Parser、Contract、Policy、Rule 门禁均满足时，才允许输出“本次覆盖范围内未发现异常”；任何情况下都不使用“网络整体健康”替代证据有限的结论。

## 3. 规则资产 Bundle 状态机

```text
draft
  -> generating
  -> validating
  -> replaying
  -> pending_approval
  -> active

任一门禁失败 -> rejected/blocked
active -> deprecated
```

在现有状态字段兼容范围内，阶段细节记录在 Bundle 的 validation report：

- `context_gate`：不可变 context_id/digest、隐私检查、样本覆盖。
- `contract_gate`：主键稳定、必填字段、值类型、实体覆盖。
- `policy_gate`：语义类型与 compare mode 一致、阈值和恢复阈值合法。
- `rule_gate`：DSL 合法、故障/恢复成对、初始状态行为明确。
- `replay_gate`：样本数、命中数、碰撞数、预估日事件量。
- `release_gate`：三类资产齐全、全部校验通过、`collision_count=0`。

审批动作在同一数据库事务内：锁定 Bundle 和全部子 Job，废弃对应旧 active 版本，激活新 Contract、Policy、Rules，最后更新全部 Job。任何一步失败均整体回滚。

## 4. LLM Agent Loop 契约

### 4.1 输入边界

允许：

- approved field whitelist 内的 structured records。
- 假名化设备维度、数值分布、枚举分布和历史统计。
- Parser Contract seed、policy intent、event intent。
- 上一轮候选及确定性 validator feedback。

禁止：

- raw CLI、密码、token、SNMP community。
- 未经批准字段、真实设备名、真实管理 IP。
- 让 LLM 自行批准或修改 active 资产。

### 4.2 输出边界

- ObservationContract 只能引用样本中存在的字段，必须声明稳定主键和字段语义。
- ChangePolicy 只能使用平台支持的 compare mode，阈值必须有样本统计或显式 policy intent 依据。
- EventRule 只能使用版本化 DSL；不得生成 Python、正则执行代码或任意表达式。
- 每轮输出均通过 Pydantic schema 和现有确定性 validator。

## 5. 控制面 UI

默认视图按 Observation Type 展示业务资产链，不要求用户在三个独立表格之间比对：

```text
数据来源 -> Contract -> Change Policy -> Event Rules -> 回放 -> 审批发布
```

每行展示：

- 当前 active 版本与候选 Bundle。
- 缺失层、阻断原因和唯一“下一步”操作。
- 样本周期/记录数、字段覆盖、回放命中、碰撞和预估事件量。
- “生成/补齐资产”从 Parser 激活种子和不可变数据集上下文启动。
- “整包审批”只在全部门禁通过时可用。

原有 Contract、Policy、Rule、Agent Loop、审批审计表继续作为专家视图保留。

## 6. 领导版报告数据契约

报告事实模型包含：

1. 巡检范围：目标、发起、跳过、完成、失败、取消、覆盖率。
2. 数据可信度：命令成功率、Parser ready 率、结构化观测数、规则资产覆盖。
3. 当前网络现状：按设备和领域汇总当前异常，列明当前值、阈值和命中规则。
4. 周期变化：Change、Event、恢复事件、重复事件及趋势。
5. 风险排序：critical/major/warning/minor/info，给出影响对象和证据。
6. 整改建议：P0/P1/P2、建议责任角色、完成时限和复核方式。
7. 结论限制：未采集设备、解析失败、缺少规则和不适用项。
8. 证据索引：批次、Workflow、Snapshot、Contract、Policy、Rule 标识。

交付物：

- `report.docx`：面向甲方领导，采用项目公文写作 Skill 的 A4、标题、正文和签署格式。
- `report.md`：面向技术人员，保留完整质量、变化和证据明细。
- `report.json`：结构化事实，供二次集成和一致性校验。
- 原始输出 ZIP、事件 CSV 保持独立下载。

## 7. 验收门禁

- Bundle 缺少任一必需资产、任一验证失败、隐私检查失败或回放碰撞大于 0 时不可审批。
- Bundle 审批失败后，数据库中不能出现任何新 active 子资产。
- 运行时只读取 active 版本；手工 Provider 的资产同样必须走验证和审批。
- running/cancelled/failed/not_started 批次不能输出“巡检完成且未发现异常”。
- 首次巡检的 initial down/低光功率能够进入当前状态风险清单。
- Parser/规则覆盖不足时报告必须降低可信度并列明缺口。
- `lixian/Q1`、`Q2`、`Q3` 文件数、设备数和命令数符合固定基线；接口和光衰模板跨三个周期全样本通过。
- 生成的 DOCX 能打开，包含总体结论、范围、风险、领域现状、建议和结论限制。

## 8. 分阶段落地

1. 新增通用 LLM Rule Generation Agent、结构化输出和有界反馈循环。
2. 将现有 Workflow 切换为显式 LLM Provider，并保留 builtin/manual Provider。
3. 新增 RuleAssetBundle 持久化、门禁、预览和原子审批 API。
4. 将资产链 UI 改为引导式 Bundle 流程，保留专家视图。
5. 新增当前状态评估和领导报告事实模型，提供 JSON/Markdown/DOCX。
6. 使用 `lixian` 三周期完成 Parser、规则回放、报告生成和浏览器验收。

## 9. 实施结果（2026-07-14）

### 9.1 规则资产控制面

已完成以下生产路径：

```text
Parser active
  -> 不可变脱敏数据上下文
  -> LLM Agent Loop 生成 ObservationContract
  -> LLM Agent Loop 生成 ChangePolicy
  -> LLM Agent Loop 生成 EventRule
  -> 确定性校验
  -> 历史回放与碰撞检查
  -> RuleAssetBundle 待审批
  -> 单事务原子发布
  -> 运行时仅解析 active 版本
```

- Agent Loop 最多执行受控轮次，每轮把 schema、语义和回放错误反馈给模型；到达上限仍失败时保留证据并阻断，不静默切换为内置规则。
- 数据上下文只从已解析的 `structured_json` 构建，执行字段白名单、真实设备身份假名化、敏感字段清除、跨周期分层采样，并以 `context_id + digest` 固化。
- Bundle 子资产禁止单独审批；Bundle 审批会锁定全部子资产，并在同一事务中完成旧版本废弃和新版本激活。发布失败时不产生半套 active 资产。
- 手工 Provider/Registry 继续保留，适用于应急、兼容和确定性测试；默认自动生成路径仍为 LLM。
- 控制面默认展示“数据上下文 -> 观测契约 -> 变化策略 -> 事件规则 -> 历史回放 -> 审批发布”，并展示阻断原因、候选资产版本、回放命中、碰撞和预估事件量；旧单资产任务保留为专家兼容入口。

### 9.2 通用观测适用性治理

光功率数据暴露出一个不能靠阈值本身解决的问题：部分设备会用 `-40 dBm`、零偏置电流或空阈值表示“不适用/无有效 DOM 测量”。如果直接执行低阈值规则，会把铜口、无模块或未运行端口误报成严重光衰。

本次没有按厂商或型号写业务分支，而是新增版本化的通用 applicability 谓词 DSL：

- 语义目录为观测字段声明适用性条件，LLM 不得覆盖受治理条件。
- ObservationContract 生成器编译并验证条件引用的字段。
- State Normalizer 只携带适用性所需的最小上下文，不复制描述或原始输出。
- Change Detector 把该上下文传递到 EventRule 运行时。
- EventRule 编译器将受治理条件附加到候选规则；synthetic validation 自动生成“不适用”反例。
- DSL 仅支持白名单布尔/比较操作，不执行 Python、动态表达式或模型生成代码。

该设计可由语义资产扩展到其他厂商和指标，不要求每增加一个型号就修改业务代码。

### 9.3 报告事实与风险关联

领导版报告已实现 JSON、Markdown 和 DOCX 三种同源输出，并使用项目公文格式组件生成 A4 文档。正文只保留领导决策所需内容，技术 ID 和完整证据留在 JSON/附件中。

风险计算增加跨域关联：同一物理接口已经处于 Down 时，其低光功率只作为故障定位证据，不再重复计为独立重大风险；接口别名通过通用规范化进行关联。报告明确区分：

- 主风险：需要独立定级和处置的当前故障。
- 关联证据：支持主故障定位、但不重复计数的测量信号。
- 数据限制：缺模板、解析失败、未覆盖域和规则缺口。

### 9.4 `lixian` 实测结果

固定原始数据基线已核验：Q1 为 2 文件、2 设备、54 命令；Q2 为 2/2/44；Q3 为 2/2/54。使用真实 DeepSeek Provider 构建不可变脱敏上下文并完成规则生成、确定性校验、Q1 -> Q2 / Q2 -> Q3 回放和报告生成。最终冻结版本如下：

| 指标 | 实测值 |
|---|---:|
| Active RuleAssetBundle | 1 套（1 Contract / 6 Policies / 22 EventRules） |
| 脱敏生成上下文 | 354 条 records / 6 个 Snapshot / 3 个 Patrol Run |
| 上下文隐私结果 | raw CLI 0；真实设备名/IP 0；隐私门禁通过 |
| 历史回放 | 命中 6 / 样本 548 / 碰撞 0 |
| Q1 -> Q2 | 3008 Observation / 81 Change / 4 Event |
| Q2 -> Q3 | 3140 Observation / 465 Change / 26 Event |
| Q2 -> Q3 当前主风险 | 2 项 InterfaceDown（重大） |
| Q2 -> Q3 关联光功率证据 | 4 项，不重复计数 |
| Q2 -> Q3 状态改善 | 20 项；关闭已跟踪故障 2 项 |
| 三周期趋势 | 新增证据 4 / 持续 2 / 已恢复 4 |
| Q2 -> Q3 Parser Ready | 22/26（84.6%），缺失模板 4，解析失败 0 |

原始样本中的无效 `-40 dBm` 哨兵值已被适用性门禁排除；Down 接口上的 `-36.96 dBm` 被归入关联证据。最终结论为“发现重大网络风险、可信度中”，没有把两台样本设备的结论扩大为全网结论。可信度未评为高的原因是当前周期仍有 4 条结构化命令缺少 Parser 模板；未覆盖领域不能据此判定健康。

### 9.5 当前发布判断

规则生成、Bundle 治理、运行时适用性、风险关联和领导报告主链路已经具备可验收条件。当前输出适合作为两台样本设备的阶段性巡检报告和平台能力验收材料，不应直接表述为全网健康证明。生产推广前仍需完成剩余 Parser 缺口、真实设备用途确认、权限审计和更大样本量的历史回放。

### 9.6 运行态与交付验收

- FastAPI、Django BFF 和 React 生产预览已按同一版本启动并完成真实登录态验收；规则资产 Bundle、Parser 概览和离线巡检接口均能正常返回。
- Skill 启动阶段默认只同步元数据，不加载本地 BGE/Transformers 语义模型；本地语义路由改为显式开关，FastAPI 常驻内存从约 502 MB 降至约 204 MB，同时保留外部 RAG 和显式重建索引能力。
- Parser 治理概览修复了数据库会话提交后访问 ORM 对象造成的 500；聚合统计在对象仍附着于会话时完成，并有回归测试覆盖。
- Bundle 列表不再只信任历史 validation report，而是按当前子 Job、资产组成、子资产状态、脱敏上下文和回放结果重新计算 `release_ready`。历史子资产缺失时，UI 明确提示重新生成整套资产并禁用审批发布；最终审批事务仍执行同一套服务端强校验。
- 控制面治理告警和阻断原因已统一为中文运维语义，旧版本不同英文原因映射后去重；专家仍可查看原始 Job 和验证证据。
- 桌面端和 390 x 844 移动端真实浏览器验收均无页面级横向溢出；Bundle 宽表在自身容器内滚动，筛选、分页和审批状态可见。
- 回归结果：完整单元测试 783 项通过；扩展 Agent/可观测/Skill System 测试 103 项通过、1 项跳过；BFF 路由 4 项通过；前端 34 项、UTF-8 扫描、ESLint 和 Vite 生产构建通过。
- 最终 DOCX 为 A4 纵向、7 页，页边距符合项目公文格式，4 个表格均启用跨页重复表头，无占位符，七个正文部分齐全。由于本机未安装 LibreOffice，使用本地 Gotenberg 转为 PDF，并逐页渲染为 PNG 完成视觉检查；无截断、重叠、缺字或表格破版。

### 9.7 最终验收标识与不可变交付物

权威规则资产与分析版本：

| 对象 | 标识 |
|---|---|
| Active RuleAssetBundle | `2648aa99-25d8-4725-9623-4841be1ef692` |
| 不可变生成上下文 | `rule-context-af699e54fae142658b84` |
| 上下文摘要 | `b84fbd99340f7e7cc0396babe5fa8af73932fce43ac4af4d57713674aab5b125` |
| Q1 -> Q2 Analysis Attempt | `52a68e9c-a0ea-4c29-b3c1-a00d7b2aea4e` |
| Q1 -> Q2 Analysis Revision | `goal-acceptance:2648aa99:q1-q2` |
| Q2 -> Q3 Analysis Attempt | `368c0008-e25a-4a41-97b3-19a901f36569` |
| Q2 -> Q3 Analysis Revision | `goal-acceptance:2648aa99:q2-q3` |

交付目录：`.runtime/deliverables/q1-q2-q3-leadership-report/`。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `network-patrol-leadership-report.docx` | 43442 | `c500f0cfba1b137800df939f11505e35296be77a52142468df35f3b922af057f` |
| `network-patrol-leadership-report.json` | 49619 | `dd53570d93a91266a2e1d5bbbae067d1c957e9e239b3c4dca1a0a007485c8e48` |
| `network-patrol-leadership-report.md` | 5039 | `67efda3bb1a29ec87cb7aedb1c57472819776140d98d239b886b1a7f790412c9` |
| `network-patrol-leadership-report-rendered.pdf` | 205924 | `7120dafb6322bfe60e5e8a1fe772d7bf575a6d03e87a3b3e9fc4ea940fafabc6` |

报告正文的统计时间固定为权威 Analysis Attempt 的 `completed_at`，OOXML ZIP 条目时间也被归一化。同一分析版本经 UI 连续下载两次，得到相同的 43442 字节和相同 SHA-256 `c500f0cf...057f`，证明报告不会因下载时刻发生漂移。通用导出入口为：

```powershell
venv\Scripts\python.exe scripts\prod\export_offline_patrol_report.py `
  --job-id offline-143232f737454b29b3f5 `
  --analysis-attempt-id 368c0008-e25a-4a41-97b3-19a901f36569 `
  --output-dir .runtime\deliverables\q1-q2-q3-leadership-report
```

浏览器验收覆盖离线任务列表、Q1 -> Q2 与 Q2 -> Q3 结果、完整事件证据链、规则资产包深链接、隐私/回放/碰撞门禁和报告下载，前端控制台无错误或警告。

### 9.8 生产推广前的明确限制

- 当前结论仅覆盖 `lixian` 中两台样本设备和已识别命令，不是全网健康证明。
- Q2 -> Q3 Parser Ready 为 84.6%，剩余 4 条缺模板；消除缺口前报告可信度保持“中”。
- 当前仍有 33 个 Active Parser 模板待完成 `textfsm-2.1.0-platform-v3` 方言治理；运行时阻断为 0，但替换候选仍须逐一回放和审批。
- 生产推广还需要真实设备用途确认、租户/角色权限审计、备份恢复演练，以及更大设备量和更长时间窗的回放与容量测试。
