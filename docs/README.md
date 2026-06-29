<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps Agent 文档中心

> 版本：2026-05-24

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
| [SKILL_CREATION_GUIDE.md](./SKILL_CREATION_GUIDE.md) | Skill 创建与 SKILL.md v2.0 |
| [测试手册.md](./测试手册.md) | 单元/集成/E2E 测试 |
| [system-overall-design-and-phased-implementation-plan.md](./system-overall-design-and-phased-implementation-plan.md) | NetOps Agent 总体设计、改造方案与分阶段实施计划 |
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

---

## 已归档 / 重定向

以下文档已合并至编号系列，保留跳转：

| 旧文档 | 新文档 |
|--------|--------|
| [API文档.md](./API文档.md) | → [04_API 接口文档](./04_API%20接口文档.md) |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | → [10_部署安装手册](./10_部署安装手册.md) |
| [logging-handbook.md](./logging-handbook.md) | → [08_日志规范 & 日志字典](./08_日志规范%20&%20日志字典.md) |

---

## 快速链接

- 项目根 [README.md](../README.md)
- 脚本说明 [scripts/README.md](../scripts/README.md)
- Web 开发 [web/README.md](../web/README.md)
- OpenAPI：http://localhost:8000/docs（开发环境）
