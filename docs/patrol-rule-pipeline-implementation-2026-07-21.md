# 巡检链路 + 规则生成链路实施验收记录（2026-07-21）

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文中的“已落地”仅代表当时基线，不代表当前产品完成状态。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-command-adapter-simplification-remediation-2026-08-10.md)。

## 已落地

| 项 | 状态 | 说明 |
|----|------|------|
| P1-4 CI/SKILL | 完成 | CI 增加 patrol/parser quality gate；device-patrol 队列改为 `netops.patrol`；Bundle replay 碰撞门禁已存在 |
| P0-1 Coverage policy | 完成 | `PATROL_PARSER_COVERAGE_POLICY=skip\|block\|warn`；preview/launch/collector 统一决策；UI 展示预检跳过 |
| P0-4 Impact KPI | 完成 | work queue `production_impact`；`/parser/coverage?by_effective_plan=true`；Inbox 影响面列 |
| P0-2 Template→context | 完成 | `prepare_from_parser_template` + `auto_build_context` + Outbox 可选 auto-generate；UI「自动补上下文」 |
| P0-3 Change gaps | 完成 | `/parser/rule-gaps/*` + Parser 控制面「变化缺口」；Bundle replay 门禁 |
| P1-1 Gap ledger | 完成 | 迁移 `0049_parser_gap_ledger`；production 登记；Beat `promote_parser_gap_ledger` |
| P1-2 四态 | 完成 | effective-plan `command_asset_states`；设备页四态表 + deep-link |
| P1-3 SSE | 完成 | Redis publish + `GET /devices/patrol/batches/{id}/events` + BFF SSE + Panel Top3/实时失败 |
| P1-5 异步修订 | 完成 | `async_mode=true` + task 查询；工程修订抽屉进度条 |
| P2-1 容量 | 完成 | `scripts/test/patrol_capacity_benchmark.py` + `docs/patrol-capacity-baseline-2026-07-21.md` |
| P2-2 config-diff | 完成 | `entry_script` + `config_diff_bridge` 接入 raw_hash_diff / backup |
| P2-3 Adapters | 完成 | RawEvidence.`source`（0050）+ `collection/adapters`（CLI + stubs） |
| P2-4/5 RBAC+文档 | 完成 | Gateway + BFF operator；approve/force-activate 仍 admin；双模式/验收文档 |

## 配置开关

```env
PATROL_PARSER_COVERAGE_POLICY=skip
RULE_ASSET_AUTO_SEED_ENABLED=true
RULE_ASSET_AUTO_GENERATE_ENABLED=false
PRODUCTION_GAP_LEDGER_ENABLED=true
PRODUCTION_GAP_LEDGER_JOB_THRESHOLD=3
```

## 建议冒烟

1. `alembic upgrade head`（含 0049/0050）
2. `pytest tests/unit/test_patrol_coverage_policy.py tests/unit/test_command_four_state.py tests/unit/test_collection_adapters.py -q`
3. 设备页 preview：缺模板设备进入 `skipped`
4. Parser：Inbox 影响面、变化缺口 seed、自动补上下文、工程修订异步进度
