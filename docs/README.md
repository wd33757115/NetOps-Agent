<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Latest platform remediation plan: [check-item-centered-patrol-platform-remediation-2026-07-27.md](./check-item-centered-patrol-platform-remediation-2026-07-27.md)

# NetOps Agent 文档中心

> 版本：2026-07-28
>
> 文档治理清单：[document-governance.yaml](./document-governance.yaml)
> 当前实施证据：[检查项平台实施与验收矩阵](./implementation/check-item-platform-completion-matrix-2026-07-28.md)

---

## 正式文档（01–11）

| 编号 | 文档 | 说明 |
|------|------|------|
| 01 | [系统架构设计](./01_系统架构设计.md) | 逻辑/物理架构、数据流、安全边界 |
| 02 | [概要与详细设计](./02_概要与详细设计.md) | Supervisor、Skill、Workflow、RAG 详细设计 |
| 03 | [数据库设计 & 数据字典](./03_数据库设计%20&%20数据字典.md) | PostgreSQL 表结构、字段说明 |
| 04 | [API 接口文档](./04_API%20接口文档.md) | BFF/FastAPI 端点、认证、错误格式 |
| 05 | [编码 & 工程规范](./05_编码%20&%20工程规范.md) | Python/React 规范、测试、Git |
| 06 | [环境 & 编译构建说明](./06_环境%20&%20编译构建说明.md) | 依赖安装、本地启动、构建 |
| 07 | [系统配置说明](./07_系统配置说明.md) | 环境变量完整清单 |
| 08 | [日志规范 & 日志字典](./08_日志规范%20&%20日志字典.md) | structlog、事件字典、Langfuse |
| 09 | [错误码定义文档](./09_错误码定义文档.md) | ErrorCode、统一错误信封 |
| 10 | [部署安装手册](./10_部署安装手册.md) | Docker Compose、生产 checklist |
| 11 | [运维技术手册 & 故障排查](./11_运维技术手册%20&%20故障排查.md) | 巡检、排障、备份恢复 |
| 12 | [Supervisor 路由与 Skill 执行流程](./12_Supervisor路由与Skill执行流程.md) | 用户提问→Skill 匹配→Workflow/ExecutionPlan 全链路 + ITSM 时序 |
| 13 | [生产上线清单](./13_生产上线清单.md) | 先上生产：阻塞项、冒烟、回滚、Compose 陷阱 |

---

## 专项指南

| 文档 | 说明 |
|------|------|
| [check-item-centered-patrol-platform-remediation-2026-07-27.md](./check-item-centered-patrol-platform-remediation-2026-07-27.md) | 检查项中心的巡检、Parser、规则资产、报告及文档/废弃代码治理总体改造方案 |
| [SKILL_CREATION_GUIDE.md](./SKILL_CREATION_GUIDE.md) | Skill 创建与 SKILL.md v2.0 |
| [测试手册.md](./测试手册.md) | 单元/集成/E2E 测试 |
| [system-overall-design-and-phased-implementation-plan.md](./system-overall-design-and-phased-implementation-plan.md) | NetOps Agent 总体设计、改造方案与分阶段实施计划 |
| [patrol-parser-dual-mode-guide.md](./patrol-parser-dual-mode-guide.md) | 测试巡检库、生产巡检库与 Parser 资产生成边界 |
| [parser-automation-guide.md](./parser-automation-guide.md) | Parser 自动发现、命令策略、身份识别和契约审批 |
| [textfsm-generator-guide.md](./textfsm-generator-guide.md) | TextFSM Generator 工作流程、设备品牌型号识别和使用方法 |
| [observation-fabric-system-design.md](./observation-fabric-system-design.md) | Observation、CurrentState、Change、Event 事实层设计 |
| [patrol-change-event-pipeline.md](./patrol-change-event-pipeline.md) | 巡检到变化检测、事件构建和事件流的链路说明 |
| [patrol-chain-audit-and-remediation-plan-2026-07.md](./patrol-chain-audit-and-remediation-plan-2026-07.md) | 巡检链路 18 项问题审计、分阶段修复计划与实施状态 |
| [patrol-chain-failure-semantics-remediation-2026-07.md](./patrol-chain-failure-semantics-remediation-2026-07.md) | 巡检链路第二轮失败重试、分布式幂等与容量修复计划 |
| [patrol-chain-integrity-recovery-remediation-2026-07-11.md](./patrol-chain-integrity-recovery-remediation-2026-07-11.md) | 巡检事实一致性、事件 provenance、失败恢复与治理修复计划 |
| [patrol-chain-replay-integrity-remediation-2026-07-11.md](./patrol-chain-replay-integrity-remediation-2026-07-11.md) | 巡检 Snapshot 幂等、版本化回放、崩溃恢复与历史事件迁移计划 |
| [rule-asset-control-plane-guide.md](./rule-asset-control-plane-guide.md) | 规则资产生成、提交审批、审批激活和聊天触发使用说明 |
| [llm-rule-control-plane-implementation-plan.md](./llm-rule-control-plane-implementation-plan.md) | LLM 规则资产控制面完整改造计划与分阶段验收 |
| [auth-rbac-plan.md](./auth-rbac-plan.md) | JWT、RBAC、BFF 可信头 |
| [langfuse-sse-plan.md](./langfuse-sse-plan.md) | Langfuse + SSE Trace 方案 |
| [storage-minio-plan.md](./storage-minio-plan.md) | MinIO 网盘方案 |

---

## 架构决策记录（ADR）

| 编号 | 文档 |
|------|------|
| 001 | [Skill 体系架构](./adr/001-skill-system-architecture.md) |
| 002 | [Progressive Disclosure](./adr/002-progressive-disclosure.md) |
| 003 | [LLM 路由](./adr/003-llm-based-routing.md) |
| 004 | [生产加固](./adr/004-production-hardening.md) |
| 005 | [Django + React BFF](./adr/005-django-react-architecture.md) |
| 006 | [Docker Compose 编排](./adr/006-docker-compose-orchestration.md) |
| 007 | [通用 Workflow Context](./adr/007-generic-workflow-context.md) |
| 008 | [检查项作为巡检业务主身份](./adr/008-check-item-business-identity.md) |

---

## 已归档 / 重定向

以下文档已合并至编号系列，保留跳转：

| 旧文档 | 新文档 |
|--------|--------|
| [API文档.md](./API文档.md) | → [04_API 接口文档](./04_API%20接口文档.md) |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | → [10_部署安装手册](./10_部署安装手册.md) |
| [logging-handbook.md](./logging-handbook.md) | → [08_日志规范 & 日志字典](./08_日志规范%20&%20日志字典.md) |

---

## 文档治理

当前架构、专项设计和历史文档的状态不再由文件名中的“最新”“最终”判断，而由
[`document-governance.yaml`](./document-governance.yaml) 统一登记。审计命令：

```powershell
venv\Scripts\python.exe scripts\docs\audit_docs.py --today 2026-07-28
```

`active` 文档是当前权威说明；`superseded` 仅用于理解迁移前设计；`archived` 只保留审计。
未登记文档属于待治理范围，不得作为当前架构依据。

---

## 快速链接

- 项目根 [README.md](../README.md)
- 脚本说明 [scripts/README.md](../scripts/README.md)
- Web 开发 [web/README.md](../web/README.md)
- OpenAPI：http://localhost:8000/docs（开发环境）
