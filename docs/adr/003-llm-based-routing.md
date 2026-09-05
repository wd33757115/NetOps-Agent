<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->


# 003: LLM-based Routing（基于 LLM 的语义路由）

> 演进核对（2026-09-06）：原“纯 LLM 路由”方案已演进为触发匹配、可选语义检索、Workflow Intent 与 ExecutionPlan 协作。下文保留取舍记录，不表示所有请求都调用 LLM 路由，也不提供当前准确率证明；以[Supervisor 指南](../12_Supervisor路由与Skill执行流程.md)为准。

## 状态

已接受

## 上下文

Skill 系统需要将用户查询准确路由到合适的 Skill。早期方案使用 Embedding 进行预筛选，但存在以下问题：
1. Embedding 匹配语义理解能力有限
2. 需要维护 Embedding 向量数据库
3. 触发词匹配不够灵活
4. 无法处理复杂的语义理解场景

## 决策

采用纯 LLM 路由策略：

### 1. 两阶段路由

#### 阶段 1：关键词快速匹配
- 使用 Skill 的 triggers 字段进行快速预筛选
- 匹配到关键词的 Skill 进入候选列表
- 无匹配时使用所有 Skill

#### 阶段 2：LLM 精准判断
- 将用户查询和候选 Skill 列表传给 LLM
- LLM 判断哪个 Skill 最适合
- 返回匹配结果和置信度

### 2. LLM Prompt 设计

```
你是一个智能路由助手，请将用户查询路由到最合适的 Skill。

用户查询: {query}

可用 Skills:
{skills_list}

请按以下格式输出 JSON:
{
  "skill": "最匹配的 skill-name",
  "confidence": 0.0-1.0,
  "reason": "选择理由"
}
```

### 3. 置信度阈值

- 默认阈值：0.7
- 低于阈值时 fallback 到 RAG

## 理由

1. **更准确** - LLM 语义理解能力远超 Embedding
2. **更灵活** - 无需维护向量数据库
3. **更简单** - 减少系统复杂度
4. **可优化** - 可以通过优化 Prompt 提升准确率

## 后果

### 正面
- 路由准确率显著提升
- 系统更简洁
- 维护成本降低

### 负面
- 路由延迟增加（需要调用 LLM）
- Token 消耗增加
- 依赖 LLM 稳定性

## 替代方案

### 方案 A：纯 Embedding 路由

- 速度快
- 但准确率较低

### 方案 B：Hybrid（Embedding + LLM）

- 兼顾速度和准确率
- 但系统更复杂

### 方案 C：规则匹配

- 速度最快
- 但灵活性差，难以覆盖所有场景

## 实现细节

### 性能优化

- 添加路由结果缓存
- 对相同查询直接返回缓存结果
- 异步处理，不阻塞主流程

### Fallback 策略

- 置信度低于阈值 → RAG
- LLM 调用失败 → RAG
- 无匹配 Skill → RAG

## 相关决策

- [001: Skill System Architecture](001-skill-system-architecture.md)
- [002: Progressive Disclosure](002-progressive-disclosure.md)
