# 规则资产候选自动化与人工发布门禁

> - 文档状态：Active
> - 最后校验：2026-07-30
> - 说明：文件名为历史兼容，不再表示平台支持无人值守发布
> - 权威上位方案：`check-item-centered-patrol-platform-remediation-2026-07-27.md`

## 1. 建设目标

规则资产自动化以规则资产页显式确认的离线样本为唯一生产来源，负责自动发现缺口、构造脱敏上下文、
生成所需候选、执行确定性验证并准备审批材料。自动化不拥有工程复核、发布审批或资产激活权限。

```text
显式离线规则样本
-> 不可变来源与脱敏上下文
-> AssetRequirementProfile
-> 按需生成 Contract / CurrentStateRule / ChangePolicy / EventRule
-> 确定性验证与必要的历史回放
-> pending_engineering_review
-> 工程师语义复核
-> pending_release_approval
-> 独立审批人发布
-> RuntimeBundle 独立装配与审批
```

原始 CLI 不进入 Contract、Policy 或 Rule 的模型上下文。模型只生成候选；本地验证只能证明候选满足
机器合同，不能替代工程师判断业务语义，也不能直接改变 Active 资产。

## 2. 运行模式

| 模式 | 行为 |
|---|---|
| `disabled` | 由工程师人工触发候选生成 |
| `generate_only` | 自动发现、生成、验证并准备审批材料，发布仍须人工完成 |

旧数据库中的 `auto_publish` 仅为兼容输入，读取时必须安全规整为 `generate_only`。API 与 UI 不再
提供该模式，也不得通过隐藏参数恢复无人值守发布。

Celery Beat 可以按周期触发候选协调任务。任务认领使用数据库行锁、租约、幂等上下文和最大重试次数，
支持多 Worker 并发。运行中的 Workflow 超过阈值仍无进度时进入恢复或工程复核，不得永久显示“运行中”。

## 3. AssetRequirementProfile

每个生成上下文从已审批 CheckItemVersion 冻结 `rule-asset-requirements/v1`。至少包含：

```text
requires_observation_contract
requires_change_policy
requires_current_state_rule
requires_event_rule_assets
requires_historical_replay
current_state_observation_types[]
change_policy_observation_types[]
required_event_types_by_observation{}
```

同一画像必须贯穿 Dataset Context、Workflow、生成器、persister、Bundle read model、工程复核、
最终审批和 RuntimeBundle 装配。各阶段不得按命令名、资产数量或页面阶段重新猜测需求。

- Contract-only 证据检查项跳过 Policy、Rule 与历史回放。
- 健康检查项按 observation type 校验 CurrentStateRule。
- ChangePolicy 按 canonical observation 覆盖，不按命令数量计数。
- EventRule 必须覆盖 Catalog 要求的故障与恢复事件类型。
- 画像外多余资产同样阻断，避免扩大运行语义。
- 来源、上下文、画像或候选摘要漂移后，旧验证和旧审批全部失效。

## 4. 机器资格门禁

自动化可以计算 `ready_for_human_review`，但返回的发布权限必须始终是：

```text
publish_authority = human_only
```

机器资格根据画像按需校验：

1. 来源是已完成、用途正确且仍未漂移的离线规则样本任务。
2. sanitized context 隐私检查通过且不包含 raw CLI。
3. 画像要求的候选资产完整，且不存在画像外多余资产。
4. synthetic validation 通过。
5. 画像要求历史回放时，周期、设备、记录、碰撞和事件量门禁通过。
6. 资产、来源、上下文和画像摘要一致。

Contract-only 证据型检查项不因缺少 Change 或 Event 被阻断。需要双周期语义的健康检查项则不能用
单周期证据绕过回放。任一条件不满足时保留候选、验证报告、阻断原因和唯一下一步行动。

## 5. 人工职责分离

控制面必须提供三个互不替代的角色：

| 角色 | 决定 |
|---|---|
| 候选创建者 | 选择来源、检查项作用域并发起生成 |
| 工程复核者 | 核对脱敏样本、canonical 映射、规则语义、阈值、回放和碰撞 |
| 发布审批者 | 核对生产作用域、依赖摘要、职责分离、回滚条件并决定是否发布 |

服务端必须比较规范化 actor identity；UI 文案和审计记录不能代替拒绝规则。自动化服务不得调用工程
复核、审批、资产激活或 RuntimeBundle 激活服务。手工内置 Provider 与 LLM 候选遵守相同门禁。

## 6. 控制面要求

工程复核页面至少展示：

- 来源任务、Dataset、设备和周期摘要；
- 隐私检查与不可变摘要；
- AssetRequirementProfile 及 `required/not_required` 阶段；
- ObservationContract、CurrentStateRule、ChangePolicy、EventRule 候选；
- synthetic validation、历史命中、碰撞和事件量估算；
- Workflow 阶段状态、耗时、失败原因和修订轨迹；
- 接受、要求修订、驳回和提交独立审批入口。

页面只提供当前阶段唯一有效的下一步。机器门禁通过时显示“可提交工程复核”，不得显示“即将自动
发布”。已发布子资产也不能冒充 Active RuntimeBundle；运行包仍需独立装配、审批和激活。

## 7. 运行与监控

- FastAPI、Celery Worker 和 Celery Beat 必须同时运行。
- 自动化策略变更记录管理员、前后值和原因。
- 监控 Beat 心跳、活动 Workflow、超时恢复、生成失败、转人工和待审批数量。
- `published_count` 仅为兼容响应字段，候选自动化运行中应保持为 0。
- Active Resolver 只读取已审批并激活的资产。
- 来源或画像漂移必须立即使待审批候选失去发布资格。

## 8. 验收结论

自动化验收应证明候选可以稳定进入人工复核，不能以“模型生成成功”或“机器门禁通过”宣称生产发布。
最终验收至少包含：

1. Contract-only 与完整健康链两种画像的 Workflow 路径。
2. CurrentState、Change 与故障/恢复 Event 的逐 observation 覆盖。
3. 多余资产、角色缺失、来源漂移和画像漂移的 fail-closed 反例。
4. legacy `auto_publish` 安全降级。
5. 自动化运行前后 Active 资产数量不变。
6. 不同工程师完成语义复核和发布审批的真实 API/数据库审计证据。

当前工程回归已经覆盖前五类合同；第六类仍属于生产人工门禁，不得由测试账号自动代签。
