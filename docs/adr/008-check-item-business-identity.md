<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 008: 巡检以检查项作为业务主身份

> 演进核对（2026-09-22）：本文为历史决策；当前问答使用 DSH，巡检和规则使用固定业务流程。实现以[当前架构](../01_系统架构设计.md)为准，旧模块名与性能数值不代表当前版本。

## 状态

已接受

## 背景

现有巡检计划、Parser 资产和规则控制面主要以
`vendor + profile + command` 组织。相同业务目的在不同厂商和型号上通常使用不同命令，
一条命令也可能同时提供 CPU、内存等多个业务结果。继续以命令作为顶层身份会导致：

- 业务覆盖率无法回答“哪些检查已经完成”。
- 跨厂商规则和报告字段被底层命令及 Parser 字段名割裂。
- 同一条命令被多个检查重复执行。
- 缺模板、采集失败和无法判断容易被误写成“未发现异常”。

## 决策

1. `CheckItem` 是巡检计划、执行结论、覆盖率和报告的稳定业务身份。
2. `CollectionImplementation` 描述某类设备如何完成检查项，包含兼容条件、命令链和
   Parser 或文本比较要求。
3. 一个检查项可以有多个实现，一个实现可以服务多个检查项。
4. Effective Plan 先解析 `设备 x 检查项`，再按规范化命令去重采集。
5. Parser 只提取厂商输出；ObservationContract 负责投影到 canonical observation。
6. 每个计划内的 `设备 x 检查项` 必须形成独立执行事实和 Assessment。
7. `completed` 仅表示链路完成，不代表健康；健康结论只能是
   `healthy / at_risk / unable_to_determine / not_applicable / informational`。
8. 当前状态评估与跨周期变化比较相互独立。没有基线时仍评估当前风险，但不生成虚假
   Change。
9. 运行时只读取冻结且摘要校验通过的资产包，不调用 LLM，也不因缺口静默降级。
10. 命令中心模型在迁移期只作为可观测的兼容层，满足退出门禁后删除。

## 后果

- Patrol Profile 和报告将逐步从命令列表切换为检查项目录。
- 必须新增版本化 CheckItem、CollectionImplementation、关联关系、CheckExecution、
  CheckAssessment、ComparisonContext 和 RuntimeBundle。
- 现有 Command Profile 在 shadow 阶段投影为检查项计划，并逐批比较实际命令集合。
- UI 默认展示检查项与结论，命令、Parser 和规则作为可展开证据。
- 报告不得再根据命令字符串推断健康状态。
- 兼容性身份不足或多个实现同等匹配时必须阻断并给出原因，不允许按排序结果猜测。
