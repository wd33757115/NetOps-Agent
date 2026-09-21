<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->


# 001: Skill 系统架构

> 演进核对（2026-09-22）：本文为历史决策；当前问答使用 DSH，巡检和规则使用固定业务流程。实现以[当前架构](../01_系统架构设计.md)为准，旧模块名与性能数值不代表当前版本。

## 状态

已接受

## 上下文

NetOps Agent 需要一个灵活、可扩展的能力模块系统，用于处理各种网络运维任务。当前使用 Python 类定义 Skill，但这种方式存在以下问题：
1. 需要编写代码才能添加新 Skill，门槛较高
2. Skill 定义与执行逻辑耦合
3. 难以进行版本控制和协作
4. 缺少统一的 Skill 元数据管理

## 决策

采用 Grok 风格的文件驱动 Skill 系统，主要设计如下：

### 1. Skill 定义方式

- 使用 `SKILL.md` 文件定义 Skill
- Frontmatter 存储元数据（YAML 格式）
- 文件正文存储系统指令（Markdown 格式）

### 2. 核心组件

| 组件 | 职责 |
|------|------|
| `SkillSystem` | 对外接口，协调各组件 |
| `MetadataParser` | 解析 SKILL.md 的 Frontmatter |
| `SemanticRouter` | 语义路由，匹配用户查询到 Skill |
| `SkillLoader` | 按需加载 Skill 内容 |
| `SkillCache` | LRU 缓存，减少文件读取 |
| `SkillSecurityManager` | 权限控制和审计日志 |

### 3. 目录结构

```
src/skills/
├── skill-name/
│   └── SKILL.md
├── another-skill/
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── assets/
```

## 理由

1. **低代码** - 无需编写 Python 代码即可定义 Skill
2. **易维护** - Markdown 格式便于版本控制和协作
3. **可扩展** - 支持添加脚本、参考资料等资源
4. **性能优化** - 渐进式披露 + LRU 缓存
5. **向后兼容** - 保留 Python 类 Skill 支持

## 后果

### 正面
- 降低 Skill 创建门槛
- 提升开发效率
- 便于 Skill 分享和复用
- 更好的可观测性

### 负面
- 需要迁移现有 Python Skill
- 增加系统复杂度
- 需要维护两套 Skill 系统（过渡期）

## 相关决策

- [002: Progressive Disclosure](002-progressive-disclosure.md)
- [003: LLM-based Routing](003-llm-based-routing.md)
