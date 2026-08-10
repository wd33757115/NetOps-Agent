<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Latest platform remediation plan: [check-item-centered-patrol-platform-remediation-2026-07-27.md](./check-item-centered-patrol-platform-remediation-2026-07-27.md)

# NetOps Agent 文档中心

> 版本：2026-07-30
>
> 文档治理清单：[document-governance.yaml](./document-governance.yaml)
> 当前实施证据：[检查项平台实施与验收矩阵](./implementation/check-item-platform-completion-matrix-2026-07-28.md)
> 分层 E2E 证据：[35 项 E2E 证据矩阵](./implementation/check-item-platform-e2e-evidence-matrix-2026-07-30.md)

---

<!-- governed-index: active -->

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

<!-- governed-index: active -->

## 专项指南

| 文档 | 说明 |
|------|------|
| [check-item-centered-patrol-platform-remediation-2026-07-27.md](./check-item-centered-patrol-platform-remediation-2026-07-27.md) | 检查项中心的巡检、Parser、规则资产、报告及文档/废弃代码治理总体改造方案 |
| [implementation/check-item-platform-completion-matrix-2026-07-28.md](./implementation/check-item-platform-completion-matrix-2026-07-28.md) | 当前实现、验证证据、生产阻断项和下一步 |
| [implementation/check-item-platform-e2e-evidence-matrix-2026-07-30.md](./implementation/check-item-platform-e2e-evidence-matrix-2026-07-30.md) | 35 条浏览器验收场景的证据层级、现有证据和剩余门禁 |
| [simple-parser-pipeline-v2-2026-07-22.md](./simple-parser-pipeline-v2-2026-07-22.md) | Parser Job、样本、生成、验证、人工审批与激活的当前架构 |
| [textfsm-generator-guide.md](./textfsm-generator-guide.md) | TextFSM 生成与人工审批操作指南 |
| [patrol-report-canonical-observation-design-2026-07-27.md](./patrol-report-canonical-observation-design-2026-07-27.md) | canonical observation 与跨厂商报告字段设计 |
| [rule-asset-control-plane-guide.md](./rule-asset-control-plane-guide.md) | 规则资产生成、工程修订、审批、激活和回滚 |
| [observation-fabric-system-design.md](./observation-fabric-system-design.md) | Observation、CurrentState、Change、Event 事实层设计 |
| [patrol-change-event-pipeline.md](./patrol-change-event-pipeline.md) | 在线与历史巡检的状态、变化、事件、风险和故障排查 |
| [patrol-parser-dual-mode-guide.md](./patrol-parser-dual-mode-guide.md) | Parser 验证数据与生产巡检数据的边界 |
| [rule-asset-unattended-automation-2026-07-27.md](./rule-asset-unattended-automation-2026-07-27.md) | 规则资产候选自动化、人工复核与独立发布门禁（文件名为历史兼容） |
| [textfsm-deepseek-thinking-tool-loop-2026-07-26.md](./textfsm-deepseek-thinking-tool-loop-2026-07-26.md) | Thinking 模型与本地工具校验循环协议 |
| [unstructured-patrol-comparison-remediation-2026-07-19.md](./unstructured-patrol-comparison-remediation-2026-07-19.md) | 非结构化输出摘要比较与事件边界 |
| [SKILL_CREATION_GUIDE.md](./SKILL_CREATION_GUIDE.md) | Skill 创建与 SKILL.md v2.0 |
| [测试手册.md](./测试手册.md) | 单元/集成/E2E 测试 |

<!-- governed-index: proposed -->

## 提案与路线图

以下文档尚未成为当前运行合同，实施时必须重新评审：

| 文档 | 状态 |
|------|------|
| [auth-rbac-plan.md](./auth-rbac-plan.md) | proposed |
| [device-management-control-plane-roadmap.md](./device-management-control-plane-roadmap.md) | proposed |
| [langfuse-sse-plan.md](./langfuse-sse-plan.md) | proposed |
| [storage-minio-plan.md](./storage-minio-plan.md) | proposed |
| [system-overall-design-and-phased-implementation-plan.md](./system-overall-design-and-phased-implementation-plan.md) | proposed |

---

<!-- governed-index: active -->

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

<!-- governed-index: historical -->

## 已归档 / 重定向

以下文档已合并至编号系列，保留跳转。其余 superseded/archived 文档只通过
`document-governance.yaml` 查找，不再作为推荐入口：

| 旧文档 | 新文档 |
|--------|--------|
| [API文档.md](./API文档.md) | → [04_API 接口文档](./04_API%20接口文档.md) |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | → [10_部署安装手册](./10_部署安装手册.md) |
| [logging-handbook.md](./logging-handbook.md) | → [08_日志规范 & 日志字典](./08_日志规范%20&%20日志字典.md) |

---

<!-- governed-index: active -->

## 文档治理

当前架构、专项设计和历史文档的状态不再由文件名中的“最新”“最终”判断，而由
[`document-governance.yaml`](./document-governance.yaml) 统一登记。审计命令：

```powershell
venv\Scripts\python.exe scripts\docs\audit_docs.py --today 2026-07-30
venv\Scripts\python.exe scripts\deprecations\audit_deprecations.py --today 2026-07-30
```

旧链路退出前必须从迁移 `0095` 的观测台账导出至少两个真实发布周期。示例：

```powershell
venv\Scripts\python.exe scripts\deprecations\export_runtime_evidence.py `
  --candidate legacy-parser-job-dispatch `
  --window "2026.08=2026-08-01T00:00:00+08:00/2026-09-01T00:00:00+08:00" `
  --window "2026.09=2026-09-01T00:00:00+08:00/2026-10-01T00:00:00+08:00" `
  --output .tmp\deprecations\legacy-parser-job-dispatch.json
```

导出结果必须存入不可变对象存储，并把 URI 与 SHA-256 连同其他六项 gate 证据写入 registry；脚本返回
零调用也不能替代替代覆盖检查、数据库零写入证明或回滚演练。

最终产品目标使用保守审计命令复核：

```powershell
venv\Scripts\python.exe scripts\test\check_item_product_goal_audit.py `
  --output .tmp\product-goal-audit\current-state.json
```

仅在发布门禁中同时使用 `--external-evidence <immutable-evidence.json>` 与
`--require-achieved`。外部证据逐 gate 提供不可变对象 URI、SHA-256、责任人类型和带时区复核时点；
即使数据库计数全部满足，缺少该证据时工程全栈、甲方内容验收和授权签发仍保持
`external_evidence_required`。测试账号、历史 `lixian` 数据和合成生产夹具不能替代这些责任主体。

`active` 文档是当前权威说明；`superseded` 仅用于理解迁移前设计；`archived` 只保留审计。
未登记文档属于待治理范围，不得作为当前架构依据。

---

## 快速链接

- 项目根 [README.md](../README.md)
- 脚本说明 [scripts/README.md](../scripts/README.md)
- Web 开发 [web/README.md](../web/README.md)
- OpenAPI：http://localhost:8000/docs（开发环境）
