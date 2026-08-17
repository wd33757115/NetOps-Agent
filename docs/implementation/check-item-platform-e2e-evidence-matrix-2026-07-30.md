# 检查项平台 42 项 E2E 证据矩阵

> - 对应总体方案：`docs/check-item-centered-patrol-platform-remediation-2026-07-27.md`
> - 审计日期：2026-08-08
> - 审计范围：总体方案 20.4 的 35 条原始浏览器场景、离线 CurrentAuthority、显式内容验收，以及
>   8.19 新增的策略来源、实现/基线选择、取消终态、风险去重和生产反馈五项场景
> - 目的：区分领域测试、组件 Mock、真实依赖集成、预发布现场验证和人工签发，防止局部测试冒充产品完成

## 1. 证据层级

| 代码 | 层级 | 可证明内容 | 不可替代内容 |
|---|---|---|---|
| `U` | 单元/领域/合同测试 | 状态机、不可变式、失败语义、确定性算法 | HTTP、Worker、数据库和对象存储真实协同 |
| `CM` | 组件 Mock E2E | 页面交互、文案、禁用态和 API 故障展示 | 后端落库、任务消费、资产激活和制品下载 |
| `I` | 集成 E2E | 浏览器或真实 HTTP、PostgreSQL、Redis/Celery、MinIO 的协同结果 | 现场设备、真实凭据和生产网络结论 |
| `P` | 预发布/生产影子 | 真实设备、真实模型或真实生产候选的可运营性 | 人工审批、报告签发和甲方验收 |
| `H` | 人工门禁 | Parser/规则/运行包审批、业务语义确认、甲方内容验收与独立签发 | 自动化回归和系统幂等性 |

判定规则：

1. 除场景 16 外，组件 Mock 不能作为主通过证据。
2. 场景 1 至 15、17 至 31、33 至 35，以及场景 36、37 的拒绝、幂等、职责分离和漂移分支，均可在无现场设备时完成真实依赖集成，但
   PostgreSQL、Redis/Celery、MinIO 和 HTTP API 必须真实运行。
3. 场景 36 可以用显式 `production_network_claim=false` 的合成新鲜采集证明 `I` 层系统路径；场景 32
   和 36 的 `P/H` 层 `production_current` 网络结论必须使用新鲜、真实、经授权的当前设备批次。
4. `lixian` 只可证明历史、影子、Parser 或规则回放，不得声明当前网络健康。
5. `done` 只表示该场景所要求的主证据层已经通过，不表示整个平台或生产投产完成。
6. 历史 E2E 中出现的 Candidate ID、样本数和解析记录数只证明当次隔离执行；候选若没有当前证据协议的
   精确样本关联，不得继续作为生产待审批资产。2026-08-02 已按此规则拒绝旧四候选。
7. 场景 37 的自动化只能证明内容验收机制和失败关闭语义；真实内容接受与最终签发必须由不同的真实职责主体完成。
8. 场景 38 与场景 39 已在 `I` 层完成历史样本真实依赖工程纵切；场景 39 仍等待真实 Active 资产的
   新鲜生产双周期与甲方报告验收。场景 42 已完成领域/API/组件工程链，但真实预发布反馈、候选回放、
   阈值暂停和 Bundle 回滚仍待 `I/P` 证据；场景 40 保持 `in_progress`，场景 41 保持真实网络 SME 门禁。
   设计合同、相邻测试或组件 Mock 不得使未完成证据层变为 `done`。
9. 场景 38-41 的对应证据层是首个甲方 `production_current` 内容验收的前置门禁；场景 42 是超过 20% 灰度的
   前置门禁。
10. 采集实现双路由和目录/RuntimeBundle 原子切换已具备 `U/CM` 工程证据；它只证明发布机制，不证明
    某个真实厂商/型号的命令、Parser 或规则资产正确。使用真实 Inventory、候选资产和 `live_capture`
    完成观察与独立审批前，证据层保持 `P/H pending`。

产品目标与场景映射：

| 目标 ID | 主要场景 | 当前缺口 |
|---|---|---|
| `G1_SCOPE` | 8、11、12、17、31、35、36 | 真实甲方目标矩阵尚未冻结 |
| `G2_EXECUTION` | 9、10、11、13、22、25、30、36 | 场景 9、10、11 已完成真实 Workflow 批次；现场生产设备与正式目标矩阵仍由场景 32、36 和投产门禁验收 |
| `G3_CURRENT_RISK` | 9、11、22、25、27、32、35、36 | 新鲜真实 `production_current` 批次受环境和资产阻断 |
| `G4_CHANGE_RECOVERY` | 10、23、27 | 场景 10 的真实依赖双周期恢复纵切已完成；真实 Active RuntimeBundle 与新鲜生产双周期仍未完成 |
| `G5_REMEDIATION` | 10、27、28 | 整改完成、独立验收和正式关闭的工程闭环已完成；真实责任人与甲方签发仍是人工门禁 |
| `G6_TRACEABILITY` | 12、23、28、29、34、35、36、37 | 显式内容验收已完成真实依赖 v1/v2、五类对象字节级故障注入、1000 条服务端分页与浏览器恢复纵切；甲方真实内容验收和独立签发仍未完成 |
| `G7_SCALE` | 17、31、32 | 容量已验证，真实多厂商灰度与两个生产周期未完成 |
| `G8_REPORT_SCHEMA` | 12、17、26、34、35、37、41 | 跨厂商投影与同源制品已验证；场景 41 的领导问题数、RiskEpisode、影响实体与事件次数已完成工程同源验证，真实甲方内容复核仍待生产 current |
| `G9_RISK_TRUTH` | 10、27、35、38、39、41、42 | 生命周期、显式跨检查项关联和不可变生产反馈工程主链已具备；真实网络 SME 关联策略、场景 40 故障矩阵及生产反馈的预发布回放/回滚证据仍待关闭 |

## 2. 场景矩阵

| # | 场景 | 主证据层 | 当前最可信证据 | 当前判定 | 唯一剩余门禁 |
|---:|---|---|---|---|---|
| 1 | 规则页导入离线样本 | `I` | 真实 BFF -> FastAPI -> MinIO 直传/回读 -> Celery 预览 -> Workflow -> PostgreSQL：`lixian/Q2` 规则来源任务从原任务恢复上传，识别为 1 文件、1 设备、27 命令；浏览器明确将未匹配身份确认为外部设备后启动处理，真实 Worker 完成导入并将流水线推进到 Parser 缺口治理。既有 `display version` 专用纵切另验证 12 条结构化记录、Active Parser 复用、不重复创建 Parser Job 且不调用 LLM | `done / verified_full_stack / verified_browser_worker` | 无；本场景只验证离线来源导入，后续规则生成与人工审批分别由场景 3 至 7 验收 |
| 2 | 检查项与兼容实现归组 | `I` | 真实来源经 BFF/API 先形成物理命令实现，再由检查项目录和 `AssetRequirementProfile` 投影为业务检查项。真实浏览器在同一 `lixian/Q2` 任务内显示 20 个检查项聚合和 4 个未归类阻断单元；`device.version` 只显示 Identity、Parser、Contract、RuntimeBundle，Policy/Event 不适用且不展示，展开后可见 `display version` 兼容实现。来源详情、分页 API 与 PostgreSQL 投影仍逐字段一致，vendor/model/search 与边界已验证 | `done / verified_full_stack / verified_browser` | 无；新的输出签名仍须按兼容性治理进入同一实现修订，未归类输出不得进入运行包，也不会自动扩大兼容范围或发布 |
| 3 | NTC 复用或生成 Parser 并查看客观结果 | `I`；真实模型另做 `P` | 真实 `lixian display device manuinfo` 经 BFF -> FastAPI -> MinIO -> Celery -> Workflow -> PostgreSQL -> Parser Job，命中 NTC 模板并由 DeepSeek 初审；审批比较使用生产 ParserRuntime 解析 `8/8` 条记录。S6850 四样本另经独立 AI Worker 生成五 record-set ParserBundle，生产 Runtime 全样本执行 508 条记录并形成 v2 审批包。两类候选均保持 `pending_approval`，未自动激活 | `done / verified_full_stack / verified_parser_bundle` | 无工程生成阻断；人工发布属于场景 5，不由本场景替代 |
| 4 | LLM 初审和人工直审 | `I`；真实模型另做 `P` | 场景 3 已通过真实 DeepSeek 初审；隔离候选经真实 BFF/API/PostgreSQL 保存人工直审，候选仍为 `pending_approval`，复核摘要与审计落库；审批抽屉组件验证两阶段页面 | `done / verified_full_stack` | 无；真实生产候选的业务语义决定属于场景 5 的人工门禁 |
| 5 | 审批 Parser | `I + H` | 隔离候选使用两个临时认证账号验证复核前发布拒绝、同人发布拒绝、独立发布 -> Active -> Active Contract -> Outbox -> Resolver，并形成复核/激活两条审计；测试数据全部清理。8.2 另验证候选必须冻结精确样本关联，旧可变候选会 fail closed；S6850 v2 审批包已展示 4 份样本、五个模板、508 条实际解析记录及独立 Reviewer 结论 | `integration_done / ParserBundle_ready_for_human_review / blocked_by_human_gate` | 由真实工程师确认候选语义，并由不同发布人批准；不得用隔离夹具或事务回放替代人工决定 |
| 6 | 生成并审批规则资产 | `I + H` | 隔离离线 CPU 双周期经真实 BFF/API/Celery/七步 Workflow/PostgreSQL 完成审批职责分离。S6850 候选另以 4 组/508 条 sanitized records dry-run 生成 presence、Rx、Tx 三个 Policy 和 6 个 EventRule；设备只有 Alarm 阈值时只生成 Critical/Recovered，不虚构 Warning；raw CLI 为 0 | `generation_and_validation_done / blocked_by_parser_and_rule_human_gates` | Parser 激活后用不可变 context_id 重新形成正式规则候选，再由不同工程师完成复核与生产审批；dry-run 不得直接发布 |
| 7 | 查看并激活 RuntimeBundle | `I + H` | 隔离纵切完成 RuntimeBundle preview、幂等 draft、创建人自激活拒绝、独立激活和 Active Resolver 冻结角色读取。S6850 候选事务回放又形成 524 Observation、46 Change、4 周期事件，0 collision；外层事务回滚后生产 Active 资产摘要完全不变 | `integration_done / candidate_runtime_replay_done / browser_interaction_pending / blocked_by_human_gate` | 在生产工作台由真实审批人复核并激活真实目标资产；补一次页面级完整操作证据 |
| 8 | 创建检查项 Profile | `I` | 隔离 `configuration.running` RuntimeBundle 经真实 BFF/API/PostgreSQL 完成 Profile 创建、刷新、影响预览、激活、设备绑定启用和设备 Effective Plan；返回精确 Profile 版本、检查项、采集命令及不可变 Runtime Manifest 摘要，测试后全部夹具清理 | `done / verified_full_stack` | 无；真实生产 Profile 和目标设备仍须由业务人员选择，不由测试夹具代替 |
| 9 | 首次巡检与无基线说明 | `I` | 隔离双设备批次经真实 BFF/API/Celery/Workflow/PostgreSQL/Redis/MinIO 和 SSH 采集完成；成功设备首观测 CPU 91% 形成 1 Finding、1 RiskCase 和 `at_risk` Assessment，`comparison_status=first_observation`，Change/Event 均为 0；五类报告制品由同一 Manifest 物化并校验摘要 | `done / verified_full_stack` | 无工程集成门禁；真实生产设备、Active 业务资产和当前网络结论仍属于场景 32 与人工投产门禁 |
| 10 | 下一周期 Change/Event/Risk 恢复整改 | `I` | 受控双周期事实经真实 BFF/FastAPI/PostgreSQL 先因 Coverage 不完整阻断恢复，补齐后依次完成服务端候选、技术恢复、整改完成、独立验收和正式关闭；同人自验收与错误“无需整改”关闭均 fail closed，幂等重放、episode 绑定、关闭后复发隔离、审计、风险页合同、Markdown 和十一表 Excel 投影一致 | `done / verified_full_stack` | 无工程集成门禁；真实 Active RuntimeBundle、新鲜生产双周期、真实责任人和甲方签发属于投产门禁 |
| 11 | 部分设备失败不包装健康 | `I` | 与场景 9 共用同一真实批次：成功设备为 `at_risk`，连接失败设备形成 `unable_to_determine`；目标矩阵保持 2/2，无缺行；JSON、Markdown、DOCX、XLSX 和证据包下载字节与同一 Manifest SHA-256 一致 | `done / verified_full_stack` | 无工程集成门禁；现场凭据、网络分区和甲方签发仍按生产门禁执行 |
| 12 | 同一 Manifest 的报告、Excel、证据包 | `I` | 真实 BFF -> FastAPI -> PostgreSQL -> Celery/RabbitMQ -> Redis -> MinIO：Markdown/DOCX/XLSX/JSON/证据包下载字节分别与 Manifest SHA-256 和 MinIO 对象一致；未签发状态与来源摘要响应头完整 | `done / verified_full_stack` | 无；人工正式签发属于场景 28，不由本场景替代 |
| 13 | Workflow 与 direct 终态一致，或 direct 禁用 | `I` | 生产 direct 在任何预览、数据库写入或队列投递前拒绝；真实 BFF 返回 409，数据库四类计数及 `netops.patrol` 队列前后不变 | `done / verified_full_stack` | 无；非生产 `parser_validation` 诊断适配器继续保留 |
| 14 | Parser 失败后修 Spec、重调度、旧候选禁发 | `I` | 真实 `lixian/Q2/SW1 display device manuinfo` 经 BFF/API/MinIO/Celery/Workflow/PostgreSQL 完成不可满足 R1、R2/R3 修订和重新调度；R1 精确写入 `contract_conflict`，旧候选变为 `rejected` 且激活返回 409，最终候选由生产 ParserRuntime 解析 8 条记录后保持 `pending_approval`，人工语义复核仍为唯一 blocker。范围化清理后存活 Job/候选均为 0；后端组合 `114 passed`，`ParserGenerationStep` `8 passed` | `done / verified_full_stack / verified_worker` | 无工程集成门禁；真实生产候选仍须人工语义复核与独立发布，本场景没有把组件回归表述为新增浏览器点击证据 |
| 15 | 单样本候选及证据局限 | `I` | 隔离 Parser 候选经真实 BFF/API/PostgreSQL 保存 1 份样本并由生产 ParserRuntime 解析 2 条记录；审批比较页明确显示“1 份生成样本”，要求逐项确认最终解析结果和主键，复核前及同人发布均被拒绝 | `done / verified_full_stack` | 无；单样本只证明该输出变体可形成候选，不得自动扩大为厂商或型号级兼容，也不得绕过独立人工发布 |
| 16 | RuntimeBundle 健康接口失败时失败关闭 | `CM` | `ProductWorkspaces.test.tsx` 明确不把不可用显示为就绪 | `verified_component_mock` | 保持组件回归；无需伪造后端故障为生产 E2E |
| 17 | 控制面、数据库、Resolver 计数一致及分页 | `I` | 真实 BFF/API/PostgreSQL 对 Active Catalog 的 20 个检查项、39 个实现对做六页遍历；页大小为 7，39 个稳定键无遗漏、无重复；状态筛选、搜索、越界空页、数据库 Active Bundle 和 Resolver 三方一致，当前均如实为 0 ready / 39 missing | `done / verified_full_stack` | 无；生产资产为 0 是当前真实状态，不由测试伪造激活 |
| 18 | 子资产已发布但 Bundle 缺失显示待装配 | `I` | 隔离 `device.version` 实现具有 1 个 Parser 与 1 个 Contract 候选，但没有 RuntimeBundle；真实 API/数据库仍判定未就绪，控制面显示缺口并禁止把子资产数量冒充运行就绪，测试后无资产残留 | `done / verified_full_stack` | 无；真实生产子资产仍按场景 5 至 7 的人工门禁发布 |
| 19 | 运行包预检 blocker 与修复入口 | `I` | `configuration.running` 经真实 BFF/API/PostgreSQL 预检返回 `409 comparison_profile_missing`，数据库零写入；每个 blocker 均包含稳定码、说明、修复建议和服务端生成的唯一动作。浏览器实测从装配抽屉进入携带 `check_item_id` 的“规则样本来源”，保存与激活按钮保持禁用 | `done / verified_full_stack` | 无；缺失资产进入样本导入，已有不兼容资产进入生产工作台，多候选歧义回到装配选择 |
| 20 | draft digest、审批激活和 Plan 同步 | `I + H` | 真实 API/PostgreSQL 纵切已验证 preview、draft digest、职责分离、原子激活和 Active RuntimeBundle Resolver；隔离 Active Bundle 再经设备 Effective Plan HTTP 冻结精确 Runtime Manifest ID/digest 与采集命令 | `integration_done / effective_plan_http_verified / blocked_by_human_gate` | 由真实审批人复核并激活首批生产 Bundle；工程测试不得替代该人工门禁 |
| 21 | 单 fallback 可运行且披露韧性降低 | `I` | 同一真实批次冻结主命令 `display cpu-usage` 为 `asset_unavailable_at_plan_time`，只执行具备完整 RuntimeBundle 的备用命令 `display cpu`；Snapshot 解析与 Observation 正常，计划、JSON/Markdown/XLSX 均披露 `fallback_resilience=reduced` 和不可用路径 | `done / verified_full_stack` | 无工程集成门禁；真实多厂商 fallback 资产覆盖仍由生产审批与灰度验证 |
| 22 | 成功但零记录必须无法判断 | `I` | 领域服务验证 `valid_empty -> EvidenceQualification(empty) -> Coverage(empty) -> Assessment(unable_to_determine)`；同一事实经真实 BFF/API/Celery/PostgreSQL/Redis/MinIO 形成五类制品，均披露“输出为空/未观测实体/未形成观测”，不授予健康权限 | `done / verified_full_stack` | 无；真实现场空结果继续作为生产回归样本 |
| 23 | stale、cutoff、晚到事实新 Manifest | `I` | 真实 `lixian/Q2` 输出经 BFF -> FastAPI -> Celery -> PostgreSQL/Redis -> MinIO 形成并签发 v1；补录 cutoff 后的迟到事实后显式物化 v2，`late_fact_count=4`、source digest 改变，v1 五类制品和签发摘要保持不变；下载 GET 不再隐式创建 Manifest | `done / verified_full_stack` | 无；现场迟到数据继续作为生产回归，不得借下载动作隐式生成版本 |
| 24 | Prompt/IP/Inventory 冲突隔离 | `I` | 真实 `lixian/Q2` CLI 经 BFF/MinIO/Celery/PostgreSQL 预览后形成同 IP 0.95、同名 0.85 两个候选，任务以 `ambiguous_device_binding` 阻断；未确认执行返回 409，人工选择精确 Inventory UUID 后同一事务立即重算为 ready，期间四类治理计数均不变 | `done / verified_full_stack` | 无；新的身份冲突仍须人工选择或作为外部设备确认，系统不得按最高分静默绑定 |
| 25 | 截断 CLI 部分解析仍无法判断 | `I` | OutputQuality/Assessment 领域合同与真实交付栈组合验证：保留 2 条已解析记录，但 Qualification 为 `truncated`、Coverage 为 `partial`，JSON/Markdown/DOCX/XLSX 同时披露截断、48/2 覆盖与缺失实体，结论为“部分检查项无法判断” | `done / verified_full_stack` | 无；真实设备分页提示变体继续进入负样本回归 |
| 26 | 期望 48 接口、实际 40 接口 | `I` | 真实 BFF -> FastAPI -> PostgreSQL -> Celery/RabbitMQ -> Redis -> MinIO：冻结 Inventory 实体分母 48、实际观测 40，并将缺失/额外接口样例投影到 JSON、Markdown、DOCX 和 XLSX；结论为“部分检查项无法判断” | `done / verified_full_stack` | 无；真实设备当前周期与人工签发仍属于场景 32 和生产门禁 |
| 27 | 历史 Risk 本批失败不恢复、不刷新 | `I` | 风险生命周期领域测试证明失败执行不改 `last_seen_at` 或自动恢复；真实交付栈使用 30 天前 open Risk 与本批 `parse_failed`，五类制品均将其放入“历史风险待复核”，结论为“存在未关闭风险，且部分检查项无法判断” | `done / verified_full_stack` | 无；真实整改责任人与关闭审批属于生产业务门禁 |
| 28 | 签发后旧 Manifest 不变，新版重签 | `I + H` | 两个临时认证账号经真实 BFF/API/PostgreSQL/MinIO 验证 v1 与 v2 独立内容验收和签发；v1 被取代后仍按精确 Manifest ID 下载原五类字节，版本列表同时展示 `released + superseded` 与当前 v2。v1 的冻结结论、`formal`、采集时点权限、内容接受摘要和签发均不变 | `integration_done / verified_full_stack / blocked_by_real_human_gate` | 工程行为已闭环；真实甲方内容验收人与签发人仍须复核生产报告，自动化双账号不得替代 |
| 29 | 报告异步物化、去重、失败保旧、重试切换 | `I` | 真实 BFF -> FastAPI -> PostgreSQL -> Celery/RabbitMQ -> Redis result -> MinIO：首次成功、运行中/完成后同键重放、停 MinIO 后重试耗尽、失败保留旧投影与对象、新键重试成功；请求账本为 `completed/failed/completed` | `done / verified_full_stack` | 无；正式签发属于场景 28，不由本场景替代 |
| 30 | Parser missing/compile/parse 三失败层 | `I` | 三组隔离 Snapshot 均先冻结身份确认、输出完整的 Qualification，再分别形成 `template_missing / parser_compile_failed / parse_failed`，Coverage 均为 `not_evaluable`；真实五类制品保留机器码并显示中文失败层，均为无法判断 | `done / verified_full_stack` | 无；新的 Parser 失败码必须进入同一受控字典和回归 |
| 31 | cursor 篡改、过期、筛选和深页 | `I` | cursor v3 已实现 HMAC、TTL、规范 Base64URL、查询 scope、稳定 keyset、首屏 total 复用和浅页 offset 上限；7 项合同测试与 1 项真实 FastAPI/PostgreSQL 集成测试通过，1,105 条结果连续翻至尾页且后续页 `COUNT=0` | `done / verified_http_postgresql` | 无；真实浏览器大数据交互作为持续 CM 回归，不替代已通过的集成层 |
| 32 | 历史报告不冒充当前；当前周期门禁 | `P` | `lixian` 历史/影子报告为非当前结论 | `partial / blocked_by_environment` | 新鲜真实设备 `production_current` 批次和正式签发 |
| 33 | AssetGap 只预填离线治理，未确认数据禁调 LLM | `I` | 真实 AssetGap 分页/读取返回稳定治理动作；浏览器点击后进入携带 gap、检查项、实现和命令的规则样本页并自动打开预填导入抽屉；直接从生产 Change 缺口创建规则任务及未确认身份执行均返回 409，Parser Job、RuleGenerationJob、模型调用和 Workflow Run 前后完全不变 | `done / verified_full_stack` | 无；工程师仍须创建、导入、预览并确认不可变离线样本任务后，才允许后续模型调度 |
| 34 | 证据索引与原始输出分别下载 | `I` | 真实 `lixian/Q2` 源文件经 BFF -> FastAPI -> PostgreSQL/ReportManifest -> MinIO：operator 原文下载返回 403，admin 返回 200；原始文件字节与源 SHA-256 一致，仅附设备、登录失败、输出缺失和校验四份 TXT 清单，`.collection.json` 被排除；证据包仍无 raw CLI 正文 | `done / verified_full_stack` | 无；该场景证明受控交付路径，不提升历史样本为当前网络事实，也不替代正式签发 |
| 35 | 网络级健康表达四级门禁 | `I` | 真实 `lixian` CPU 输出经 BFF -> FastAPI -> PostgreSQL -> Celery/RabbitMQ -> Redis -> MinIO 创建 4 个隔离批次、105 个目标对；部分未知、全部不适用、未关闭历史风险和全部健康均得到预期权限，五类制品、API、签发页与 v4 Manifest 的同一 `conclusion-permission/v1` 一致 | `done / verified_full_stack` | 无工程门禁；真实设备当前批次与甲方签发属于场景 32 和人工投产门禁，不由本场景替代 |
| 36 | 离线证据申请当前权限并生成独立生产批次 | `I + P + H` | 合成但新鲜的 H3C 当前配置经真实 BFF、FastAPI、PostgreSQL、Celery/Redis、MinIO 和报告下载完成工程纵切：历史升级、同人证明、幂等冲突、Snapshot 正文篡改均被稳定拒绝；prepare/attest 并发 exactly-once，Worker 重投递前后事实计数不变。冻结两台设备，一台成功形成 `informational`，一台命令失败形成 `comparison_failed + unable_to_determine`；Evidence 以 Inventory UUID 对账，ReportConclusionPolicy 保持 `terminal_truth_reconciled=true`、`current_conclusion_ready=true`、`overall_healthy_allowed=false`。真实浏览器已验证上传、独立复核、空计划 fail closed、批准后 Beat stale、刷新保持、延迟调度和恢复续跑；冻结后 Active Bundle 切换不影响本批次，Manifest/版本/摘要缺失或篡改在 Parser 前拒绝。证据明确 `production_network_claim=false` | `integration_done / engineering_verified / browser_fault_matrix_verified / production_evidence_pending` | `I` 层工程矩阵已关闭；仍须以新鲜现场采集、真实人工批准 Active RuntimeBundle、真实职责主体及正式签发完成 `P/H` 层 |
| 37 | 显式内容验收与独立报告签发 | `I + H` | 迁移 `0084` 已应用；服务端对 v5 Manifest/source、ClaimLedger、Target/Execution/Assessment、Evidence/Coverage、readiness 与五类交付物实际字节做全量对账，冻结关键项全审、每检查项追溯项和分层普通事实。真实 `lixian/Q2` 经 BFF/API/Celery/Redis/PostgreSQL/MinIO 完成 v1/v2 内容接受、独立签发、精确下载、旧版冻结和清理；对象删除、摘要漂移及内容接受后篡改均 409。1000 条清单完成第 1/20 页、幂等重放、重登录恢复和真实浏览器刷新/布局复验；前端 `6 passed`、Vite `3387 modules` 通过 | `engineering_done / verified_full_stack_browser / real_human_pending` | 由甲方真实内容验收人与不同签发人对新鲜 `production_current` 报告作出决定；自动化账号不得替代 |
| 38 | Assessment 策略来源优先级与冲突 | `I` | 迁移 `0086-0090` 已应用；阈值、期望状态与适用性 Profile/Version/Approval/Binding、TargetPolicySnapshot、实体级 AssessmentBasis、全目标影响预览、Inventory 选择器、巡检只读依据、历史草稿迁移、双读影子和目标级切换/回滚已进入主链。`scripts/test/assessment_policy_scenario38_full_stack_e2e.py` 使用真实 BFF/FastAPI/PostgreSQL/Celery/RabbitMQ/Redis/MinIO 验证并发复核幂等、RBAC、设备输出 > Inventory > Scope > Catalog、冲突/过期/篡改失败关闭、接口期望状态、同实体光功率阈值、冻结重放、Worker 重投递和五类制品字节/摘要回读；ClaimLedger 的 policy/basis digest 与冻结事实一致。摘要：`.tmp/assessment-policy-scenario38-full-stack-e2e/summary.json`，`production_network_claim=false`。浏览器已验证判断策略路由、27 条来源盘点、三阶段迁移导航、桌面/移动布局和零控制台错误 | `done / verified_full_stack / engineering_historical_evidence / browser_navigation_verified / populated_data_path_pending` | 使用隔离已审批策略补验影响预览、Inventory 绑定、目标级切换/回滚；随后只允许由真实工程师审批黄金资产并在新鲜 `production_current` 使用。历史 `lixian` 不得冒充当前网络事实、真实人工决定或甲方验收 |
| 39 | CollectionImplementation 唯一选择与 ComparisonContext 基线资格 | `I + P + H` | Resolver 对零命中和同优先级多命中 fail closed；`ProductionBaselineCandidateRepository` 按设备/CheckItemVersion 批量装载并核验用途、终态 Assessment、EvidenceQualification、Snapshot/PatrolRun、RuntimeManifest/RuntimeBundle/ComparisonContext、来源资格及事实合同。真实 PostgreSQL 事务纵切使用两台 H3C MSR5620 和两台 Cisco NEXUS9000 历史双周期输出，证明首次观测、唯一最近基线、同时间歧义、60 天过期、Contract/Bundle 漂移及零/多实现；UTC 与 `+08:00` 等价时刻摘要一致，Change/Event lineage 完整，测试前后数据库不变。摘要：`.tmp/scenario39-comparison-context-e2e/summary.json`，`production_network_claim=false` | `integration_done / verified_real_postgresql_historical_evidence / production_current_pending` | 使用真实人工批准 Active RuntimeBundle 和新鲜甲方范围完成两个 `production_current` 周期，对账 Change/Event、五类制品、内容验收和独立签发；历史样本工程证据不得替代 `P/H` 层 |
| 40 | 批次取消、Worker 恢复与晚到回调 fencing | `I` | 批次取消 API/BFF/UI、协作式 revoke、Run/step 终态锁定、后续分发阻断、子 Workflow 传播、完整 UTD 分母和五类制品已实现；1000 台 × 2 检查项以常数级查询结算。真实 PostgreSQL 拒绝晚到成功/失败；真实 RabbitMQ/Celery 隔离队列完成 Worker A 硬终止、Worker B 重投递、旧 attempt abandoned 和新 attempt completed。connection/command/queue/heartbeat 原因码贯穿 Workflow、目标、Assessment 与报告。浏览器已验证待分发取消入口和无副作用退出 | `in_progress / engineering_advanced / production_device_fault_matrix_pending` | 在预发布真实设备完成断连、命令超时、串并行任务中断和千台在线取消压测；对账已完成事实、未完成 UTD、报告分母和五类制品 |
| 41 | 多事件与跨检查项风险去重 | `I + H` | 迁移 `0085`、RiskEpisodeAssociation 状态机、API、风险详情治理 UI、ReportManifest、ClaimLedger 和五类制品投影已落地。受控双风险/双事件证明：待审、拒绝、撤销、episode 变化、歧义和链式关系均不合并；只有另一工程师批准且仍有效的关联把 2 个 RiskEpisode 投影为 1 个领导问题，同时保留 2 个技术风险、2 个事件和完整证据。旧领导报告的接口 Down/光功率启发式已删除。后端 108 项、前端 4 项和 Vite 构建通过；PostgreSQL 迁移后无测试关联残留，Active RuntimeBundle 仍为 0 | `engineering_done / report_and_ui_verified / network_sme_policy_pending` | 使用真实甲方范围和新鲜 `production_current` 事实，由网络 SME 复核至少一组批准和一组拒绝的关联，再由甲方内容验收人与独立签发人确认领导/技术口径；自动化账号不得替代 |
| 42 | 生产误报/漏报反馈、暂停与回滚 | `I + P` | 迁移 `0093`、`ProductionFeedbackService`、Patrol API、设备巡检“质量反馈”工作面及 Rollout evaluator 已落地。领域测试证明反馈精确绑定 Execution/Assessment/Event/Risk 与冻结 Bundle、跨执行 occurrence 被拒绝、同人复核被拒绝、确认反馈只创建治理待办且历史事实/RuleGenerationJob 不变、候选关联受目的与状态约束、样本不足不伪造误报率；Rollout 回归证明已确认反馈进入不可变阶段证据并越阈暂停。后端相关 `31 passed`、前端组件 `2 passed`、生产构建通过 | `engineering_done / unit_and_component_verified / integration_and_production_evidence_pending` | 在真实 PostgreSQL/BFF/浏览器/Worker 预发布链路从事件提交误报、漏报、严重度和作用域反馈，经独立复核形成离线候选并回放；证明已签发报告不变、观察窗口后阈值越界暂停晋级、上一 Bundle 可回滚，随后才允许超过 20% 灰度 |
| 43 | 废弃链路真实零流量与安全删除 | `I + P` | 迁移 `0095` 已应用；五个登记候选具备 Prometheus、结构化日志和 payload-free PostgreSQL 观测调用点，真实数据库完成写入/窗口计数/清理冒烟。证据导出器按候选和 release cycle 统计，审计强制七项 gate 的不可变 URI/SHA-256、至少两个不重叠周期和 `call_count=0`；命令中心废弃范围已收窄为旧顶层列表，保留检查项内的逐命令证据。治理及受影响回归 `150 passed` | `engineering_done / observation_started / two_release_cycles_pending` | 在部署迁移和观测代码后经过两个真实发布周期，分别导出零流量/零写入证据，证明替代覆盖并演练回滚；只有全部七项门禁通过后才把候选提升为 `ready_to_remove`，当前不得删除 |

## 3. 本轮新增可复现证据

### 3.1 受控风险报告内容验收

命令：

```powershell
venv\Scripts\python.exe scripts\test\check_item_report_content_acceptance.py
```

输出目录：

```text
.tmp/check-item-report-content-acceptance
```

该夹具固定生成：

- 3 台设备、3 个检查项；
- 2 项风险、2 项变化、2 项事件；
- 1 项历史风险待复核、1 项恢复待确认；
- 1 项 `parse_failed` 导致的“无法判断”；
- CPU 静态阈值和光功率设备输出动态阈值；
- 整改责任人、动作和期限；
- Markdown、DOCX、XLSX、JSON 和证据索引包。

这是 `shadow_validation` 受控夹具，`production_network_claim=false`。它证明报告内容合同和制品
一致性，不证明当前网络状态。

显式 `ContentAccepted` 的持久化实现另由以下回归验证：

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_report_content_review.py tests\unit\test_patrol_risk_api.py -q
npm --prefix web\react_frontend test -- --run src/components/devices/ReportReleasePanel.test.tsx
npm --prefix web\react_frontend run build
```

当前结果分别为后端 `19 passed`、报告视图回归 `37 passed`、前端 `5 passed`、生产构建
`3387 modules transformed`。真实依赖正向纵切见 3.15，已关闭迁移应用、BFF/API 双账号、v1/v2
内容接受、独立签发、旧版冻结和清理；浏览器千台恢复性与预发布对象故障注入仍保留。所有自动化账号
只证明工程机制，不代表甲方已经接受任何生产报告。

### 3.2 技术附表视觉验收

`check_item_report_content_acceptance.py` 已将受控风险生命周期报告升级为 15 页技术附表：总览、
检查项结论、CPU、接口状态、接口光功率、风险、历史风险、恢复、技术恢复与关闭、整改、事件、变化、
当前观测、事实声明和证据索引。CPU、接口状态和接口光功率三页只消费冻结的 canonical fact 与
`ReportProjectionProfile`，不读取厂商命令或 Parser Value；缺少事实的接口状态目标仍保留一行并明确
显示“无法判断 / Parser 解析失败”。导出层使用显式文本单元格；SHA-256 显示为
`sha256:<digest>`，不会被 Excel 或第三方查看器转成科学计数法；负光功率和风险当前值保持数值类型，
单位独立成列。`artifact-tool` 已完成逐页渲染与关键区检查，中文表头不重叠，公式错误扫描为 0。
受控制品位于 `.tmp/check-item-report-content-acceptance`，其中 17 条 Claim 与所有业务明细逐行对账，
投影摘要和采集路径韧性均为冻结事实。

本轮运行环境没有 LibreOffice/Word 可执行文件，因此新增“含风险内容”的 DOCX 完成了 OOXML
结构、内容合同和无障碍审计（high/medium/low 均为 0），没有冒充逐页视觉通过。历史零风险 DOCX
的既有 Office/WPS 视觉证据仍然有效；风险版 DOCX 的逐页视觉检查保留为人工签发门禁。

### 3.3 安全 cursor 的真实 HTTP/PostgreSQL 深页验收

命令：

```powershell
venv\Scripts\python.exe -m pytest tests\integration\test_check_item_result_cursor_http.py tests\unit\test_check_item_batch_result_cursor.py -q
```

真实集成用例在外层回滚事务中创建 1,105 个设备检查结果，经 FastAPI 路由连续使用
`next_cursor` 翻至尾页。验收确认：

- 1,105 个 `execution_id` 无遗漏、无重复；
- 第一页计算准确总数，所有后续 cursor 页不再执行 `COUNT`；
- 文本篡改、非规范或畸形 Base64URL、过期、批次/筛选 scope 不一致均拒绝；
- `offset > 1000` 经 HTTP 返回 400，并要求继续使用 cursor；
- 用例结束后外层事务整体回滚，不向业务库残留测试批次。

### 3.4 规则资产画像与人工发布权回归

命令：

```powershell
venv\Scripts\python.exe -m pytest \
  tests\unit\test_rule_asset_requirements.py \
  tests\unit\test_rule_asset_automation.py \
  tests\unit\test_rule_asset_bundles.py \
  tests\unit\test_rule_asset_persister_skill.py \
  tests\unit\test_dataset_rule_generation_context.py \
  tests\unit\test_event_rule_generator_skill.py -q

venv\Scripts\python.exe -m pytest \
  tests\unit\test_rule_asset_command_units.py \
  tests\unit\test_rule_asset_engineering_revision.py \
  tests\unit\test_rule_control_jobs.py \
  tests\unit\test_parser_management_api.py \
  tests\unit\test_parser_control_plane.py \
  tests\unit\test_check_item_catalog.py \
  tests\unit\test_check_item_assessment_runtime.py \
  tests\unit\test_patrol_change_event_pipeline.py -q

venv\Scripts\python.exe -m pytest tests\unit\test_workflow_plugins.py -q
```

结果分别为 `62 passed`、`211 passed` 和 `36 passed`。前端 ESLint 与生产构建同时通过。该证据证明：

- 同一冻结 AssetRequirementProfile 控制 Workflow、生成器、持久化、Bundle 读取、审批和装配；
- Contract-only 检查项不会被强迫生成 Policy、Rule 或历史回放；
- 健康类检查项按 canonical observation 校验 CurrentState、Change 和故障/恢复事件角色；
- 画像外多余资产、角色缺失及来源/画像摘要漂移均 fail closed；
- 旧 `auto_publish` 配置只会安全降级为 `generate_only`，自动化结果始终为
  `publish_authority=human_only`。

这些回归不替代场景 6 的人工语义复核，也不证明真实 API/Worker/PostgreSQL 全栈发布已经完成。
场景 6 因而继续保持 `partial / blocked_by_human_gate`。

### 3.5 资产画像控制面真实浏览器复验

在本地完整服务栈重启后，使用真实浏览器访问
`/skills/parser?workspace=production&subtab=asset-production-units` 和
`/skills/parser?workspace=runtime&subtab=coverage-backfill`，确认：

- 候选自动化只提供“人工触发 / 自动准备候选”，最近运行只统计启动与待人工复核，不展示自动发布；
- `device.version` 的 `structured + evidence` 画像只要求 IdentityPolicy、Parser、Contract 和
  RuntimeBundle，CurrentStateRule、ChangePolicy、EventRule 与 TextComparisonProfile 明确为
  `not_required`；
- `configuration.running` 只展示 TextComparisonProfile 与 RuntimeBundle，不伪造 TextFSM 或事件规则；
- `interface.oper_status`、`system.cpu_usage` 和 `interface.optical_power` 按健康画像展示当前状态、
  变化和事件角色；
- Active Catalog 当前为 20 个检查项、39 个实现对、0 个 Active RuntimeBundle，页面如实显示
  39 个缺口和 0% 就绪率；
- 自动化设置中的周期、设备、记录数量和预计事件量明确标记为“历史回放门槛”，Contract-only
  单周期资产不受这些门槛影响；
- 检查项主表、覆盖页与运行包装配抽屉共用中文展示投影，稳定 Catalog ID 保留，英文原名不再穿透
  主要业务视图。

对应组件回归为 `ProductWorkspaces.test.tsx` 10 项和
`RuleAssetProductionWorkbench.test.tsx` 4 项，后端画像门禁回归为
`test_rule_asset_automation.py` 8 项。该证据证明控制面合同和真实部署页面一致，不替代资产人工审批，
也不把 0 Active RuntimeBundle 解释为生产就绪。

### 3.6 异步报告真实全栈故障注入

命令：

```powershell
$env:NETOPS_E2E_PASSWORD = '<本地 BFF 测试用户密码>'
venv\Scripts\python.exe scripts\test\patrol_report_async_full_stack_e2e.py --timeout-seconds 180
```

证据：

```text
.tmp/patrol-report-async-full-stack-e2e/summary.json
```

测试通过真实 Django BFF 登录和代理调用 FastAPI，由 PostgreSQL 持久化报告请求账本，Celery Worker
经 RabbitMQ 消费任务、Redis 保存任务结果，并由 MinIO 保存与回读报告对象。验收确认：

- 首次请求返回 `202` 并完成物化；运行中和完成后重复同一幂等键均重放原 task/result，数据库不新增
  请求行。
- `(batch_id, idempotency_key)` 唯一，请求使用确定性 task id，BFF 正确转发
  `Idempotency-Key`，同时不允许客户端伪造受信任身份头。
- 停止真实 MinIO 后，以新幂等键发起的任务经过真实 Worker 自动重试并最终进入 `failed`，账本保存
  终态时间和错误；上一版 PostgreSQL 投影与 MinIO 对象均未改变。
- 恢复 MinIO 后，以第三个幂等键显式重试成功，新的源摘要与对象原子切换；三个请求状态依次为
  `completed / failed / completed`。
- request fencing 阻止旧 Worker 或延迟重投递覆盖更新请求；测试结束后数据库、审计记录和 MinIO
  测试对象全部清理。

这项证据关闭场景 29 的无现场设备集成门禁。它不证明当前网络状态、生产资产已审批、报告已人工
签发，也不替代场景 23、28、32 和 35 的独立验收。

### 3.7 同一 ReportManifest 的真实下载与对象一致性

命令：

```powershell
$env:NETOPS_E2E_PASSWORD = '<本地 BFF 测试用户密码>'
venv\Scripts\python.exe scripts\test\check_item_report_download_full_stack_e2e.py --timeout-seconds 180
```

证据：

```text
.tmp/check-item-report-download-full-stack-e2e/summary.json
```

测试使用 `lixian/Q2/XA-FOTIC-RT5620-1-10.34.2.219.txt` 的真实 `display version` 输出，经现有
Active Parser 形成 12 条解析记录和 2 条 canonical Observation，创建临时历史批次后通过真实
Django BFF 调用 FastAPI，交由 Celery/RabbitMQ/Redis 物化并写入 MinIO。验收确认：

- Manifest 为 `facts_status=final`、`release_status=pending_signoff`、用途为
  `offline_historical`，没有把历史证据冒充当前网络结论。
- Markdown、DOCX、XLSX、JSON 和 evidence ZIP 五种下载的 SHA-256 分别等于同一 Manifest
  记录的摘要，也等于对应 MinIO 对象字节摘要。
- BFF 仅转发受控的下载完整性响应头，保留事实状态、签发状态、source digest 和
  Content-Disposition，不透传任意上游响应头。
- 未签发报告下载的是已经物化并附着到 Manifest 的不可变对象，仅在文件名和响应头标记为预览；
  下载时不会重新渲染另一份内容。
- evidence ZIP 包含 Manifest、Assessment、风险生命周期和证据索引，不包含 raw CLI 正文；测试
  结束后 PostgreSQL 临时事实、审计记录和 MinIO 对象全部清理。

首次全栈复验发现未签发下载路径会重新渲染已物化报告，导致下载字节与 Manifest 摘要不一致。
实现已修正为：只要请求制品已经附着到 Manifest，就必须从对象存储回读并校验；签发状态只改变
交付标签，不改变制品字节。该证据关闭场景 12，但不替代报告内容人工复核和场景 28 的正式签发。

### 3.8 生产 direct 入口无副作用拒绝

命令：

```powershell
$env:NETOPS_E2E_PASSWORD = '<本地 BFF 测试用户密码>'
venv\Scripts\python.exe scripts\test\check_item_direct_launch_rejection_full_stack_e2e.py
```

证据：

```text
.tmp/check-item-direct-launch-rejection-e2e/summary.json
```

测试通过真实 Django BFF 登录并向生产巡检启动接口提交 `launch_mode=direct`。FastAPI 在设备解析、
平台 readiness、批次持久化、审计和任务投递前返回 409，错误码为
`direct_check_item_patrol_disabled`。请求前后：

- `PatrolBatchRecord` 均为 0；
- `CollectionExecutionPlanRecord` 均为 0；
- `WorkflowRun` 均为 10；
- `device_patrol_launch` 审计均为 14；
- RabbitMQ `netops.patrol` 待消费消息均为 0，消费者均为 1。

单元测试同时使用禁止调用的 preview spy，证明生产 direct 拒绝发生在预览函数之前。非生产
`parser_validation` 仍可使用 direct 作为诊断适配器，不构成第二条生产语义。该证据关闭场景 13。

### 3.9 规则资产离线来源真实全栈

命令：

```powershell
$env:NETOPS_E2E_PASSWORD = '<本地 BFF 测试用户密码>'
venv\Scripts\python.exe scripts\test\rule_asset_source_import_full_stack_e2e.py
```

证据：

```text
.tmp/rule-asset-source-import-full-stack-e2e/summary.json
```

测试从真实 `lixian/Q2/XA-FOTIC-RT5620-1-10.34.2.219.txt` 精确提取
`display version` 输出，经 Django BFF 创建 `rule_asset_source` 离线任务，使用预签名 URL
将文件真实写入 MinIO，再由 Celery 完成预览并由 `parser-validation-ingest` Workflow 执行导入。
验收确认：

- 预览识别 1 个文件、1 台设备和 1 条命令，`ready=1`、`template_missing=0`、
  `parse_failed=0`；
- 外部样本必须经人工确认且保持 `inventory_device_id=null`，不会冒充生产 Inventory 设备；
- PostgreSQL 中形成同一来源任务、Period、PatrolRun、Snapshot、Workflow 和审计事实；
- Snapshot 使用 Active Parser `c6d5718d-6357-4e44-af82-db7fd9d5c020` 解析出 12 条记录；
- 已支持命令不会重复创建 Parser Job，生成前后 Parser Job 均为 58，模型调用均为 31；
- 当前来源尚未生成自己的 RuleGenerationContext 和候选 Bundle 时，控制面必须返回
  `context_id=null`、`bundle_id=null`，不得借用其他离线任务或历史无来源上下文；
- 测试结束后离线任务、Workflow、Run、Dataset、文件元数据、文件夹、审计和 MinIO 对象均完成
  范围化清理。

该证据关闭场景 1，但不替代场景 3 至 7 的 Parser 候选生成、规则资产生成、人工审批和
RuntimeBundle 激活。

### 3.10 NTC Parser 候选真实全栈

命令：

```powershell
$env:NETOPS_E2E_PASSWORD = '<本地 BFF 测试用户密码>'
venv\Scripts\python.exe scripts\test\parser_ntc_candidate_full_stack_e2e.py `
  --timeout-seconds 360
```

证据：

```text
.tmp/parser-ntc-candidate-full-stack-e2e/summary.json
```

测试从真实 `lixian/Q2/SW1-10.34.2.11_2025-12-24_15-24-35.txt` 精确提取
`display device manuinfo`，创建隔离型号作用域后完成真实 BFF、FastAPI、MinIO、Celery、
`parser-validation-ingest` Workflow 和 PostgreSQL 链路。验收确认：

- 命中 `ntc_templates 9.1.0` 的
  `hp_comware_display_device_manuinfo.textfsm`，没有调用 LLM 从零生成 TextFSM；
- provenance 冻结 Apache-2.0 许可证、原始模板、源摘要、派生模板摘要和
  `ntc-platform-compat/v2` 转换；
- v2 只做 Value 标识符大小写规范化和可信 catch-all Error 兼容转换，不写入 canonical 字段；
- DeepSeek `deepseek-v4-pro` 对实际解析结果完成独立语义初审并通过；
- 生产 `ParserRuntime` 在审批比较 API 中对冻结样本解析成功，`1/1` 样本通过并形成 `8` 条组件记录；
- 客观门禁同时包含 `compiled/executed/source_executed/source_shape_compatible`，生成、审批和激活
  不再因门禁字段漂移误拦截；
- 候选状态为 `pending_approval`、`approval_ready=false`，唯一 blocker 为尚未接受人工语义复核；
  Active 查询为零，生成和 AI 初审没有绕过人工审批；
- Parser Job 删除经真实 BFF `DELETE` 请求体传递删除理由；离线来源、Workflow、Run、Dataset、
  文件、文件夹、候选和 MinIO 对象全部完成范围化清理；
- E2E 摘要分开保存 `flow_success` 与 `cleanup_success`，清理异常不会覆盖主流程诊断。

该证据关闭场景 3，并为场景 4 的真实 LLM 初审分支提供证据；它不执行人工审批，所以不关闭场景
5，也不证明 Contract、Policy、Rule 或 RuntimeBundle 已经发布。

### 3.11 命令资产归组与生产工作台分页真实全栈

证据：

```text
.tmp/rule-asset-source-import-full-stack-e2e/summary.json
```

场景 1 的真实离线来源导入完成后，同一夹具继续通过 BFF 调用来源命令资产接口和全局生产工作台
分页接口，并与 PostgreSQL 的 `RuleAssetCommandUnitRecord` 对账。验收确认：

- `H3C + MSR5620 + display version` 只形成一个稳定 `asset_key`；
- 当前来源为 1 台设备、1 个命令切片和 1 个结构变体，结构变体从本来源 Snapshot 计算，不借用
  历史 Parser Job 的样本；
- 来源详情、全局 API 和 PostgreSQL 的厂商、型号集合、Profile、命令、设备数及结构变体数完全一致；
- `vendor=H3C`、`model=MSR5620` 和命令搜索可命中，随机搜索词返回 `total=0`；
- `page_size=1` 的第一页返回 1 条，第二页为空，但 `total=1`、`page_count=1` 保持稳定；
- PostgreSQL 的型号数组筛选使用 JSONB contains，不再把 JSON 列编译为 PostgreSQL 不支持的
  `LIKE` 运算；
- 导入前后 Parser Job 均为 58、Parser 模型调用均为 31，证明已有 Active Parser 时不会产生重复任务；
- 证据 JSON 为标准 UTF-8；临时用户、任务、Workflow、Run、Dataset、元数据、文件夹和 MinIO
  对象均完成范围化清理。

该证据关闭场景 2。它只证明检查项采集实现的归组与控制面可运营性，不代表规则资产或
RuntimeBundle 已经完成人工发布。

### 3.12 Parser 人工语义复核与独立发布真实全栈

证据：

```text
.tmp/parser-semantic-review-full-stack-e2e/summary.json
```

测试创建一个随机兼容范围的隔离 H3C Parser 候选和一份 parser-validation Snapshot，并创建两个
临时 Operator 账号，通过真实 Django BFF、FastAPI 和 PostgreSQL 验证：

- 审批比较由生产 `ParserRuntime` 解析 `1/1` 份样本、`2` 条接口记录；
- 保存语义复核前调用发布返回 `400`，阻断原因为缺少人工语义复核；
- 人工直审确认实际解析结果与主键后，候选继续保持 `pending_approval`；
- 复核证据冻结模板、Contract、ParserSpec、样本 Manifest、回归计数和最终解析值摘要；
- 语义复核人尝试发布返回 `400`，服务端要求不同发布审批人；
- 第二个账号发布成功，运行时 Resolver 读取同一 Active Parser，派生 Contract 为 Active；
- 数据库同时存在 `parser_template_semantic_reviewed` 与 `parser_template_activated` 两条独立审计，
  激活 Outbox 在同一事务中创建；
- 测试结束后模板、Contract、Outbox、Snapshot、规则种子和临时用户的隔离残留均为 `0`。

该证据关闭场景 4，并完成场景 5 的工程集成层。2026-08-02 审计后，旧四个真实候选因没有不可变
样本关联已拒绝；当前先受“按新协议重新生成”门禁约束。新候选形成后，业务语义复核与生产批准仍是
不可自动代理的 `H` 层门禁，因此场景 5 继续保持 `candidate_regeneration_in_progress /
blocked_by_human_gate`。

### 3.13 规则资产与 RuntimeBundle 真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\rule_asset_runtime_bundle_full_stack_e2e.py `
  --base-url http://localhost:3000 `
  --timeout-seconds 240
```

证据：

```text
.tmp/rule-asset-runtime-bundle-full-stack-e2e/summary.json
```

测试创建隔离的 `system.cpu_usage` 兼容实现、Active Parser 和两个已完成离线规则来源周期，通过
真实认证 BFF、FastAPI、Celery Worker、七步 `rule-asset-generation` Workflow 和 PostgreSQL
完成以下纵切：

```text
离线结构化事实
-> 不可变 sanitized context
-> Contract / ChangePolicy / EventRule 候选
-> validation / historical replay
-> 工程复核
-> 发布审批
-> Active rule resolver
-> RuntimeBundle preview / draft
-> 独立激活
-> Active RuntimeBundle resolver
```

验收确认：

- Workflow 状态为 `completed`，生成 `1` 个 ObservationContract、`1` 个 ChangePolicy 和
  `2` 个 EventRule，候选创建人等于 Workflow 冻结的发起 actor；
- 未完成工程复核时批准返回 `409`，创建人自审返回 `409`，工程复核人自批返回 `409`；
- 独立账号完成工程复核和发布审批后，运行时 Resolver 能读取同一组 Active 冻结资产；
- RuntimeBundle preview 与 draft 使用冻结摘要；创建人自激活返回 `409`，独立账号激活成功；
- Active RuntimeBundle Resolver 返回冻结的 Parser、Contract、Policy 和 EventRule 角色，而不是
  动态拼接当前最新子资产；
- 测试分别记录主流程和清理结果；两者均成功，测试前后的生产 Active Parser、Contract、Policy、
  EventRule 和 RuntimeBundle 身份及摘要完全一致。

该证据完成场景 6、7 和 20 的工程集成层，但不关闭其 `H` 层。测试使用隔离作用域、确定性内置
Provider 和临时职责账号，不能替代真实 LLM 候选的语义复核、生产目标矩阵审批、页面级人工操作
或真实资产激活。

### 3.14 网络级健康表达与失败事实真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\report_conclusion_permission_full_stack_e2e.py `
  --password '<本地 BFF 测试用户密码>' `
  --timeout-seconds 240
```

证据：

```text
.tmp/report-conclusion-permission-full-stack-e2e/summary.json
```

测试读取 `lixian/Q3/XA-FOTIC-RT5620-1-10.34.2.219.txt` 中真实 CPU 命令输出，通过真实 Django
BFF、FastAPI、PostgreSQL、Celery/RabbitMQ、Redis 和 MinIO 创建十一个隔离
`production_current` 批次，共冻结 112 个设备检查项目标对。验收确认：

- `95 healthy + 5 unable_to_determine` 只允许“已评估范围内未发现异常，以下范围无法判断”，
  不允许“整体健康”。
- 全部 `not_applicable` 必须同时具备冻结 CheckItem applicability、同一 Execution 的显式原因和
  CoverageDecision 证据，只允许“本次无适用健康检查项”。
- 当前 Assessment 为健康但存在未关闭、未取得本批恢复证据的历史 RiskCase 时，禁止“未发现异常”
  和“整体健康”。
- 只有全部适用健康目标均为 `healthy`、目标/执行/Assessment/Qualification/Coverage/Snapshot
  完整对账且没有 unresolved RiskCase 时，才允许“本次巡检范围整体健康”。
- Inventory 期望 48 个接口但仅观测 40 个接口时，CoverageDecision 冻结缺失和额外实体样例；
  JSON、Markdown、DOCX 和 XLSX 均披露同一分母、实测数量和缺失接口，结论只能是
  “部分检查项无法判断”，不得因已观测实体均正常而标记完整或健康。
- 成功采集但零业务记录时，执行终态仍可为 `completed`，但 EvidenceQualification、Coverage 和
  Assessment 分别保留空输出、零实体和零观测事实；正式结论只能是“部分检查项无法判断”。
- 截断输出即使已解析 2 条记录，也保留 `output_quality=truncated`、实体覆盖 `48/2` 和缺失实体，
  不得用局部正常记录形成健康结论。
- Parser 缺失、模板编译失败和运行时解析失败均在 Parser 前保留合格原始证据资质，Parser 后分别
  使用 `template_missing / parser_compile_failed / parse_failed`，Coverage 为 `not_evaluable`；
  机器码保留在 JSON/技术附表，Markdown、Word 和 Excel 显示可读中文失败层。
- 30 天前的 open RiskCase 在本批 Parser 失败时不恢复、不刷新 `last_seen_at`，并进入“历史风险待复核”；
  报告结论为“存在未关闭风险，且部分检查项无法判断”。
- Markdown、DOCX、XLSX、JSON、证据包、报告 Manifest API 和签发页返回完全相同的
  `conclusion-permission/v1`；BFF 下载字节逐项等于 Manifest SHA-256 和 MinIO 对象字节。
- 测试结束后清理 55 个 MinIO 对象、11 个 Manifest、11 个报告请求、112 个 Execution 及全部隔离
  批次、Workflow、风险和检查项记录，数据库范围化残留均为 0。

配套领域与报告回归为 `103 passed`，结论策略单独为 `10 passed`，报告投影定向回归为
`28 passed`。该证据结合已有领域服务测试，关闭场景 22、25、26、27、30 和 35 的工程集成门禁，
但不把历史样本冒充现场网络，也不替代场景 32 的真实设备当前批次或甲方人工签发。

### 3.15 迟到事实、不可变旧版本与独立重签真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\report_revision_release_full_stack_e2e.py
```

证据：

```text
.tmp/report-revision-release-full-stack-e2e/summary.json
```

测试读取 `lixian/Q2/XA-FOTIC-RT5620-1-10.34.2.219.txt`，通过真实 Django BFF、FastAPI、
Celery/RabbitMQ、Redis、PostgreSQL 和 MinIO 完成以下纵切：

```text
冻结过期历史事实
-> 显式异步物化 v1
-> 内容验收账号复核并接受 v1
-> 独立签署账号发布 v1
-> 精确版本下载五类制品
-> cutoff 后补录迟到事实
-> 以新幂等键显式物化 v2
-> v1 标记 superseded，但冻结结论、验收、签名和对象保持不变
-> v2 重新内容验收并由独立账号签署
-> 分页读取版本历史
```

验收确认：

- v1 与 v2 使用不同 `report_manifest_id`、版本号和 source facts digest；v2 冻结
  `late_fact_count=4`。
- 迟到事实不会修改 v1；v1 被取代后仍可按精确 Manifest ID 下载 Markdown、DOCX、XLSX、JSON
  和证据包，五类字节与 v1 Manifest SHA-256、MinIO 对象及签发记录完全一致。
- v1 被取代后仍返回原 `historical_result`、`formal=true` 和采集时点权限语义；`superseded` 仅表示
  已有新版，不能把旧版重新计算为草稿或 `current_authority_blocked`。
- v1/v2 各自形成不可变 `ReportContentReviewRecord`、内容接受摘要和 `ReportReleaseRecord`；v2 不继承
  v1 的内容验收或发布状态，内容验收人与签发人为两个不同临时认证账号。
- 内容机器对账以冻结 `target_pairs` 与 Execution/Assessment 的唯一集合等值为实际执行分母；历史报告
  没有生产 ScopeContract 时仍由 conclusion permission 明确限制用途，不会被错误判成执行缺失。
- 报告下载 GET 只读取已挂载对象，不调用 Manifest build；未物化版本拒绝下载，不会在读取时产生
  隐式数据库写入。
- BFF 保留查询参数与 `X-NetOps-Report-Manifest-Id`、`X-NetOps-Report-Version` 完整性响应头；
  版本列表同时返回当前 v2 和 `released + superseded` 的 v1。
- 测试结束后清理 10 个 MinIO 对象、2 个 ContentReview、2 个复核项、2 个 Release、2 个 Manifest、
  2 个请求账本、10 条审计及全部隔离事实和临时账号；数据库中 `report-revision-e2e-%` 残留为 0。

这项证据关闭场景 23、28 和 37 的工程集成门禁。临时双账号证明服务端职责分离、显式内容验收、
版本不可变和独立重签机制，不代表真实甲方内容验收人或签发人已经复核任何生产报告；场景 28/37
因而继续保留 `real_human_pending` 的生产边界。

### 3.16 证据索引与原始输出分权交付真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\check_item_report_download_full_stack_e2e.py --timeout-seconds 180
```

证据：

```text
.tmp/check-item-report-download-full-stack-e2e/summary.json
```

测试读取 `lixian/Q2/XA-FOTIC-RT5620-1-10.34.2.219.txt`，把原始文件及一个故意加入的
`.collection.json` 内部 sidecar 写入 MinIO 采集归档，再经真实 Django BFF、FastAPI、
PostgreSQL、Celery/RabbitMQ、Redis 和 MinIO 生成同一批次的 ReportManifest 与五类报告制品。

验收确认：

- operator 访问受保护原文接口返回 `403`，一次性 admin 返回 `200`，临时账号在测试后删除。
- 原始输出 ZIP 中只有设备目录下的原始 `.txt`、`设备列表.txt`、`登录失败设备列表.txt`、
  `原始输出缺失设备列表.txt` 和 `原始输出校验清单.txt`；故意写入的 `.collection.json`、
  CSV、JSON 和 JSONL 均未进入交付包。
- 原始 `.txt` 与 `lixian` 源文件逐字节一致，SHA-256 为
  `115633bafee00c7387b13eaad543f2b48bb4417225b65d0fc49e605c4d8bc736`。
- 下载请求显式绑定 `report_manifest_id`；响应头中的 Manifest ID、版本号、原始证据集合摘要和
  文件数与冻结 Manifest 一致。服务端按 `(PatrolRun, source_file_hash)` 对完整文件集合做等值
  比较，缺失、多出或摘要不一致均返回 `409`，不会静默拼入当前批次外文件。
- 设备清单包含完整设备身份和原始文件数；本成功场景的登录失败与输出缺失清单仍保留表头而不是
  被省略，便于千台批次统一自动处理。
- 证据索引包仅包含 README、Manifest、Assessment、风险生命周期和 evidence index，不含
  `H3C Comware Software` 等 raw CLI 正文；它与原始输出是两个独立下载和权限面。
- 测试结束后清理 5 个报告对象、1 个原始归档、ReportManifest、批次事实及一次性账号。

该证据关闭场景 34 的工程集成门禁。夹具使用历史 `lixian` 文件验证受控交付协议，不表示执行了
当前在线巡检，也不证明甲方已对报告内容签发；这两项仍分别受场景 32 和场景 28 的门禁约束。

### 3.17 RuntimeBundle 控制面对账与修复闭环

命令：

```powershell
venv\Scripts\python.exe scripts\test\runtime_bundle_control_plane_full_stack_e2e.py
```

证据：

```text
.tmp/runtime-bundle-control-plane-full-stack-e2e/summary.json
```

验收确认：

- 真实 Django BFF、FastAPI 和 PostgreSQL 返回 Active Catalog 的 20 个检查项、39 个实现对；
  以每页 7 条连续读取 6 页，39 个稳定实现键无遗漏、无重复，并验证状态筛选、搜索和越界空页。
- 控制面健康摘要、数据库 Active `CheckItemRuntimeBundle` 和 Runtime Resolver 均为
  `0 ready / 39 missing`；该结果是数据库初始化后的真实生产状态，不使用事务夹具伪造 Active 资产。
- 隔离 `device.version` 实现可发现 Parser 与 Contract 子资产候选，但缺少完整 RuntimeBundle 时仍然
  判定未就绪；“子资产已发布/可选”不会被提升为运行时可执行。
- `configuration.running` 装配预检返回 `409` 和 `comparison_profile_missing`，请求前后
  RuntimeBundle、版本及关联记录数量完全不变。所有 blocker 均包含 `code`、`message`、
  `remediation` 和 `remediation_action`。
- 修复动作按问题性质分流：缺失新资产进入携带精确 `check_item_id` 的规则样本导入；已有资产
  不兼容进入生产资产工作台；多个兼容候选进入当前实现的装配选择。前端不再根据模糊错误文案猜测
  下一页。
- 真实浏览器打开 `configuration.running` 装配抽屉，确认缺失 ComparisonProfile、修复说明和
  “导入规则样本”动作可见，草稿保存与激活均禁用；点击后到达
  `/skills/parser?workspace=production&subtab=source-jobs&check_item_id=configuration.running`，
  页面提供单周期和双周期样本导入入口。
- 测试使用的隔离子资产、审计和账号均已清理，没有绕过人工职责分离，也没有改变生产 Active
  RuntimeBundle 数量。

该证据关闭场景 17、18、19 的工程门禁。它证明控制面能够诚实识别缺口并把工程师带到可执行的
下一步，不表示任何真实生产规则或 RuntimeBundle 已获人工批准。

### 3.18 设备身份歧义与 AssetGap 治理入口真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\asset_gap_identity_control_full_stack_e2e.py --timeout-seconds 180
```

证据：

```text
.tmp/asset-gap-identity-control-full-stack-e2e/summary.json
```

验收确认：

- 从真实 `lixian/Q2/XA-FOTIC-RT5620-1-10.34.2.219.txt` 提取 `display version`，经 BFF、
  MinIO、Celery 和 PostgreSQL 完成异步预览；夹具构造一个同管理 IP 候选和一个同设备名候选。
- 绑定结果保留两个有序候选及匹配依据，分别为管理 IP `0.95` 和设备名 `0.85`；系统不按最高分
  静默认领资产，而是以 `ambiguous_device_binding` 阻断预览和执行。
- 未确认身份时执行返回 `409`；人工选择精确 Inventory UUID 后，服务端在同一事务中更新绑定并
  重新计算门禁，任务立即由 `blocked` 变为 `ready`，无需重新上传或重新预览。
- AssetGap 读取只返回治理事实和服务端生成的唯一修复动作。浏览器实测“导入规则样本”进入
  `action=asset-gap-source` 深链，页面显示 gap、检查项、实现版本和命令，并自动打开已经预填名称
  与单周期用途的规则样本导入抽屉。
- 深链和抽屉只冻结待导入的证据范围，不创建离线任务、不读取 raw CLI 给模型，也不调度 Parser
  或规则生成。生产 Change 缺口直接创建规则任务被服务端以 `409` 拒绝。
- Parser Job、RuleGenerationJob、模型调用和 Workflow Run 在读取缺口、直接生成拒绝、未确认
  执行拒绝及身份确认后均分别保持 `58 / 59 / 31 / 10`，证明治理导航零生成副作用。
- 测试结束后离线任务、AssetGap、两个 Inventory 夹具、账号、文件元数据和 MinIO 对象全部清理。

该证据关闭场景 24 和 33 的工程门禁。它证明“发现生产缺口 -> 带上下文进入规则样本生产”和
“身份冲突 -> 人工选择 -> 立即重算门禁”两条操作链可用，但没有创建或激活任何生产资产。

### 3.19 检查项 Profile 与 Effective Plan 真实全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\check_item_profile_full_stack_e2e.py
```

证据：

```text
.tmp/check-item-profile-full-stack-e2e/summary.json
```

验收确认：

- 通过真实 BFF 创建仅包含 `configuration.running` 的检查项 Profile，重新列表读取后版本、
  检查项选择和状态保持一致。
- 影响预览在 PostgreSQL 保存审计证据，并使用隔离的 Active TextComparisonProfile 与
  RuntimeBundle 判定 1 台目标设备、1 个检查项全部 ready。
- Profile 激活后先创建 disabled 设备绑定，再显式启用；设备级 Effective Plan 随即返回精确
  Profile 版本、`configuration.running`、`display current-configuration` 和不可变
  Runtime Manifest ID/digest。
- 修复了设备 Effective Plan 端点过去只展示 legacy 命令策略、没有调用权威
  `CheckItemEffectivePlanService` 的缺陷；check-item Profile 现在以 RuntimeBundle 决策覆盖
  命令和就绪状态，legacy command-only 路径继续兼容。
- 修复了 PostgreSQL `JSON` 类型上使用 `=` 比较 selector 导致绑定创建失败的问题；同一
  Profile 的有限候选改为应用层结构化比较，并保留幂等 upsert。
- 测试结束后 Profile、绑定、设备、RuntimeBundle、ComparisonProfile、审计与临时账号全部
  清理；生产 Active RuntimeBundle 数量前后均为 0。

该证据关闭场景 8，并补齐场景 20 的 Effective Plan HTTP 工程证据。它不代表真实生产 Profile、
设备范围或 RuntimeBundle 已经通过人工审批。

### 3.20 风险恢复、整改验收与正式关闭全栈证据

命令：

```powershell
venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_assessment_runtime.py \
  tests\unit\test_check_item_reporting.py \
  tests\unit\test_patrol_risk_api.py -q --tb=short

npm --prefix web\react_frontend run test -- \
  --run src/components/devices/RiskCaseWorkspace.test.tsx --reporter=dot

venv\Scripts\python.exe \
  scripts\test\check_item_risk_closure_full_stack_e2e.py
```

结果为后端 `81 passed`、风险工作台组件 `3 passed`，Vite 生产构建通过；真实依赖纵切摘要位于
`.tmp/check-item-risk-closure-full-stack-e2e-episode-v2/summary.json`。验收确认：

- RiskCase 只有进入 `recovery_pending` 才生成待确认候选；候选由服务端计算，客户端不能提交
  “评估成功”、任意规则 ID 或拼接证据绕过门禁。
- 候选重新校验同一设备与检查项的成功 CheckExecution、健康且完整的 CheckAssessment、
  EvidenceQualification、CoverageDecision、冻结 RuntimeBundle 摘要及精确规则版本。
- recovery event 必须来自当前 Execution 的 source Change、同一实体和冻结 Bundle 中的恢复规则；
  current-state 健康路径必须存在匹配风险类型的冻结 CurrentStateRule。
- 覆盖不完整时页面展示服务端 blocker 并禁止确认；补齐证据后才出现可确认候选。确认使用事务锁和
  幂等键，重复请求不新增 RiskOccurrence。
- 确认后只形成“技术已恢复”，整改记录仍保持原状态；风险工作台明确提示技术恢复不等于整改完成
  或验收通过。
- 整改完成登记人与验收人必须职责分离；自验收返回 conflict，独立验收记录验收意见、证据、验收人
  和时间。
- 正式关闭要求技术恢复和整改验收同时成立；存在整改记录时不能以“无需整改”绕过验收。
- 恢复、整改验收和关闭动作均保留审计；同一幂等键重放只保留 1 条 resolved occurrence 和
  1 条 closed occurrence，关闭时间稳定。
- RiskCase 是长期业务身份，每轮发生到关闭使用独立 episode；Occurrence、Remediation 和动作
  幂等键均绑定 episode。领域回归证明关闭后复发并复用上一轮客户端幂等键时，会形成第二套独立
  整改、恢复、验收和关闭事实，不覆盖上一轮证据。
- CheckExecution、ReportManifest 和报告聚合优先使用当前 Change 关联的
  NetworkEventOccurrence；跨周期复用的 NetworkEvent 聚合身份不会把当前 recovery 错算到旧批次，
  也不会用旧 fault 投影替换当前恢复事实。
- 同一事实生成 Markdown 和十一表 Excel；“技术恢复与关闭”页分列技术状态、整改状态、关闭状态、
  关闭人、关闭依据、恢复证据和关闭证据。报告用途保持 `shadow_validation`，
  `production_network_claim=false`。

该证据完成场景 10 的工程集成层，但不把受控双周期夹具冒充真实生产网络。Active RuntimeBundle、
现场设备、新鲜 `production_current` 两周期、真实责任人和甲方签发仍按 `P/H` 门禁验收。

### 3.21 首巡、部分失败与 fallback 韧性真实全栈

可复现命令：

```powershell
venv\Scripts\python.exe scripts\test\check_item_patrol_workflow_full_stack_e2e.py `
  --timeout-seconds 240
```

证据：

- `.tmp/check-item-patrol-workflow-full-stack-e2e/summary.json`
- 测试以随机后缀创建隔离 H3C 检查项实现、Parser、Contract、ChangePolicy、EventRule、
  RuntimeBundle、Profile 和两台设备；通过真实 BFF 发起批次，由 Celery Worker 执行
  `patrol-change-event` Workflow。
- 成功设备通过本地 SSH 协议端点返回 `display cpu` 的 91% 样本，形成 1 Snapshot、1 canonical
  Observation、1 CurrentStateFinding 和 1 RiskCase；Assessment 为 `at_risk`。
- 失败设备连接到不可达端口，Workflow 失败但终态对账仍形成 `unable_to_determine` Assessment；
  成功设备结论不被失败设备覆盖，失败设备也没有被包装成健康。
- 两个 ComparisonContext 均为 `no_baseline / first_observation`；Change Detector 按冻结的
  `device + check_item + collection_action` 作用域抑制首观测伪变化，最终 Change 和 Event 均为 0。
- Effective Plan 只执行备用命令；主命令以 `asset_unavailable_at_plan_time` 留痕，两个目标对均为
  `fallback_resilience=reduced`。该事实进入 JSON、Markdown 和 Excel，不包含 collector 私密参数。
- Markdown、DOCX、XLSX、JSON 和证据包均从同一 ReportManifest 下载，字节摘要逐项一致；重复物化
  复用并回读校验已经挂载的不可变对象，不重写已冻结制品。
- 清理删除本次隔离对象、数据库夹具、临时用户和本地输出，并确认运行前后的 Active 资产集合一致。

该场景是 `shadow_validation` 工程夹具，`production_network_claim=false`。它完成场景 9、11 和 21
的工程集成证据，但不代表甲方现场当前网络已完成巡检，也不替代真实资产审批和报告签发。

### 3.22 ParserSpec 修订与重调度真实全栈

可复现命令：

```powershell
venv\Scripts\python.exe scripts\test\parser_ntc_candidate_full_stack_e2e.py `
  --temporary-user --exercise-revision --timeout-seconds 480 `
  --output .tmp\parser-spec-revision-full-stack-e2e\summary.json
```

该纵切使用 `lixian/Q2/SW1-10.34.2.11_2025-12-24_15-24-35.txt` 中真实
`display device manuinfo` 输出，通过真实 Django BFF、FastAPI、MinIO、Celery Worker、Workflow 和
PostgreSQL 验证：

- R1 故意要求样本中不存在的主键，任务进入 `failed`，稳定错误码为 `contract_conflict`；
- 同一 Parser Job 保存 R2 后重新调度成功，命中 NTC 9.1.0 模板；
- 保存 R3 后，R2 候选保留审计但状态变为 `rejected`，激活旧候选返回 `409`；
- 最终候选由 DeepSeek 初审，并通过生产 ParserRuntime 对 `1/1` 样本解析 8 条记录；
- 候选保持 `pending_approval`，`approval_ready=false`，唯一 blocker 是尚未接受人工语义复核；
- 临时用户、离线来源任务、Workflow、PatrolRun、Parser Job、候选、文件和 MinIO 对象全部清理，
  存活 Job 与候选均为 0。

证据为 `.tmp/parser-spec-revision-full-stack-e2e/summary.json`。该证据关闭场景 14 的工程集成门禁；
它不替代真实候选的人工语义判断和独立发布审批，也不声称新增了一次浏览器点击录像。

#### 3.22.1 Parser 生成步骤回退与历史结果浏览器复验

2026-08-06 在真实 Parser 控制面打开 `system.cpu_usage / display cpu / H3C MSR5620` 待审批候选，
完成以下只读浏览器复验：

- `样本识别 -> 语义方案 -> 生成与校验 -> 独立复核 -> 人工确认` 五个阶段均可直接进入；
- 支持“返回上一步”“下一步”和“回到当前进度”，查看历史阶段不会倒退服务端任务状态；
- 刷新页面后仍可读取 3 条持久化候选记录；展开候选可查看当轮 TextFSM、客观执行检查、
  3 条实际解析记录、错误诊断及独立 Reviewer 结论；
- 390 x 844 移动视口无横向溢出，恢复桌面视口后控制台无 error/warning；
- 全程未接受人工语义复核，未批准、发布或激活任何 Parser 资产。

该证据补充场景 3、4 和 14 的页面可诊断性，不替代 Parser 工程师对业务字段、主键、记录边界和
解析结果的最终判断，也不关闭场景 5 的独立发布门禁。

#### 3.22.2 规则资产步骤回退与历史结果工程复验

2026-08-06 完成规则资产生成详情的六阶段工作面回归：

- `来源与 Parser -> 安全上下文 -> 候选生成 -> 验证与回放 -> 工程复核 -> 发布审批` 均可直接进入；
- 支持“返回上一步”“下一步”和“回到当前进度”，历史导航不调用生成、修订、复核或审批接口；
- 阶段结果读取现有持久化 Bundle Step、AgentAttempt、EngineeringDraft、Replay 和 blocker，不建立前端
  第二套状态机；
- 候选生成阶段可查看每轮候选摘要、本地验证反馈和 revision request；验证阶段可查看
  `matched_change_count`、`collision_count`、估算事件量及 Workflow；复核和发布阶段分别展示职责主体与
  原子发布阻断；
- 规则资产后端聚焦回归 `136 passed`，Parser/规则资产七个前端文件共 `27 passed`，Vite 生产构建完成
  `3389 modules transformed`。

真实浏览器中的 `system.cpu_usage` 生产单元仍因 Parser 候选待人工审批而停在“尚未创建规则生成种子”。
这是预期的 fail-closed 结果，只证明缺种子状态和唯一下一动作投影正确；本轮没有为了演示填充态而批准
Parser、创建虚假 Bundle 或激活生产资产。因此，六阶段填充态当前记为 `component_verified`，真实浏览器
填充态继续标记 `blocked_by_parser_human_gate`，不得写成完整生产 E2E 已关闭。

#### 3.22.3 场景 39 自动基线工程单元纵切

2026-08-06 使用 `tests/unit/test_check_item_assessment_runtime.py` 的真实领域服务和 SQLite 持久化执行：

- 首周期通过 Effective Plan freeze、CommandSnapshot、EvidenceQualification、CoverageDecision 与
  CheckAssessment 完成 `healthy + complete + first_observation` 事实；
- 第二个 `production_current` 周期没有手工传入 `baseline_candidates`，仍自动选中上一周期唯一
  PatrolRun 和 Snapshot，ComparisonContext 为 `comparable / latest_compatible_success`；
- 把上一批用途改为 `shadow_validation` 后，候选以 `acquisition_purpose_ineligible` 拒绝；篡改
  EvidenceQualification digest 后以 `evidence_qualification_integrity_failed` 拒绝；
- CheckItem/canonical/identity/ObservationContract 事实合同摘要不一致时为
  `schema_incompatible / no_compatible_fact_contract`；同一最近时点两个合格事实为
  `baseline_missing / multiple_equally_recent_compatible_baselines`；
- 200 个目标的空历史批量读取只执行 1 条 SELECT，未出现按设备 N+1；
- `test_check_item_assessment_runtime.py` 共 `49 passed`，Ruff 定向检查通过，Alembic 只有
  `0091_comparison_baseline_idx` 一个 head。

本证据只关闭工程单元接线缺口。真实 PostgreSQL 与两个厂商/型号历史输出签名的集成矩阵由 3.22.4
继续关闭；新鲜生产双周期、真实 Active 资产和五类正式报告仍属于 `P/H` 层门禁。

#### 3.22.4 场景 39 真实 PostgreSQL 历史工程纵切

命令：

```powershell
venv\Scripts\python.exe scripts\test\check_item_comparison_context_full_stack_e2e.py
```

证据：`.tmp/scenario39-comparison-context-e2e/summary.json`。

2026-08-06 的纵切使用真实 PostgreSQL 与真实历史原始输出：

- H3C MSR5620：`lixian/Q2/Q3` 两台设备的 `display version`；
- Cisco NEXUS9000：桌面 Q1/Q2 两台设备的 `show version`，包含 UTF-16 文件解码；
- 每个精确 vendor/model/command 只有一个实现，同型号多设备复用同一实现，不按设备复制资产；
- 首周期四个 ComparisonContext 均为 `first_observation`，第二周期唯一合格候选为
  `comparable / latest_compatible_success`；
- 同一最近时刻存在两个候选时为
  `baseline_missing / multiple_equally_recent_compatible_baselines`，60 天旧候选为
  `baseline_stale / latest_compatible_baseline_expired`；
- Contract/RuntimeBundle 修订后为 `schema_incompatible / no_compatible_fact_contract`；
- 零实现与同优先级多实现都形成 `unsupported` 终态 Execution，不产生采集命令，也不从分母消失；
- 8 条终态 Execution、8 条 EvidenceQualification 和 16 条 StateObservation 完成对账；事务回滚后
  Active Bundle、Active Contract、Execution、Assessment 和 ComparisonContext 的生产基线完全不变。

同时新增时区等价摘要回归：同一时刻的 UTC `Z` 和 PostgreSQL `+08:00` 表示必须生成相同的
ComparisonContext/EvidenceQualification digest。相关聚焦结果为 Parser `55 passed`、
RuntimeManifest/Change/Event `52 passed`、Assessment/Reporting `88 passed`。

该场景关闭的是使用历史真实数据的工程 `I` 层，证据明确
`production_network_claim=false / source_observed_at_overridden=true`。它不证明甲方当前网络状态，不替代
真实工程师审批 Active RuntimeBundle、新鲜双周期、正式报告内容验收或独立签发。

### 3.23 CurrentAuthority 独立生产批次工程全栈

命令：

```powershell
venv\Scripts\python.exe scripts\test\patrol_current_authority_full_stack_e2e.py `
  --timeout-seconds 240
```

证据：`.tmp/patrol-current-authority-full-stack-e2e/summary.json`。

本纵切使用运行时生成、采集时间为当前窗口的 H3C `display current-configuration` 合成输出，调用真实
BFF、FastAPI、PostgreSQL、Celery Worker、Redis、MinIO 和报告 API。验证结果：

- 历史意图申请当前权限返回 `historical_intent_only`；
- 导入人自证返回 `same_actor_forbidden`；同键异内容返回 `idempotency_conflict`；
- prepare 后改写 Snapshot 正文但不改存储哈希，独立证明返回 `snapshot_content_hash_mismatch`；
- 恢复原字节后，不同 principal 完成证明，平台创建独立 `production_current` 候选批次；
- 并发 prepare/attest 只形成一条有效资格和候选批次；重复投递同一 Worker 任务前后 Execution、
  Assessment、Qualification、Manifest 和制品计数完全一致；
- 冻结范围包含两台设备，四个 Workflow Step 全部 completed；成功设备形成
  `completed + informational + complete + first_observation`，命令失败设备形成
  `comparison_failed + unable_to_determine + insufficient`，失败目标没有从分母消失；
- Evidence 中保留外部设备名用于展示，但跨事实关联使用冻结 Inventory UUID；外部别名与 Inventory
  身份不同仍得到 `terminal_truth_reconciled=true`；
- ReportConclusionPolicy 得到 `current_conclusion_ready=true`、全目标 `unable_to_determine=1` 和
  `overall_healthy_allowed=false`，证明非健康类检查项失败也不能被包装成整体健康；
- Markdown、DOCX、XLSX、JSON 和证据包逐一下载，字节 SHA-256 与 Manifest、MinIO 对象三方一致；
- Manifest 为 `facts_status=final`、`release_status=pending_signoff`，证明事实冻结不等于正式签发；
- 撤销后审计状态保留，测试数据库运行资产恢复、上传对象删除。

该证据的 `production_network_claim=false`，关闭场景 36 的 `I` 层后端真实依赖集成，以及 W0.4b
中的并发、重投递和范围受限失败子项。后续 3.24 关闭真实浏览器双账号正向链与空计划失败关闭，
3.25 关闭批准后故障投影、整页刷新和自动恢复，3.26 关闭冻结资产漂移合同。
真实设备、人工批准生产资产、真实职责主体和甲方签发仍属于 `P/H` 层，不能被本夹具替代。

### 3.24 CurrentAuthority 真实浏览器阶段证据与空计划校正

2026-08-01 在已启动的本地完整服务栈中，使用真实浏览器完成：创建 `current_attestation` 离线任务、
上传新鲜工程 CLI、预览与执行、将外部身份唯一绑定到 Inventory、重新执行、提交冻结证明、切换独立
管理员账号复核。Celery Beat 在无人工调用后端任务的情况下自动处理批准请求，证明 Beat/Worker 调度链
实际在线。该证据仅是 `production_network_claim=false` 的工程夹具，不代表现场网络。

浏览器随后暴露真实缺陷：目标检查项 `configuration.running` 没有 Active RuntimeBundle，冻结 Manifest
正确返回 `blocked_asset_missing / active_runtime_bundle_missing`，但旧状态机把 1 个目标、0 个启动、
1 个跳过错误写成 `completed`，页面同时显示“执行已结束”和“尚未创建设备 Workflow”。7.9 校正后：

- resolved 目标必须引用真实 collection action；未知状态、悬空 action 和动作缺失均 fail closed；
- 整批无 action 且存在资产/实现阻断时返回 `authority_production_plan_incomplete`；
- 冻结 RuntimeManifest ID/摘要仍写入失败批次，设备保持已选并记录 `preflight_status=failed`；
- 页面显示“没有执行，尚未形成网络结论”，并提供“前往规则资产控制面”；
- 部分目标可执行时仍继续，阻断目标以 `blocked_asset_missing + unable_to_determine` 留在分母；
- 定向后端 `13 passed`、前端 `21 passed`，Vite 生产构建转换 `3387` 个模块。

服务重载后，独立第三方管理员重新复核同一工程任务，Celery Beat 自动形成批次
`patrol-current-offline-8a689832778b59478dcef641`。浏览器观察到“执行失败”“当前范围没有可执行的生产
规则资产”“尚未创建设备 Workflow”，并成功进入携带来源任务与数据集参数的规则资产修复深链。数据库
对账同时确认：request/batch 均为 `failed`，`dispatch_error_code=authority_production_plan_incomplete`，
`target_count=1`、`launched_count=0`，冻结 RuntimeManifest ID/摘要存在；目标保持 `selected`，其 preflight、
dispatch 和 failure code 均为失败，且 `workflow_run_id` 为空。

本节当前为 `browser_defect_reproduced / browser_fail_closed_verified`。空计划 fail-closed 子项已关闭；
批准后刷新与故障恢复由 3.25 关闭，冻结资产漂移由 3.26 关闭。

### 3.25 CurrentAuthority 生产准入与基础设施恢复证据

8.1 将已有 `PatrolProductionReadinessService` 接入 CurrentAuthority 两个位置：独立复核页在批准前展示
Worker、Beat、数据库、队列、MinIO 和真实 Workflow 检查，并在 `approved/attesting` 等待调度阶段继续
显示阻断与轮询恢复；Worker 冻结 EffectivePlan 前再次校验，直接调用 API 或状态竞态均不能绕过。
检查对象固定为真实 `patrol-attested-offline-current` Workflow；恢复任务每轮只计算一次准入快照，并供
本轮最多 100 个请求共享。基础设施暂不可用时整轮请求被延后，资格保持 `approved`、批次保持
`awaiting_current_plan`，不创建 Workflow 或设备失败事实；证据篡改、范围漂移、空计划等业务错误仍终态失败。

真实进程故障演练结果：

- 停止本项目 Celery Beat 并等待 45 秒心跳窗口后，准入为 `blocked`，错误码为
  `patrol_celery_beat_not_ready`，原因明确为心跳 `stale`；
- 恢复 Beat 后新心跳进入 Redis，状态恢复 `ready`；
- 保持 Beat 运行但停止 Worker 后，准入同时返回 `patrol_celery_patrol_queues_not_ready` 和
  `patrol_celery_beat_not_ready`，证明系统没有把“Beat 进程存在”误当成调度链可用；
- 恢复 Worker 后 `netops.patrol` 重新出现消费者，Beat 心跳恢复 fresh，整体再次为 `ready`；
- solo Worker 执行恢复任务时，远程 `inspect` 会等待当前任务自身而产生假阴性；8.1 只把当前任务实际
  delivery queue 视为该队列的可信消费者，不推断其他队列，API 侧仍执行完整远程检查；
- 真实浏览器请求 `240f986e-1c7c-52e5-a7e9-115fb136ae41` 在批准后观察到 Beat stale，整页刷新后仍为
  “复核通过，等待计划”。手工触发同一个恢复任务得到 `prepared_count=0`、`deferred_count=1`，数据库
  保持 request=`approved`、batch=`awaiting_current_plan`、设备=`awaiting_current_plan` 且无 Workflow；
- 恢复 Beat 后由周期任务自动续跑。由于工程环境没有 Active RuntimeBundle，最终按真实业务阻断转为
  `authority_production_plan_incomplete`，`launched_count=0` 且仍无 Workflow；页面不再显示平台不可用，
  而是给出规则资产修复入口。临时证明资格已撤销，测试账号已删除；
- 后端 CurrentAuthority、重放、恢复任务、生产准入与心跳组合 `31 passed`；前端批准前阻断、修复说明、
  同页重新检查及批准后持续反馈与离线任务组合 `21 passed`，生产构建转换 `3387` 个模块。

本节状态为 `infrastructure_fault_recovery_verified / browser_fault_projection_verified`。工程层故障投影、
刷新保持和恢复续跑已经关闭；真实现场可用性仍由 W0.5 独立验收。

### 3.26 CurrentAuthority 冻结资产漂移合同

资产漂移不以“当前 Active 是否改变”笼统判定，而以计划冻结边界区分：

- Effective Plan 冻结前，Resolver 可以选择当时唯一合格的 Active RuntimeBundle；
- Effective Plan 冻结后，本批次只读取 Manifest 中精确的 Bundle version ID、Parser、Contract 与规则版本；
- 冻结后切换 Active RuntimeBundle，不得改变本批次解析或规则结果，也不得静默升级；
- 冻结 Manifest 缺失、签名/摘要篡改、Bundle version 不存在或内容摘要不一致时，必须在 Parser 前以
  `runtime_manifest_invalid` 失败，不得回退当前 Active、legacy 资产或留下部分 Observation/Change/Event。

定向回归证明执行计划携带冻结 Manifest 身份，Active 切换后仍使用精确冻结 Bundle version ID；缺失或
篡改的冻结 Manifest 返回稳定错误码。CurrentAuthority 组合 `31 passed`，Runtime Manifest 签名、Bundle
摘要与防篡改定向 `6 passed`。本节状态为 `frozen_asset_drift_engineering_verified`。

### 3.27 Parser 候选不可变样本证据与重试生命周期

2026-08-02 对真实本地 PostgreSQL 执行存量审计与迁移，证据层为 `U + I(database)`：

- 审计发现原 4 个 `pending_approval` 候选没有候选到样本的数据库关联；其中两组 Job 当前样本已与
  审批包 Manifest 不一致，另外两组已无存活样本。继续审批会把旧 Candidate ID 解释为新证据。
- `0080_parser_candidate_sample_evidence` 建立精确关联，并把全部缺少 v1 证据的旧未发布候选拒绝；
  Active Parser 仍为 3，没有用迁移替换生产运行时。
- `0081_parser_job_sample_count_integrity` 对账存量 Job，并由数据库触发器维护 current 样本计数。
- `0082_parser_sample_lifecycle` 将离线重试的非权威 PatrolRun 样本标记为 `retired`；历史样本不删除，
  但不会进入 ParserSpec、readiness、LLM、验证或新候选。
- 候选创建、语义复核、审批包导出和激活统一校验 sample link、角色/期望、sanitized digest、Snapshot
  raw digest 与当前样本集合；Dataset 中额外 Snapshot 不会改变审批结果。
- 定向后端组合 `150 passed`，Alembic `current` 为 `0082_sample_lifecycle`。

迁移后首次只读对账为 Active Parser `3`、Pending Approval `0`、Rejected `15`、
CandidateSampleEvidence `0`、Active RuntimeBundle `0`。随后真实 Worker 已从当前权威样本重新生成
CPU、接口状态和 MSR absent-only 三个候选；当前只读对账为 Pending Approval `3`、
CandidateSampleEvidence `3`，Active Parser 和 Active RuntimeBundle 仍分别为 `3/0`。三者都没有完成
人工语义复核；MSR 候选只证明光模块存在性，不能声明光功率 ready。

### 3.28 S6850 多记录集光功率生成实测

2026-08-02 从 `lixian` Q2/Q3 选择两台 S6850-56HF 的四份真实
`display transceiver diagnosis interface` 输出，经真实规则样本 API、MinIO、Workflow、PostgreSQL 和
Celery 创建 Job `fdee94d2-4bc5-4f55-9b55-99e7569c6ca9`：

- 四个 current 样本覆盖两设备、两周期和两个输出结构签名，ParserSpec v2 已确认。
- LLM 正确提出接口状态、模块诊断/阈值、通道测量三个 record set，不需要命令或型号硬编码。
- 第一轮模型调用耗时 421328 ms，31003 prompt tokens、29017 completion tokens；返回 Bundle 可编译、
  可执行，但正样本完整性和主键校验失败，ModelCallLedger 为 `validation_failed`。
- 第二轮模型调用已预留，外层 Skill 在总计 600 秒处终止子进程；Job 最终为
  `skill_execution_failed`，第二条 ledger 残留 `reserved`，GenerationAttempt 为 0。
- 没有 Candidate、CandidateSampleEvidence、Active Parser 或 RuntimeBundle 被创建或激活。

本场景状态为 `multi_record_spec_verified / generation_operability_blocked`。它证明模型能够识别多粒度业务
结构，也证明当前任务边界尚不满足生产：下一步必须实现原子 ParserBundle、逐轮候选/验证持久化、阶段
心跳、显式总 deadline、超时 reservation 回收和独立 `netops.ai` Worker，再使用同一不可变四样本重试。
这是修复前的历史失败证据；其工程阻断由 3.29 关闭，但原失败 ledger 和诊断继续保留。

### 3.29 S6850 ParserBundle、规则链与 RuntimeBundle 闭环

同一 Job `fdee94d2-4bc5-4f55-9b55-99e7569c6ca9` 在修复逐轮持久化、固定 deadline、阶段心跳、
reservation 回收和独立 AI Worker 后，以 ParserSpec R6 重跑成功：

- 候选 `4fe4184d-e9c7-4a72-95c6-4ee7ae08def6` 使用 4 份不可变样本，输出 5 个具名 record set、
  508 条记录；生产 ParserRuntime parity 与激活治理检查通过。
- `parser-candidate-approval-pack/v2` 同时提供脱敏原文、ParserSpec、每个 TextFSM 模板、按 record set
  的逐样本解析表、主键/空值/重复键诊断和独立 Reviewer 结论；状态为 `ready_for_human_review`，
  `production_activation_performed=false`。
- sanitized 规则链 dry-run 只读取 4 个结构化样本组、508 条记录，`raw_cli_included=false`；形成
  presence、Rx、Tx 三个 Policy 和 6 个 EventRule。`Alarm Low` 来源证据将弱模型的 `low` 校正为
  `critical_low`，因此没有生成无证据的 Warning Low 事件。
- 事务 RuntimeBundle 使用相同候选形成 524 条 Observation、46 条 Change、4 条周期事件和 4 条当前
  事件；历史回放实际命中 4 条 Change，`collision_count=0`。
- 单个周期不足以形成可靠事件日流量，因此估算输出明确为 `one_day_floor`、
  `estimated_daily_event_count_reliable=false`、`sparse_history=true`，不会把 4 条样本事件冒充稳定日均值。
- 外层事务回滚前后生产计数均为 Active Parser 3、Contract 3、Policy 11、EventRule 11、
  RuntimeBundle 0；没有绕过人工审批。

可复现证据：

```text
.tmp/parser-candidate-approval-pack-s6850-r6/manifest.json
.tmp/s6850-r6-rule-chain-dry-run-v7.json
.tmp/s6850-r6-runtime-replay-v3/summary.json
```

本场景状态为 `parser_bundle_verified / rule_chain_candidate_verified /
runtime_replay_verified / blocked_by_human_gate`。它关闭多记录集生成的工程阻断，但不关闭 Parser 人工
语义复核、独立发布、正式规则资产审批、Active RuntimeBundle、新鲜现场巡检或甲方签发门禁。

### 3.30 五条黄金结果模式的发布前历史回放

2026-08-02 在不激活生产资产的前提下，继续使用 `lixian` 完成另外四条结果模式，并与 3.29 的
S6850 光功率证据合并核对：

- `system.cpu_usage`：候选 `3182de57-1e15-4faf-b52e-596b350929f3` 使用两台 MSR5620 的 Q2/Q3
  四份真实样本，形成 36 条 canonical Observation 和 2 条真实 Change。样本没有跨越 CPU 告警阈值，
  因此周期 Event 与当前 Event 都保持 0；`CpuUtilizationHigh/Recovered` 只通过合成边界可达性，不能
  被写成现场事件。
- `interface.oper_status`：候选 `9b79b7db-a3f2-4753-82e4-fcdd27edac76` 使用同一四份样本形成 72 条
  接口 Observation，空描述接口没有丢失；真实周期为 0 Change/0 Event，`InterfaceDown/Up` 仅完成
  合成边界可达性。
- CPU 与接口的历史事件日流量均输出 `one_day_floor`、
  `estimated_daily_event_count_reliable=false` 和 `sparse_history=true`；0 事件不再被包装成健康。
- `device.version`：Q2/Q3 两次事务纵切均由现有 Active Parser 解析 12 条原生记录，投影为
  `device.hardware.model=MSR5620` 与
  `device.software.version=7.1.064, Release 0821P18`。Assessment 为 `informational/complete`，报告用途为
  `offline_historical`，当前权限关闭。
- `configuration.running`：从 S6850 SW1 Q2/Q3 的真实 `display current-configuration` 输出读取采集周期，
  使用 `configuration.ordered_lines` 比较；结果为 41 行新增、4 行删除、1 条 normalized Change、0 Event，
  并形成一行 `configuration_change@1` 报告投影。脱敏预览只保存摘要，结果文件不包含 raw CLI。
- 运行配置 RuntimeBundle 只包含 `comparison_profile + identity_policy`，没有伪造 Parser、Contract、
  ChangePolicy 或 EventRule。所有临时 Profile、Bundle、Snapshot、Change 和报告事实均位于外层事务，
  回滚前后生产数据库计数一致。

可复现证据：

```text
.tmp/cpu-msr5620-runtime-replay-v1/summary.json
.tmp/interface-msr5620-runtime-replay-v1/summary.json
.tmp/device-version-lixian-e2e-20260802-q2/summary.json
.tmp/device-version-lixian-e2e-20260802-v1/summary.json
.tmp/configuration-s6850-runtime-replay-v1/summary.json
```

本场景状态为 `five_golden_pre_release_replay_verified / production_assets_unchanged /
blocked_by_human_and_current_evidence_gates`。它证明五种结果能够汇入统一 Execution、Assessment 和报告
体系，但不会把候选资产变成 Active，也不代表当前网络状态、甲方内容验收或正式签发已经完成。

### 3.31 报告对象完整性与千条内容验收恢复性

2026-08-02 使用真实 `lixian/Q2`、BFF、FastAPI、Celery/Redis、PostgreSQL 和 MinIO 对场景 37 完成
最后两项工程补证：

- 内容验收和签发不再只比较 Manifest 中保存的摘要，而是流式读取五类对象实际字节并重新计算
  SHA-256。删除 evidence 对象时 start 返回 409 且状态为 `object_missing`；替换 evidence 字节时 preview
  返回 `digest_mismatch`；内容已接受后替换 Markdown 字节时 release 返回 409。恢复原字节后 v1/v2
  内容接受、独立签发和精确下载继续成功。
- 单独的规模夹具冻结 1000 条复核事实。真实 BFF/API 第 1 页和第 20 页各返回 50 条且集合不重叠；
  同一 50 条决定使用相同幂等键重放后计数仍为 50；关闭会话、重新登录后从服务端恢复并推进到
  1000/1000，最终 `pending=0`、`can_accept=true`。
- 真实浏览器在 1280×720 视口打开同一批次：首屏只渲染 50 条，第 20 页读取
  `assessment-0950..0999`，弹窗与表格 `scrollWidth == clientWidth`；整页刷新后仍恢复原 0/1000 任务、
  1000 条总数和首页 50 条，不会重建另一份清单。
- 两个夹具分别移除 5 个与 10 个对象，并清理 1000 条复核项、Review/Release、Manifest、批次事实和临时账号；
  两条脚本均 `success=true`，没有留下测试生产资产。

可复现证据：

```text
.tmp/report-revision-release-full-stack-e2e/summary.json
.tmp/report-content-review-scale-full-stack-e2e/summary.json
```

本节状态为 `artifact_integrity_fault_matrix_verified / content_review_scale_verified /
browser_recovery_verified / real_human_pending`。场景 37 的工程 `I` 层已经关闭，但真实甲方内容接受与
独立正式签发仍是不可自动化的 `H` 层门禁。

### 3.32 Parser 候选能力声明与统一审批包

2026-08-02 对当前 4 个黄金 Parser 候选执行只读导出与能力对账，没有审批或激活任何资产：

- 统一审批包包含 4 个候选、7 份不可变 CandidateSampleEvidence 和 534 条客观解析记录；所有候选的
  TextFSM 重放结果与生产 ParserRuntime 完全一致，候选仍为 `pending_approval`。
- `system.cpu_usage` 为 `identity + metric`，`interface.oper_status` 为
  `identity + state + context`，S6850 光功率为 `identity + state + metric + threshold`；三者字段形态
  可承载目录目标，但在 Contract 审批前均保持 `mapping_unverified`。
- MSR5620 absent-only 光模块候选只有 `identity + state`，对目录要求的 gauge 观测及结构化阈值引用
  缺少 `metric + threshold`，因此
  为 `parser_shape_incomplete`；它仍可按 absent 输出结构进入 Parser 人工复核，但不能满足
  `interface.optical.rx_power/tx_power` 或完整光功率 RuntimeBundle。
- `canonical_candidate` 只作为未复核提示输出；审批包、API 和 UI 都不把它计为 ObservationContract
  映射。目标 `check_item_id` 还需与 Active Catalog 的厂商、型号、命令实现范围对账。
- 前端审批抽屉新增“能力边界”页签，显示目标标准观测、Parser 字段角色、缺失角色、mapping 状态和
  稳定阻断码；治理提示不改变 Parser 客观复核门禁，也不提供自动生产激活入口。

可复现证据：

```text
.tmp/parser-candidate-approval-pack-current-20260802/manifest.json
.tmp/parser-candidate-approval-pack-current-20260802/draft-target-matrix.json
```

自动验证：后端相关 `81 passed`，前端审批抽屉 `6 passed`，Vite `3387 modules transformed`。本场景状态为
`parser_evidence_verified / capability_boundary_verified / canonical_mapping_pending /
production_assets_unchanged`。

### 3.33 Parser 字段契约与 Contract 源引用完整性

2026-08-02 在 3.32 的四个真实待审批候选上继续执行只读对账，没有审批或激活资产：

- `parser-field-contract/v1` 逐 record set 复编译实际 TextFSM/ParserBundle，并与冻结 ParserSpec 字段
  集合对账；数据库中 `3 active + 4 pending` 共 7 个 Parser 全部为 `aligned`。其中四个候选、7 份
  CandidateSampleEvidence、534 条解析记录形成新的可复现审批包。
- comparison API 和人工审批抽屉展示字段契约；生成型候选出现 `schema_drift`、`parser_spec_missing` 或
  `template_invalid` 时不能进入可复核状态。语义正确性仍由 LLM 初审或工程师直审，不由该门禁替代。
- RuntimeBundle 装配新增 Contract 源引用对账，覆盖主键、必填/可选字段、validator、identity
  dimension、transform、threshold reference、record-set join key 与 threshold binding。
- 使用 S6850 R6 的五 record-set 候选和 sanitized 光功率 Contract 真实复验：字段契约为 `aligned`，
  Contract 源字段 blocker 为 `0`。复验同时发现并修正 `target_field` 作用域误判：它是阈值到测量字段的
  语义关联提示，不是阈值 record set 的本地输出列；跨表执行引用由显式 relation 决定。
- 负向测试证明 ParserSpec/TextFSM 漂移和 Contract 引用不存在字段均 fail closed；合法 source-to-
  canonical identity transform 与跨记录集阈值关联仍可通过。
- 同一负向回归同时核验修复归属：`parser_field_contract_drift` 返回 `repair_parser_asset`，
  `observation_contract_source_field_missing` 返回 `repair_observation_contract`；两者均保留具体字段、
  record set 和资产类型上下文，不使用无归属的“重新生成全部”。
- 审批包位于 `.tmp/parser-candidate-approval-pack-current-20260802-field-contract-v1`，Manifest 明确
  `parser_field_contracts_aligned=true`、`check_item_runtime_ready=false`、
  `production_activation_performed=false`；数据库只读核验 Active RuntimeBundle 仍为 `0`。

本场景状态为 `parser_field_contract_verified / contract_source_references_verified /
semantic_approval_pending / production_assets_unchanged`。它关闭的是执行契约完整性，不替代真实人工
Parser/规则审批、新鲜生产 current、双周期事实或甲方签发。

### 3.34 Superseded 文档首屏告警与权威后继校验

2026-08-02 对文档治理执行了完整整改和严格审计：

- 注册表中的 20 份 `superseded` 文档均在前 20 行声明
  `<!-- document-status: superseded -->`，并链接其 `superseded_by` 指定的唯一权威后继文档。
- 旧 Parser 指南明确说明 `parser-validation.db` 已退出运行合同；历史“最终设计”“已落地”和容量估算
  均在首屏说明不能代表当前实现、生产能力或完成状态。
- `scripts/docs/audit_docs.py` 新增状态告警检查，同时拒绝缺 marker、后继链接错误，以及 Active/Proposed
  文档错误携带 superseded marker。
- `tests/unit/test_docs_governance.py` 共 5 项通过；严格全库审计结果为
  `governed=75 / active=37 / superseded=20 / archived=13 / unregistered=0 /
  broken_links=0 / index_status_violations=0 / status_banner_violations=0`。

本场景状态为 `document_governance_done / compatibility_code_removal_gated`。它消除的是过时文档误导，
不证明兼容代码已经零流量或可以物理删除；后者继续等待真实遥测、写入、替代覆盖和回滚演练证据。

### 3.35 批次取消与晚到回调的工程部分闭环

2026-08-02 完成场景 40 的第一层实施，没有启动或激活任何生产 RuntimeBundle：

- 新增批次级取消 API 和 BFF 路由，取消原因必填且记入审计语义；重复取消是幂等操作。
- 批次先冻结后续分发，Workflow Run 与未完成 step 在同一事务内进入 `cancelled`；
  `completed/failed/cancelled` 终态拒绝后续状态和 step 结果覆盖。
- Celery 采用协作式 `revoke(terminate=false)`，子 Workflow 递归取消；任务真正进入 Skill 前再次检查
  父 Run 终态，已取消运行不再执行业务 Skill。
- 未分发和未完成目标保留在批次分母，进入 `cancelled`。检查项 Finalizer 尝试将其结算为
  `cancelled + unable_to_determine`；结算失败会显式返回 `finalization_errors`，前端使用警告而非成功提示。
- 历史表和详情页共用同一可取消状态函数，覆盖“尚未创建 Workflow、仍在待分发”的批次。
  浏览器中的确认框明确告知停止分发、未完成项无法判断和已完成事实保留；本次只打开后
  退出，未对现有批次执行取消。临时 UI 验证批次及目标记录已清理。

可复现命令与结果：

```text
pytest test_workflow_cancellation + workflow_parallel + workflow_plugins
       + patrol_batch_manifest + device_patrol_runner
=> 56 passed

pytest tests/integration/test_patrol_runtime_fault_injection.py
       -k cancelled_patrol_run_rejects_late_success_callback
=> 1 passed (real PostgreSQL)

manage.py test bff.tests.PatrolBatchDownloadRouteTests
=> 3 passed

vitest patrolBatchLifecycle.test.ts
=> 3 passed

eslint (4 touched frontend files) + vite build
=> passed / 3388 modules transformed
```

本节是 2026-08-02 的首轮证据，后续由 3.38 补充。当前仍不能把场景 40 标记 `done`，但 Worker
硬终止/重投递、完整分母、五类制品和千台批量结算已经不再属于缺口；剩余门禁是预发布真实设备断连、
实际命令超时、串并行任务中断和千台在线取消压测。

### 3.36 风险 Episode 关联与领导问题分账

2026-08-02 完成场景 41 的工程主链，未创建或激活任何生产规则资产：

- 迁移 `0085` 建立 `RiskEpisodeAssociation`，绑定精确主/从 `risk_case_id + episode_id`；数据库和服务
  同时拒绝自关联、同一活动关联重复、同一症状多主、循环和链式拓扑。
- 状态机固定为提议、独立批准/拒绝和撤销。提议人不能复核自己的建议；风险关闭后复发产生新 episode，
  旧关联自动成为 stale，不再影响新报告。
- ReportManifest 冻结关联事实，ClaimLedger 为主问题生成稳定 Claim；Markdown、DOCX、XLSX、JSON
  和证据包都读取同一个 `risk_issue_projection`。两条 RiskCase、两条 EventOccurrence 和全部证据继续
  保留，领导问题数可以在批准后从 2 变为 1，影响设备、实体、事件次数分别列示。
- 风险详情页新增关联提议、独立批准/拒绝、撤销和过期提示。提议账号不显示自批按钮；没有批准关联时
  页面明确说明报告按独立问题计数。
- 旧 `leadership_report.py` 已删除“接口 Down 时自动把光功率风险作为关联证据”的领域启发式。旧报告
  也只能消费冻结的已批准 RiskEpisode 关联，不再按命令、事件名或当前状态猜根因。

可复现命令与结果：

```text
pytest test_patrol_risk_api + test_risk_episode_association
       + test_risk_issue_projection + test_check_item_reporting
       + test_report_conclusion_policy + test_report_content_review
       + test_report_manifest_transaction
=> 88 passed

pytest tests/unit/test_leadership_patrol_report.py
=> 20 passed

vitest RiskCaseWorkspace.test.tsx
=> 4 passed

vite build
=> passed / 3388 modules transformed

alembic current
=> 0085_risk_episode_association

PostgreSQL cleanup audit
=> Active RuntimeBundle 0 / RiskEpisodeAssociation 0
```

本节状态为 `engineering_done / report_and_ui_verified / network_sme_policy_pending`。自动化证明的是状态机、
失败关闭、计数和制品一致性，不能替网络 SME 判断真实两个风险是否属于同一物理问题，也不能替甲方对
新鲜 `production_current` 报告进行内容验收和独立签发。

### 3.37 `lixian` v3 双 RuntimeBundle 灰度与 TextFSM 输入鲁棒性

2026-08-08 使用当前 `lixian/Q2,Q3` 对 CPU、接口状态和 S6850 光功率执行受信任的
`rollout-target/v3` 历史影子回放。每个检查项分别创建事务内 baseline/candidate
CollectionImplementation 与 RuntimeBundle，共冻结 6 个设备检查项目标、12 份 Snapshot 和 12 份隔离
Projection；数据库事务全部回滚，MinIO 临时证据已删除，生产 Active 资产和路由均未改变。

首次执行暴露 S6850 当前 Q3 输出的模块测量行包含行尾填充空格，候选模板使用 `$$` 行尾锚点时跳过真实
数值行，随后把 `Alarm thresholds:` 错捕获为温度和电压，最终以
`field_validation_failed:module_diagnostic_measurements` 失败。平台在统一
`prepare_textfsm_input()` 层删除每行末尾空格与 Tab，同时保留行首缩进、列间距和换行结构；该策略对
厂商、型号和命令无感，并由四项定向测试覆盖。修复后：

- 黄金回放形成 632 条 Observation、48 条 Change 和 4 条真实周期 Event，生产数据库不变；
- v3 双轨的 baseline/candidate Parser 执行成功率均为 100%，RuntimeBundle 覆盖率为 100%；
- CPU 两设备形成 `healthy/complete`；接口状态因缺少已批准 expected-state/coverage decision，光功率因
  设备能力适用性和 coverage decision 未确认，均保持 `unable_to_determine`；
- Campaign 因 66.67% 目标不可判断而 `paused`，阶段尝试为 `failed`。结果合同明确拆分为
  `execution_evidence_complete=true`、`rollout_eligible=false`、`acceptance_status=fail_closed`，不再用
  单一 `success=true` 暗示可以发布；
- RuntimeBundle assessment contract 与目标快照现已共同包含 CheckItemVersion applicability，非空适用性
  不再产生伪漂移。

可复现证据：

```text
.tmp/check-item-golden-runtime-replay-20260808-after-input-normalization/summary.json
.tmp/check-item-rollout-stage-lixian-e2e-20260808-v3-final/summary.json
```

本场景状态为 `dual_track_execution_verified / parser_robustness_fixed /
rollout_fail_closed_by_policy_readiness`。它关闭历史样本执行层缺陷，但不替代 expected-state、coverage、
applicability 的工程师配置与独立审批，更不构成真实设备 `live_capture` 或生产发布证明。

### 3.38 取消与分发失败目标的报告分母结算

2026-08-08 继续补齐场景 40 的报告事实合同。此前批次取消只能结算已经创建 Workflow Run 的设备；
尚未分发的取消目标以及登录预检失败、未创建 Workflow Run 的目标虽然留在设备分母中，但对应
`CheckExecution` 没有终态 `Assessment`，因此终态报告请求会一直等待。这不是展示问题，而是报告完整性
缺陷。

本次修复后：

- RuntimeManifest 中所有非 skipped 目标都会参加终态对账；取消目标结算为
  `cancelled + unable_to_determine`，登录或分发失败目标结算为
  `collection_failed + unable_to_determine`；
- 已完成设备的事实和 Assessment 保持不变，失败目标的机器原因码继续进入报告阻断原因；
- 没有 Workflow Run 的 `cancelled/failed` 目标被报告门禁识别为合法终态，但 running/pending 目标仍
  fail closed；
- 晚到成功、晚到失败以及 Run 终态覆盖均被拒绝；重复取消和重复报告请求不重复创建 Assessment；
- Workflow 不再把设备连接失败、命令超时和 Worker/队列超时统一压平成 `workflow_failed`。稳定失败码从
  Skill/Celery Retry 包装透传到 WorkflowRun、设备目标、CheckExecution、Assessment 和报告；自由文本错误
  仍单独保存，非法机器码降级为 `workflow_failed`；
- CheckItem Finalizer 现在按 `collection_action_id` 批量读取命令级 `PatrolCollectionResult`，在进入
  Parser/Comparison 归因前先判定采集事实。所有 fallback 均失败时形成
  `collection_failed + command_timeout/command_failed`；主命令失败但备用命令完成时继续按备用输出的真实
  Parser 结果结算；命令已完成却没有对应 Snapshot 时形成 `snapshot_persistence_failed`。因此“没有可选
  Snapshot”不再一律被结构化检查项误报为 Parser 失败；
- 失败 Workflow 的 Execution 阶段由“稳定失败码优先、实际失败 Skill 兜底”确定：`device-patrol`、
  `state-normalizer`、`change-detector`、`event-builder` 分别对应 collection、normalization、comparison、
  assessment；`command_timeout` 等明确原因不会被后续步骤名称覆盖。Normalizer/Parser 失败会从已完成
  patrol 步骤读取 `snapshot_run_id`，先冻结已有 Snapshot 的 EvidenceQualification，再形成
  `not_evaluable` CoverageDecision；没有任何采集结果或 Snapshot 时回落为
  `collection_evidence_missing`，不会伪造后置失败；
- 终态收敛由逐设备、逐检查项事务改为批量查询和单事务写入，避免千台设备场景按目标数量线性增加
  数据库事务；Parser/Normalizer 失败仍走覆盖率对账专用路径，不被通用批量失败接口吞并；
- 真实 PostgreSQL + MinIO 集成用例验证 3 个目标、3 个 Assessment 和五类报告制品，其中一个已完成、
  一个取消、一个凭据失败；报告分母为 `3/3`，两项失败均如实为 UTD。

可复现结果：

```text
pytest tests/unit/test_workflow_cancellation.py
       tests/integration/test_patrol_runtime_fault_injection.py
       -k "cancell or dispatch"
=> 7 passed

pytest tests/unit/test_patrol_batch_manifest.py
       tests/unit/test_workflow_cancellation.py
       tests/integration/test_patrol_runtime_fault_injection.py
       tests/unit/test_check_item_assessment_runtime.py
=> 77 passed

pytest tests/unit/test_check_item_assessment_runtime.py
       -k terminal_device_failures_settle_two_thousand_executions_with_fixed_selects
=> 1 passed；1000 台设备 × 2 检查项 = 2000 个 UTD，SELECT 不超过 3 次，重复结算新增 0 条

pytest tests/unit/test_device_patrol_runner.py
       tests/unit/test_parser_workflow_failure_codes.py
       tests/unit/test_workflow_cancellation.py
=> 19 passed；connection_failed/command_timeout 经过 Runner、Retry、Workflow 和目标记录不丢失

pytest tests/unit/test_workflow_parallel.py
       tests/unit/test_workflow_plugins.py
       tests/unit/test_patrol_integrity_recovery.py
       tests/unit/test_patrol_failure_semantics.py
       tests/unit/test_patrol_workflow_reconciliation.py
       tests/integration/test_patrol_runtime_fault_injection.py
=> 91 passed

pytest tests/unit/test_check_item_assessment_runtime.py
       -k "bulk_device_failures_keep_coverage_stage or run_finalizer"
=> 7 passed；覆盖命令超时、Snapshot 持久化丢失、主命令超时后 fallback 解析失败、错误绑定、
   EvidenceQualification、Normalizer CoverageDecision 与正常终态

pytest tests/unit/test_workflow_cancellation.py
=> 14 passed；覆盖失败码/Skill 阶段矩阵、snapshot_run_id 传递、取消幂等和晚到回调 fencing

pytest tests/unit/test_patrol_failure_semantics.py
       tests/unit/test_device_patrol_runner.py
       tests/unit/test_workflow_cancellation.py
       tests/integration/test_patrol_runtime_fault_injection.py
=> 45 passed；采集、Runner、取消、失败码透传和真实 PostgreSQL 故障注入保持通过

python scripts/test/patrol_worker_loss_full_stack_e2e.py
=> passed；真实 RabbitMQ/Celery solo Worker A 硬终止后，Worker B 接收 broker redelivery；
   attempt 0 = abandoned/celery_worker_lost_redelivery，attempt 1 = completed；
   隔离队列和 PatrolRun 清理后均为 0
```

真实 Worker 证据：

```text
.tmp/patrol-worker-loss-full-stack-e2e/summary.json
```

本节状态为 `denominator_and_artifacts_verified / bulk_settlement_scale_verified /
worker_loss_redelivery_verified / production_device_fault_matrix_pending`。它关闭了取消和分发失败目标无法
形成最终报告、失败码丢失以及 Worker 整体退出后重投递与 fencing 的工程缺陷，但不替代预发布环境的
真实设备断连、真实命令超时、串并行任务中断和千台在线取消压测。

## 4. 发布结论

42 项场景中，场景 1、2、3、4、6、7、8、9、10、11、12、13、14、15、17、18、19、20、21、22、23、24、25、26、27、28、29、30、33、34、35 和 37 已达到各自工程部分所要求的真实
BFF/API/PostgreSQL/Celery/Redis/MinIO 集成证据层，
场景 16 达到其所需的组件 Mock 证据层，场景 31 达到真实 HTTP/PostgreSQL 集成证据层。其余场景
存在可靠的领域、组件或局部集成证据，但没有完成要求的主证据层，不能统一标记为 E2E 已通过。
场景 6、7、20 和 28 的工程纵切通过不等于真实生产人工门禁已经完成。
场景 36 已达到真实依赖 `I` 层，并关闭并发、重投递、范围受限失败、证据身份对账、浏览器上传、
Inventory 绑定、独立复核、空计划 fail closed、批准后故障投影、刷新保持、恢复续跑和冻结资产漂移。
这些工程证据不能替代新鲜现场设备、真实人工资产审批和正式签发的 `P/H` 层正向证明。
场景 37 已达到真实依赖与浏览器 `I` 层，关闭五类对象实际字节重验、故障注入、1000 条分页、幂等与
中断恢复工程门禁；但自动化账号没有完成甲方内容验收与独立签发的 `H` 层，因此只能标记为
`engineering_done / real_human_pending`。
场景 40 已具备取消 API/BFF/UI、终态锁定、真实 PostgreSQL 晚到成功/失败拒绝、未启动目标与分发失败
目标的 UTD 结算、五类制品分母对账、真实 RabbitMQ/Celery Worker 硬终止重投递和浏览器确认交互；但
尚未完成预发布真实设备断连、实际命令超时、串并行任务中断和千台在线取消压测，状态仍为
`engineering_partial`。
场景 41 已完成显式 RiskEpisode 关联、独立复核、报告分账和风险工作台的工程实现；真实关联策略仍须
网络 SME 复核，首个甲方 `production_current` 报告仍须内容验收与独立签发，因此不得标记为产品完成。
场景 3.37 已证明当前 `lixian` 三条检查项双 RuntimeBundle 执行路径完整，并如实因判断策略资产未就绪
暂停；该 fail-closed 结果不能被解释为灰度通过。

以上场景用于证明工程事实与失败语义，不自动满足 8.13 方案中的生产 `ContentAccepted` 或
`ProductGoalAchieved`。一份批次报告即使可以诚实表达未知范围，也只有通过机器全量对账、人工风险
分层抽样和独立签发后才可正式交付；整个产品还需五条黄金纵切、甲方生产矩阵、新鲜 current、两个
生产周期、多厂商规模及治理退出证据，不能由本矩阵任一单项结果替代。

下一轮工程优先级：

1. 复用场景 29 已建立的真实依赖测试栈，扩展浏览器/API/Worker/PostgreSQL/Redis/Celery/MinIO
   验收夹具，而不是为每条场景重建不同的伪集成环境。
2. 场景 3.29 已关闭 ParserBundle、逐轮持久化、阶段心跳、总 deadline 和 AI 队列隔离阻断，并形成
   S6850 v2 审批包；工程师现在必须复核四个真实待审批候选，由不同发布人作出批准或拒绝决定。旧四
   候选及其 Candidate ID 不得恢复审批。
3. Parser 人工发布后，按场景 6、7 和 20 已验证的同一服务端路径，从 Active Parser 的不可变
   sanitized context 重新生成并审批 CPU、接口状态和光功率实际需要的 Contract、CurrentStateRule、
   Policy 与 EventRule；设备版本只装配其 Contract 与报告投影；运行配置只审批 TextComparisonProfile
   并装配 identity policy。随后分别形成五条 RuntimeBundle。不得为流程整齐给运行配置伪造结构化
   Policy/Event，事务 dry-run 也不能直接转为生产资产。
4. 场景 38 与场景 39 的历史工程 `I` 层纵切已关闭；先完成判断策略控制面的真实浏览器填充态复验，
   再由真实 Active RuntimeBundle 和新鲜双周期补齐场景 39 的 `P/H` 层；随后由
   网络 SME 对场景 41 至少一组批准和一组拒绝关联作出真实复核。自动化不得根据事件名称、接口状态
   或命令字符串替代该判断。
5. W0.4b 保持回归；复用 3.35 已落地的取消核心合同，在人工审批后使用真实设备完成 W0.5 和场景 32，
   并在预发布补齐真实设备断连、实际命令超时、串并行任务中断和千台在线取消压测。Worker 硬终止/
   重投递、千台批量结算和报告制品分母已经具备自动化证据，继续保持回归。每个 included 目标对必须结算，
   已完成事实保留，未完成目标为 `cancelled/failed + unable_to_determine`，终态后不得发生越权业务写入。
   历史离线报告及 `production_network_claim=false` 的工程夹具均不得冒充当前网络结论。
6. 使用同一冻结生产目标矩阵完成甲方内容验收与独立签发，再完成 5/20/50/100% 灰度、回滚演练和
   两个生产周期对账；只有 M1/M2/M3 对应证据齐备后才提升产品完成声明。
