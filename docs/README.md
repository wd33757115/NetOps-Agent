<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps 文档中心

当前入口按工作内容组织。已替代方案、已实施计划和旧验收快照可通过 Git 历史查找；文档日期或旧测试数量不能证明目标环境已就绪。

<!-- governed-index: active -->

## 使用业务应用

| 说明 | 内容 |
| --- | --- |
| [工作台](workbench-apps-guide-2026-09-05.md) | 应用入口、桌面布局、保存与恢复 |
| [网络巡检与规则工作区](patrol-workflow-refactor-guide-2026-09-05.md) | 在线/离线任务、报告、补齐能力与复检 |
| [规则自动制作](patrol-rule-autopilot-guide-2026-09-05.md) | 人工定义、AI 制作、接管与授权发布 |
| [公文写作](official-document-workspace-guide-2026-09-05.md) | 全文起草、文种扩展、版本、排版与 Word 导出 |

## 开发与运行

| 说明 | 内容 |
| --- | --- |
| [系统架构](01_系统架构设计.md) | 当前模块职责、数据流与权限边界 |
| [开发与构建](06_环境%20&%20编译构建说明.md) | 安装、迁移、本地进程与前端构建 |
| [部署](10_部署安装手册.md) | 核心与 AI 服务、生产启动 |
| [配置参考](07_系统配置说明.md) | 配置来源与环境变量 |
| [API 参考](04_API%20接口文档.md) | BFF、业务 API 与认证 |
| [数据库](03_数据库设计%20&%20数据字典.md) | 持久化及迁移 |
| [维护与验证](maintenance.md) | 文档检查、回归范围和兼容代码退出 |
| [脚本目录](../scripts/README.md) | 部署、迁移与诊断工具 |

## 技术参考

- [检查项与命令适配器](patrol-rule-architecture.md)
- [TextFSM 生成、验证与模型工具循环](parser-generation-guide.md)
- [规则资产治理与兼容协调器](rule-asset-control-plane-guide.md)
- [变化与事件](patrol-change-event-pipeline.md)、[报告与证据](patrol-report-guide.md)
- [验证与生产边界](patrol-parser-dual-mode-guide.md)、[文本比较](patrol-text-comparison-guide.md)
- [编码规范](05_编码%20&%20工程规范.md)、[Skill 开发](SKILL_CREATION_GUIDE.md)、[Supervisor 流程](12_Supervisor路由与Skill执行流程.md)
- [日志](08_日志规范%20&%20日志字典.md)、[错误码](09_错误码定义文档.md)、[运维排障](11_运维技术手册%20&%20故障排查.md)、[上线检查](13_生产上线清单.md)

当前默认制作体验以“规则自动制作”为准；技术指南单独说明仍保留的兼容路径。架构决策保存在 [ADR](adr/) 中，阅读时注意决策年代与后续演进；它们用于解释取舍，不替代当前接口和配置。

## 权限与平台服务

- [用户角色、能力与数据范围](permissions-guide.md)
- [网盘与对象存储](storage-guide.md)
- [流式进度与 Langfuse](observability-guide.md)

已经偏离当前入口和数据架构的阶段路线图保存在 Git 历史中。新增业务需求应基于当前架构单独设计，不沿用旧计划中的“待实施”状态。

<!-- governed-index: active -->

## 文档管理

[状态登记](document-governance.yaml)记录各文档职责和最近核对日期。审计命令与代码清理进度见 [维护与验证](maintenance.md)。新文档必须登记；删除文档同步移除引用。根目录 [README](../README.md) 提供最短启动路径。
