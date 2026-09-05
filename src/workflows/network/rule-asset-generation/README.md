# 兼容规则资产生成工作流

本目录仍是已注册的契约式资产编排入口。当前产品默认流程见[规则自动制作](../../../../docs/patrol-rule-autopilot-guide-2026-09-05.md)：人工定义检查项及命令关联，AI 制作并验证其余资产。不要要求该默认流程再手工维护一套 ObservationContract。

## 实际编排

```text
脱敏解析记录 → 生成并验证 ObservationContract → 编译规则目标
    → 按需生成 ChangePolicy / EventRule → 事件规则验证
    → 按需历史回放 → 持久化候选资产包
```

同目录 `WORKFLOW.yaml` 是参数与条件的权威定义。首步使用 `infer_and_validate`；`rule-target-planner` 从已验证契约及记录编译策略和事件目标。后续变化策略、事件规则及回放分别受 `rule_asset_requirements` 控制，不是每种检查项都必须生成全部资产。

主要输入包括 `command`、`records`，以及适用的 `record_groups`、厂商型号、字段画像、标准观察目标、策略/事件意图、来源与不可变上下文引用。`historical_values` / `historical_values_by_field` 用于阈值建议；`replay_run_ids` 与 `replay_limit` 控制回放范围。实际模型提供方与重试参数由调用方传入。

YAML 中的 Generic/LAB 和 builtin 数据是试跑配置，不代表生产样本覆盖或真实模型质量。试跑也可能持久化资产，须使用隔离测试环境。

## 输出与发布

输出包括契约、适用的策略与事件候选、验证/回放证据，以及资产包持久化结果。生成结束不等于生产发布：审批服务继续校验 Bundle、上下文、来源、回放和工程复核门禁。自动发布由管理员授权的自动制作链路处理，本 Workflow 不自行授予授权。

它不连接设备采集数据；生产巡检执行冻结资产。兼容服务及整体发布约束见[规则资产治理](../../../../docs/rule-asset-control-plane-guide.md)。
