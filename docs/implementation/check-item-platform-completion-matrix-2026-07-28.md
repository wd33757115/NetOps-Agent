# 检查项平台实施与验收矩阵

> - 对应方案：`docs/check-item-centered-patrol-platform-remediation-2026-07-27.md`
> - 状态口径：`done` 已有代码、迁移和测试证据；`in_progress` 已部分落地；
>   `pending` 尚未达到阶段验收；`blocked` 存在工程阻断；
>   `blocked_by_asset_readiness` 表示平台行为正确，但生产资产矩阵尚未就绪。
> - 更新规则：不得仅凭文档、接口占位或单元测试 Mock 把条目标记为 `done`。

## 当前发布判断

| 判断对象 | 状态 | 依据 |
|---|---|---|
| 平台工程能力 | in_progress | Top 八项计划、终态 Assessment、EvidenceQualification、CoverageDecision、RiskCase 当前性、AssetGap、对象存储报告、正式签发、规则资产工作台和风险整改已落地；真实 `device.version`、多设备部分失败、direct/workflow 终态等价、迟到事实新 Manifest 和浏览器交付链路均通过 | CPU/接口状态/光功率生产资产纵切、完整权限矩阵、大批次交互和灰度验收 |
| 当前生产资产 | blocked_by_asset_readiness | 数据库 Active CheckItemRuntimeBundle 为 0；3 个 Active Parser 尚无正式装配入口，现有 Contract/Policy/Rule 使用的部分 observation type 与 Canonical Catalog 不一致；`lixian` structured ready 约 2.6% |
| 缺资产批次的诚实交付 | done | 2000 台 × 8 项形成 16000 个 `unable_to_determine`、0 个 `healthy`，并可生成 final ReportManifest 和五类交付物 |

## 总体状态

| 阶段 | 状态 | 当前证据 | 下一验收门 |
|---|---|---|---|
| Phase 0 基线与架构决策 | done | ADR-008；`lixian` v1 manifest；关键行为保护测试 | 继续保持语料摘要和方案版本同步 |
| Phase 1 检查项目录与采集实现 | done | 0058；目录、Resolver、per-device plan、命令去重和 63 项相关测试 | 在 Phase 4 真实批次中持续对账 |
| Phase 2 NTC 模板优先复用 | in_progress | NTC Resolver、provenance、客观执行结果和单测已落地 | 控制面三路真实 E2E、人工直审和导入发布 |
| Phase 3 标准语义、Assessment 与运行包 | in_progress | 0059/0060/0062/0063；Finding、Assessment、Risk、Comparison、EvidenceQualification、CoverageDecision、RuntimeBundle；规范值拼接、重复去重与冲突阻断；确定性 preview/draft/activate/rollback API、兼容范围、显式规则归属与 fallback 裁剪已通过定向测试 | canonical 迁移，CPU/接口/光功率首次状态与双周期全链路 |
| Phase 4 运行时切换 | in_progress | check-item preflight/freeze、per-binding fallback、冻结 Parser 读取、身份/输出/时效资格、实体覆盖决定、运行终态 Finalizer、终态集合对账；真实成功、缺资产、多设备部分失败和 direct/workflow 终态等价路径均通过 | CPU/接口状态/光功率生产资产与真实双周期路径 |
| Phase 5 UI 与报告切换 | in_progress | 冻结 ReportManifest；迟到事实形成新 Manifest 且旧签发不变；同源 Markdown/DOCX/XLSX/JSON/证据包写入 MinIO 并回读验签；ReportRelease；检查项 Profile、规则资产工作台、风险整改工作台；批次检查项服务端分页明细；BFF 完整代理和认证流式进度；真实浏览器 E2E | 完整权限矩阵和大批次交互验收 |
| Phase 6 文档和废弃代码清理 | in_progress | 74 份 Markdown 已登记；Active/Superseded/Archived 唯一性与断链 CI 门禁已落地 | 废弃代码引用、流量、数据、任务和回滚五类删除门禁 |
| Phase 7 灰度与收尾 | pending | 尚未开始 | 5/20/50/100% 灰度与两个完整周期对账 |

## 需求到证据

| 需求 | 权威实现或计划落点 | 测试或验证 | 状态 |
|---|---|---|---|
| 检查项是业务主身份 | `docs/adr/008-check-item-business-identity.md` | 架构评审 | done |
| Top 八项版本化目录 | `src/core/check_items/assets/catalog.v1.yaml` | `test_top_catalog_is_check_item_centered_and_complete` | done |
| CheckItem/Implementation 版本不可原地修改 | `src/core/check_items/catalog.py` | `test_catalog_bootstrap_is_idempotent_and_versions_are_immutable` | done |
| 规范化 CheckItem/Implementation/Parser 关系 | `netops_check_item_implementation_links`、`netops_collection_implementation_parsers` | 0058 migration + metadata create | done |
| 身份不足不猜测 | `CheckItemImplementationResolver` | missing family、ambiguous candidate tests | done |
| 一条命令服务多个检查项只采集一次 | `build_shadow_plan()` | NX-OS CPU/Memory `show system resources` 去重测试 | done |
| 执行完成不等于健康 | `CheckExecutionRecord` + `CheckAssessmentRecord` | completed + unable_to_determine test | done |
| 当前状态风险不依赖历史基线 | `CurrentStateFindingService` + `CheckItemExecutionService` | 首次巡检可形成 Finding/RiskCase，不伪造 Change；生命周期测试 | done |
| ComparisonContext 冻结基线选择 | `ComparisonContextService` + `netops_comparison_contexts` | no_baseline、previous-success、explicit-period tests | done |
| 当前状态时效与零记录语义 | CheckItem freshness/empty-result policy + EvidenceQualification | stale、unknown time、structured zero-record、approved not-applicable 和报告披露测试；迟到事实另项跟踪 | done |
| 设备身份与证据资格 | `EvidenceQualificationRecord` + 0063 | identity conflict fail-closed、不可变摘要、真实 `lixian` E2E | done |
| CLI 输出质量先于 Parser | Implementation output-quality policy + EvidenceQualification | truncated/paged/device error/empty 分类；Parser 成功不能覆盖资格失败 | done |
| 实体覆盖分母与完整性 | `CoverageDecisionRecord` + 0063 | fixed/inventory/derived/unbounded/unknown 决策、分母引用和报告披露 | done |
| 当前风险与历史待复核风险分离 | RiskCase verification status + CheckAssessment | 失败或过期证据不刷新 last_seen；报告 current/not_reverified/recovery_pending 分区 | done |
| 事件恢复不受乱序事实回退 | 既有 NetworkEvent/Occurrence 生命周期 | `test_late_recovery_and_fault_do_not_regress_projection` | done |
| 目标设备在启动时冻结 | `PatrolBatchRecord` + `PatrolBatchDeviceRecord` | `tests/unit/test_patrol_batch_manifest.py` | done |
| 仅终态批次请求正式报告 | `request_terminal_report()` | `tests/unit/test_patrol_batch_manifest.py` | done |
| 报告事实摘要可重建 | `PatrolBatchSummaryProjection.source_digest` | `tests/unit/test_patrol_batch_projection.py` | done |
| 缺资产只登记治理任务，运行时不调用 LLM | `AssetGapService` + runtime invariant | 幂等 gap 测试；生产全链路模型调用测试待补 | in_progress |
| 原子 CheckItemRuntimeBundle | `RuntimeBundleService` + 0060 | 依赖摘要、兼容拒绝、激活和冻结读取测试 | in_progress |
| RuntimeBundle 生产装配控制面 | `RuntimeBundleAssemblyPlanner`、assembly preview/assemble/activate/rollback API、`CoverageBackfillWorkspace` | preview 无写入、stale digest 拒绝、草稿幂等、激活审计与 API 409 合约测试通过；浏览器实测 H3C `device.version` 展示 6 个真实阻断且草稿禁用，就绪资产激活 E2E 待补 | in_progress |
| RuntimeBundle 是唯一生产绑定 | `RuntimeBundleAssetRecord` + `CheckItemRuntimeBundleService` | 装配不再要求独立 Parser binding；RuntimeBundle 冻结全部子资产，运行时按 Bundle 读取；迁移投影退出门禁待完成 | in_progress |
| 实现范围不宽于 Parser 证据范围 | `runtime_bundle_assembly.parser_compatibility()` | 厂商级 Implementation + 型号级 Parser 被 fail closed；兼容证据摘要变化使 preview 失效 | done |
| EventRule 显式归属 canonical observation | `event_rule_observation_types()` + RuntimeBundle 装配 | 仅接受 `condition_json.observation_types`，缺失、越界和外部映射不一致均阻断 | done |
| shared-command observation projection | RuntimeBundle `observation_projection` + Contract canonical superset 校验 | 允许已审批 canonical 超集，Bundle 只冻结本检查项所需投影；多 record-set 真实 E2E 待补 | in_progress |
| fallback 可执行集合与韧性 | RuntimeBundle `executable_commands` + Effective Plan 裁剪 | full/reduced/none 单测通过；不可执行高优先级命令留痕为 `asset_unavailable_at_plan_time` | done |
| 内置 EntityIdentityPolicy 可用于装配 | Gateway 启动幂等 bootstrap + RuntimeBundle 冻结版本 | 浏览器首次发现 preview/assemble 死锁后修复；启动日志确认 Active v1，预检不再返回 `entity_identity_policy_missing` | done |
| Active Parser 不冒充运行就绪 | `RuntimeBundleService` + Effective Plan | 当前 3 个 Active Parser、0 个 Active Bundle；缺 Bundle 形成 AssetGap；事务内完整 Bundle 可跑通真实样本 | done |
| 覆盖页不再用旧规则阶段冒充就绪 | `catalog_health()` + `/check-item-runtime-bundles/health` + `CoverageBackfillWorkspace` | 浏览器实测 8 检查项、27 实现对、0 Active Bundle、27 缺口、0%；接口失败显示无法判断 | done |
| 快照内 canonical 事实对账 | `state.normalizer._reconcile_snapshot_observations` | 重复值去重、冲突值阻断单测；MSR5620 12 条记录投影为 2 条 Observation | done |
| 完整软件版本投影 | `semantic_projection.concat` | `7.1.064 + 0821P18` 投影为 `7.1.064, Release 0821P18` | done |
| Comparison schema reference 不截断 | 0062 + `ComparisonContextRecord` | 66 字符真实 catalog reference 可冻结；有损 downgrade 被阻断 | done |
| 每个计划设备 x 检查项均有 Assessment | Start preflight + CheckItemRunFinalizer + terminal gate | 成功、错误绑定、失败阶段、混合设备结果和终态集合对账单测；失败设备只影响对应 Assessment | done |
| 报告同源输出 Markdown/DOCX/Excel/JSON/证据包 | `ReportManifestService` + `CheckItemReportMaterializer` | 确定性重建、事实摘要、无事件不等于健康、证据包不含 raw CLI、原子挂载测试 | done |
| 报告截止时刻与迟到事实 | `ReportManifestRecord.report_cutoff_at` | 无事实变化时幂等复用 Manifest；截止后事实变化生成新版本和新 cutoff，旧 release 保持绑定旧版本，新版本必须重新物化与签发 | done |
| 事实冻结与正式签发分离 | `ReportReleaseRecord` + 0063 + patrol API/UI | 精确 artifact digest 校验、批次身份拒绝、审计、MinIO 回读和浏览器签发流程 | done |
| 确定性交付物字节级可复现 | Office ZIP/core properties 归一化 + artifact SHA-256 | 延时重建 XLSX 摘要一致；五类交付物同 Manifest 重建一致 | done |
| 批次检查项明细服务端分页 | `CheckItemBatchResultService` + `/check-results` | 搜索、状态筛选和分页投影单测；前端生产构建 | done |
| 2000 台 × Top 八项服务端分页与报告 | `check_item_runtime_capacity_benchmark.py` | 16000 结论；摘要 0.63 秒；100 条页 P95 0.805 秒；报告 69.3 秒；全部回滚无库污染 | done |
| 2000 台 × 20 个真实检查项最终容量 | 目录扩展后的同一基准脚本 | 禁止复制虚假检查项或线性外推，待真实目录达到 20 项后执行 | pending |
| `lixian` Q1/Q2/Q3 多型号回归 | v1 manifest：Q1 2/2/54、Q2 6/6/152、Q3 6/6/162 | Q2→Q3：420→420 Observation、30 Change、0 Event；结构化就绪约 2.6% | in_progress |
| `lixian` 新检查项运行时成功路径 | `check_item_lixian_runtime_e2e.py` | 真实 MSR5620 `display version`：12 Parser rows -> 2 Observation -> confirmed/complete/stale + fixed 1/1 coverage -> informational/complete Assessment -> MinIO 五类制品 -> immutable release；含真实 Workflow Run/Step，回滚后数据库与对象存储不变 | done |
| 生产检查项 RuntimeBundle 覆盖 | `CheckItemRuntimeBundleService` | 当前 Active Bundle 为 0；应形成 AssetGap 和无法判断，不得伪造健康 | blocked_by_asset_readiness |
| 文档治理与断链门禁 | `document-governance.yaml` + `audit_docs.py` | 74 份登记；未登记 0；断链 0；UTF-8 通过；CI 已接入 | done |
| 废弃代码安全退出 | `deprecations.yaml` + `audit_deprecations.py` | 5 个候选已登记：2 个禁写、2 个只读、1 个观察中；无候选绕过七项门禁进入删除 | in_progress |

## Phase 0 保护对象

| 保护对象 | 当前权威实现 | 迁移原则 |
|---|---|---|
| 当前状态规则评估 | `src/core/workflows/patrol_batch_service.py:evaluate_current_state` | 提升为正式阶段并持久化 Finding，不重写规则语义 |
| Event 生命周期 | `NetworkEventRecord`、`NetworkEventOccurrenceRecord` | RiskCase 首期从现有相关键投影，不复制一套冲突生命周期 |
| 批次目标快照 | `PatrolBatchRecord`、`PatrolBatchDeviceRecord` | 扩展检查项 target manifest，不改写历史目标 |
| 终态报告请求 | `src/core/patrol/batch_manifest.py:request_terminal_report` | 正式报告仍只能由全终态批次触发 |
| 报告重建摘要 | `PatrolBatchSummaryProjection.source_digest` | ReportManifest 纳入目标、资产、比较和事实摘要 |
| 命令中心兼容读 | Command Profile、legacy batch readers | shadow 指标达到退出门禁前只读保留 |

## 最近验证

```text
venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_catalog.py \
  tests\unit\test_ntc_template_resolver.py \
  tests\unit\test_observation_semantic_projection.py \
  tests\unit\test_state_normalizer.py \
  tests\unit\test_check_item_assessment_runtime.py \
  tests\unit\test_check_item_reporting.py \
  tests\unit\test_parser_management_api.py \
  tests\unit\test_docs_governance.py \
  tests\unit\test_deprecation_governance.py -q
172 passed

venv\Scripts\python.exe scripts\test\check_item_lixian_runtime_e2e.py \
  --source-file lixian\Q2\XA-FOTIC-RT5620-1-10.34.2.219.txt \
  --output-dir .tmp\check-item-lixian-e2e\20260728-plan-v24
12 Parser records / 2 canonical observations
software version: 7.1.064, Release 0821P18
assessment: informational / complete
report: final / informational_only
five artifacts rendered and released
active runtime bundle before/after: 0 / 0
database unchanged: true

venv\Scripts\alembic.exe heads
0063_truth_qualification (head)

venv\Scripts\python.exe -m pytest \
  tests\unit\test_observation_semantic_projection.py \
  tests\unit\test_state_normalizer.py \
  tests\unit\test_check_item_assessment_runtime.py \
  tests\unit\test_check_item_reporting.py \
  tests\unit\test_docs_governance.py \
  tests\unit\test_deprecation_governance.py -q
83 passed

venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_catalog.py \
  tests\unit\test_ntc_template_resolver.py \
  tests\unit\test_check_item_assessment_runtime.py -q
27 passed

venv\Scripts\python.exe -m pytest \
  tests\unit\test_collection_policy.py \
  tests\unit\test_patrol_handlers.py \
  tests\unit\test_cli_ingestion_adapter.py -q
36 passed

venv\Scripts\python.exe -m py_compile \
  src\core\check_items\planning.py \
  src\core\check_items\execution.py \
  src\core\check_items\report_manifest.py \
  src\core\check_items\run_finalizer.py \
  src\core\patrol\handlers.py \
  src\core\patrol\runner.py \
  migrations\versions\0058_check_item_patrol_foundation.py \
  migrations\versions\0059_check_item_assessment_runtime.py \
  migrations\versions\0060_check_item_runtime_switch.py
passed

venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_reporting.py \
  tests\unit\test_celery_role_routing.py \
  tests\unit\test_patrol_handlers.py -q
24 passed

venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_assessment_runtime.py \
  -k "report_manifest or run_finalizer" -q
5 passed

npm --prefix web\react_frontend run check:utf8
passed

npm --prefix web\react_frontend run build
passed

venv\Scripts\python.exe scripts\test\phase1_6_e2e_validation.py \
  --previous-source lixian\Q2 \
  --current-source lixian\Q3 \
  --output-dir .tmp\lixian-phase1-6
previous/current observations: 420/420
changes: 30
events: 0

venv\Scripts\python.exe scripts\test\check_item_runtime_capacity_benchmark.py \
  --devices 2000 \
  --page-size 100 \
  --page-samples 20 \
  --output .tmp\check-item-capacity\runtime-2000-20260728.json
16000 executions / 16000 assessments / 8 aggregated gaps
summary: 0.63 seconds
result page P95: 0.805 seconds
manifest + artifacts: 69.3 seconds
truthful unknown conclusion: passed
outer transaction rolled back

venv\Scripts\python.exe scripts\test\check_item_lixian_runtime_e2e.py \
  --source-file lixian\Q2\XA-FOTIC-RT5620-1-10.34.2.219.txt \
  --output-dir .tmp\check-item-lixian-e2e\20260728-final
12 Parser records / 2 canonical observations
software version: 7.1.064, Release 0821P18
assessment: informational / complete
report: final / informational_only
five artifacts rendered
active runtime bundle before/after: 0 / 0

venv\Scripts\python.exe scripts\docs\audit_docs.py \
  --today 2026-07-28 \
  --strict-unregistered \
  --all-links
governed: 74
unregistered: 0
broken links: 0

venv\Scripts\python.exe scripts\deprecations\audit_deprecations.py \
  --today 2026-07-28
candidates: 5
observing: 1
read_only: 2
write_disabled: 2

venv\Scripts\python.exe -m pytest \
  tests\unit\test_check_item_assessment_runtime.py \
  tests\unit\test_parser_management_api.py -q
82 passed

venv\Scripts\python.exe scripts\docs\audit_docs.py \
  --registry docs\document-governance.yaml \
  --strict-unregistered \
  --all-links
governed: 74
unregistered: 0
broken links: 0
```
