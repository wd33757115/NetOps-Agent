# 巡检输出分类、能力绑定与非结构化命令处理

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

## 问题结论

同一个巡检命令可能跨多个型号复用，但空输出不能统一解释为零实体。空白可能表示合法空、命令不支持、权限错误或异常返回。配置文件等命令也不应进入 TextFSM 和 Parser Backlog。

本次改造采用以下顺序：

```text
Execution Result
-> OutputClassifier
-> Capability/Command Binding
-> Template Resolver（仅 data 且 structured）
-> Observation
-> Change/Event
```

## 输出状态

| output_status | 语义 | Parser Backlog | Fact/Baseline |
| --- | --- | --- | --- |
| `data` | 有可解析数据 | 缺模板时进入 | 取决于 Parser |
| `valid_empty` | 契约明确允许的零实体 | 不进入 | `empty`，可作为完整基线 |
| `empty_unverified` | 空白但无可信空语义 | 不进入 | 阻断，不具备删除权限 |
| `unsupported_command` | CLI 明确返回不支持 | 不进入 | `not_applicable` |
| `command_error` | 权限或执行错误 | 不进入 | 阻断 |
| `not_classified` | 非结构化 raw-only 或历史兼容数据 | 不进入 | 由 handling 决定 |

纯空字符串默认是 `empty_unverified`。只有 `supported_on` 命中且 capability 的 `empty_semantics.blank=valid_empty` 时，才能直接产生 `parse_empty`。

## 多型号模板复用

命令可通过 `capability` 绑定业务事实，并通过 `template_scope` 选择模板作用域：

```yaml
capabilities:
  vrf.inventory:
    template_scope: family
    empty_semantics:
      blank: valid_empty

commands:
  display vrf:
    enabled: true
    capability: vrf.inventory
    supported_on:
      - {vendor: H3C, families: [Comware7]}
    template_scope: family
```

`family` 作用域下，不同型号的生产缺模板样本进入同一个幂等 Job。LLM 生成的候选模板必须同时通过该 Job 的全部型号样本，以及同 family 已知型号的历史回归样本，之后仍需审批才能激活。Runtime 仍优先使用型号级模板，再回退到 family 模板。

## 非结构化命令

配置命令继续由 `command_policy.yaml` 声明为 `raw_hash_diff`：

```text
Raw CLI -> Raw Evidence（正文）
        -> SHA-256 structured fact（仅哈希）
        -> raw.command.hash Observation
        -> Change
        -> ConfigChanged Event
```

配置正文不会进入 structured payload、CurrentState、LLM 或事件；只有 Raw Evidence 保存正文。`raw_only` 日志类命令仅保存证据，不生成 Parser Job 或事实变化。

## 数据与兼容性

- migration `0035_output_classification` 为 Snapshot 和 CollectionResult 增加正式 `output_status`。
- 原有 `parse_empty`、`ignored` 和兼容 `status` 保持可读。
- Normalizer 明确跳过 `not_applicable`，阻断 `empty_unverified/command_error`。
- Evidence Timeline 和 Patrol Report 展示输出分类及其计数。
