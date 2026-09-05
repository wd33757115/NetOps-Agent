# 规则资产治理与兼容流程

核对日期：2026-09-06。默认使用检查项驱动的[规则自动制作](patrol-rule-autopilot-guide-2026-09-05.md)。本指南解释资产治理，以及仍为历史任务和人工干预保留的入口。

## 三条路径的边界

| 路径 | 输入与用途 | 发布方式 |
| --- | --- | --- |
| 命令自动制作 | 人工检查项、人工命令关联及脱敏样本 | 管理员启动或授权，完整验证后自动发布 |
| 候选协调器 `automation.py` | 受治理的来源和资产需求，准备待审候选 | 只支持 `disabled` / `generate_only`，不获得自动发布权限 |
| 手工资产 Workflow / 审批接口 | 检查和修订已有候选、兼容历史任务 | 按对应工程复核与审批门禁执行 |

候选协调器读取历史 `auto_publish` 设置时规整为 `generate_only`；它返回的 `publish_authority=human_only` 仅描述这条旧协调路径。不能将这个限制套用到已授权的新命令自动制作，也不能通过修改旧模式绕过新流程的授权。

## 资产与依赖

当前新检查项通过 CommandAdapter v2 直接投影到标准记录，变化与事件规则使用检查项标准语义。旧 ObservationContract 仍解释已有厂商字段映射；它不是每个新检查项必须手工创建的前置对象。

资产需求画像决定是否需要 CurrentStateRule、ChangePolicy、EventRule、历史回放，以及对应 observation type 和事件类型。不要按“有几个资产”判断完整度：必需资产缺失、额外语义、来源或摘要变化都应阻止发布。

规则子资产进入 active 不等于设备已经可巡检。RuntimeBundle 还必须校验设备范围、实现、Parser、Adapter 和规则版本。实际运行消费已验证的运行包，不在巡检过程中临时调用模型生成缺失规则。

## 人工治理

生成任务、候选资产与运行包各有状态，不把它们压成一个通用进度。界面可查看候选、验证、样本和历史回放，提交工程复核、要求修订、拒绝或审批激活。

手工规则包审批检查：

1. 候选处于待审批状态，验证及所需回放通过。
2. 来源、上下文、资产需求和候选摘要一致，子任务归属及版本完整。
3. 工程复核通过，审批人满足对应职责分离约束。
4. 资产切换对已有依赖的影响符合门禁。

新自动发布通过 `autopilot_authorization.py` 校验限定授权，记录 `administrator_delegated_automation`；它不伪造工程师签名。该分支要求人工所有的 CheckItem 语义及完整机器证据。

## 仍保留的 Workflow

- [rule-asset-generation](../src/workflows/network/rule-asset-generation/WORKFLOW.yaml)：按声明的需求准备候选和验证结果。
- [rule-asset-submit](../src/workflows/network/rule-asset-submit/WORKFLOW.yaml)：通过提交 Skill 处理候选，不等同于生产激活。
- [rule-asset-approve](../src/workflows/network/rule-asset-approve/WORKFLOW.yaml)：调用审批 Skill；不能只靠输入中的 actor 文本替代服务端权限与审批门禁。

这些 Workflow 仍有注册和调用入口，不能按“旧方案代码”直接删除。新业务不要绕过工作台权限直接拼接 FastAPI 路由；浏览器经 BFF，具体 API 契约以当前 OpenAPI 和 `parser_api.py` 为准。

## 排查顺序

任务停住时先检查来源用途、检查项版本、需求画像与资产缺口，再看样本验证、回放及审批原因。模型生成成功只证明有候选，不能证明运行包可用。

协调任务依赖 Worker、Beat、任务认领与租约；命令自动制作有自己的持久检查点及恢复机制。不要因前端无进度重新创建重复发布任务。

## 代码与验证入口

- [候选协调器](../src/core/rule_control/automation.py)、[审批服务](../src/core/rule_control/approval.py)
- [规则标准上下文](../src/core/check_items/canonical_rule_context.py)、[发布授权](../src/core/rule_control/autopilot_authorization.py)
- [API 实现](../src/gateway/parser_api.py)、[兼容退出登记](implementation/deprecations.yaml)

自动发布回归见[自动制作指南](patrol-rule-autopilot-guide-2026-09-05.md)，通用检查见[维护与验证](maintenance.md)。旧环境的资产数量或审批快照不作为当前可用性证明。
