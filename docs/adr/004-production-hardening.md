<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ADR-004: 生产化可观测性与容错体系

> 演进核对（2026-09-06）：原 graph.py 与旧执行节点已退出；当前使用 Supervisor v2。权限失败不得执行受限操作，是否返回知识说明是单独的交互选择。下文埋点数量、Phase 状态和兼容描述属于历史背景；当前[权限](../permissions-guide.md)、[日志](../08_日志规范%20&%20日志字典.md)与[执行流程](../12_Supervisor路由与Skill执行流程.md)为运行依据。

**日期**: 2025-05-23
**状态**: Accepted

## 背景

Phase 1-3 已经完成了核心 Skill 引擎、标准化格式和管理后台。Phase 4 需要在生产环境中运行，
面临以下挑战：

1. **Skill 加载失败** — `graph.py:23` 的 `load_all_skills()` 如果抛出异常，整个 supervisor 模块无法导入，应用崩溃
2. **无执行指标** — supervisor 路由决策、Skill 执行成功/失败/耗时均不可观测
3. **权限检查缺失** — `security.py` 有完善的权限框架，但未集成到实际执行路径
4. **Disabled Skill 泄漏** — Router 不过滤 `enabled: false` 的 Skill
5. **配置缺失** — `.env.example` 中的 `SKILL_TIMEOUT`、`SKILL_MAX_RETRIES` 无对应 config 字段

## 决策

### 1. 多层容错降级

```
L1 (加载): load_all_skills() → try/except → 降级为基础 RAG 模式
L2 (路由): 触发词 → Embedding → LLM Judge，每阶段独立容错
L3 (执行): 权限检查 → Celery 异步 → RAG 兜底
```

### 2. Metrics 埋点体系

在 supervisor 两个核心节点中集成 9 个埋点，使用已有的 `src/common/metrics.py` 基础设施：

**routing 级**:
- `skill_routing_total{no_skill|skill_hit|rag_fallback}` — Counter
- `skill_routing_duration_ms` — Histogram

**execution 级**:
- `skill_execution_total{no_decision|unauthorized|async_submitted|success|error|exception}` — Counter
- `skill_execution_duration_ms` — Histogram

### 3. 非阻断权限检查

skill_executor_node 中集成 `get_security_manager().check_permission()`，权限不足时降级到 RAG 而非崩溃。

### 4. Disabled Filter

Router (`_keyword_route` + `_is_skill_enabled`) 自动跳过 `enabled: false` 的 Skill。

## 影响

| 方面 | 影响 |
|---|---|
| 应用启动 | Skill 加载失败不再阻塞启动 |
| 可观测性 | 路由决策和 Skill 执行可量化追踪 |
| 安全性 | 权限检查在 executor 中生效 |
| 操作风险 | Disabled Skill 不会意外触发 |
| 兼容性 | 完全向后兼容，metrics 为增量添加 |

## 相关文件

- `src/agents/supervisor/graph.py` — 容错 + 9 metrics 埋点 + 权限检查
- `src/skill_system/router.py` — `_is_skill_enabled()` 方法
- `src/skill_system/__init__.py` — `_keyword_route` disabled 过滤 + `reload_all`
- `src/common/config.py` — `SKILL_TIMEOUT` / `SKILL_MAX_RETRIES` / `SECRET_KEY`
- `tests/skill_system/test_e2e.py` — 10 个 E2E 集成测试用例
