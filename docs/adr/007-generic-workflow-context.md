<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 007: 通用 Workflow 上下文与领域约束解耦

> 演进核对（2026-09-06）：通用上下文与领域必填字段解耦仍生效。未声明 required_context 只表示该层没有额外领域必填项，不免除权限、插件发布状态和业务启动校验。实现及回归入口见[Supervisor 指南](../12_Supervisor路由与Skill执行流程.md)。

## 状态

已接受

## 背景

早期 Chat Intent 基础设施默认要求工单号，导致巡检、分析等通用 Workflow
被动依赖 ITSM 语义。基础设施不应预设具体业务领域的必填字段。

## 决策

1. Chat Intent 基础设施默认不要求任何领域字段。
2. 插件通过 `required_context` 显式声明启动所需上下文。
3. ITSM Workflow 自行声明 `ticket_id`，巡检 Workflow 不声明该约束。
4. 上下文提取、状态映射和默认值属于通用机制，不在匹配器中硬编码业务流程。
5. Skill 保持独立输入输出契约，Workflow 只负责编排，不把上下游调用写入 Skill。

示例：

```yaml
required_context:
  - ticket_id
```

未配置 `required_context` 时，Workflow 可以通过聊天、API 或管理界面直接启动。

## 后果

- 新领域接入不再继承 ITSM 约束。
- 每个插件对自己的输入要求负责，依赖关系更清晰。
- 新增可从自然语言提取的字段时，只扩展提取器，不修改通用匹配流程。
- 插件测试必须覆盖必填字段存在、缺失和无领域约束三种情况。
