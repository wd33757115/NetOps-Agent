<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps 文档中心

Windows 安装、依赖、容器与升级统一见 [Windows 安装部署](windows-deployment.md)。

当前说明按使用、开发与验证组织。核对日期：2026-09-22。

<!-- governed-index: active -->

## 使用应用

- [工作台、个人壁纸与会话](workbench-apps-guide-2026-09-05.md)
- [应用管理与巡检预检操作：页面入口、授权、连接检查和 App 启停](app-operations-guide.md)
- [网络巡检与规则工作区](patrol-workflow-refactor-guide-2026-09-05.md)
- [资料管理](materials-guide.md)、[资料检索与问答](assistant-guide.md)
- [规则自动制作](patrol-rule-autopilot-guide-2026-09-05.md)、[公文写作](official-document-workspace-guide-2026-09-05.md)
- [App 使用权与数据范围](permissions-guide.md)、[网盘](storage-guide.md)

## 开发与部署

- [平台开发手册：整体架构、应用开发指南与演示应用的完整生命周期](developer-handbook/README.md)、[Skill 开发手册](developer-handbook/skills.md)、[Workflow 开发手册](developer-handbook/workflows.md)（工作台“Skills 和连接器 → 开发指南”中同样可读）
- [当前架构](01_系统架构设计.md)
- [能力包安装/升级/启用](capability-packages-guide.md)、[平台数据库 bootstrap 与迁移](database-bootstrap.md)
- [开发构建](06_环境%20&%20编译构建说明.md)、[部署](10_部署安装手册.md)、[配置](07_系统配置说明.md)
- [API](04_API%20接口文档.md)、[数据库](03_数据库设计%20&%20数据字典.md)、[编码规范](05_编码%20&%20工程规范.md)
- [日志](08_日志规范%20&%20日志字典.md)、[错误码](09_错误码定义文档.md)、[运维排障](11_运维技术手册%20&%20故障排查.md)、[上线检查](13_生产上线清单.md)
- [维护与验证](maintenance.md)、[测试手册](测试手册.md)、[脚本目录](../scripts/README.md)

## 技术参考

- [检查项与适配器](patrol-rule-architecture.md)、[解析生成](parser-generation-guide.md)、[规则资产治理](rule-asset-control-plane-guide.md)
- [思科样本检查项 Value 与语义](check-item-cisco-values.md)
- [Cisco 手工 TextFSM 与脱敏样本导入](cisco-manual-textfsm-import.md)
- [H3C 手工 TextFSM 与脱敏样本导入](h3c-manual-textfsm-import.md)
- [检查项变化策略与事件规则重建](check-item-semantic-rule-refresh.md)
- [检查项字段语义编辑与实施计划](check-item-semantic-editor.md)
- [变化和事件](patrol-change-event-pipeline.md)、[报告证据](patrol-report-guide.md)
- [样本与生产边界](patrol-parser-dual-mode-guide.md)、[文本比较](patrol-text-comparison-guide.md)
- [Skill 开发](developer-handbook/skills.md)、[Workflow 开发](developer-handbook/workflows.md)、[过程与观测](observability-guide.md)

## 仓库交付范围

GitHub 保留源码、使用与维护手册、安装启动脚本和必要构建配置。内部实施方案和阶段验收材料仅留在本地，具体规则见 [仓库内容范围](repository-distribution.md)。

文档状态登记在 [document-governance.yaml](document-governance.yaml)。团队性能与 Windows 并发配置见 [团队容量计划](team-capacity-plan.md)。
