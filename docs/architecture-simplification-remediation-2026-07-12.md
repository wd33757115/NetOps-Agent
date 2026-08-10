# 前端架构治理与最佳实践改造计划（2026-07-12）

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

## 结论（先给结论）

这套 UI 的闭环方向是对的（`Devices -> EvidenceTimeline -> Parser Control -> 资产链路`）。本轮已完成构建恢复、深链归约、工作区按需查询、状态语义、治理面板拆分和自动化门禁，当前从“不可稳定验证”进入“可持续演进”状态。

仍需继续收敛 `today` 与 `parser-assets` 两个高复杂度面板；它们没有阻断发布，但仍是后续维护成本的主要来源。

最关键的判断是：**先“打通链路可维护性”再“追求页面功能堆叠”**。

## 2026-07-19 职责边界修正

- `设备管理 -> 巡检运行` 承载在线巡检与离线巡检，二者都只消费已经 active 的 Parser 和规则资产。
- `Skills -> Parser 控制面 -> 资产生产` 承载离线文件上传、预检、命令级资产生产、LLM 候选生成、工程复核与审批。
- 正式巡检发现缺模板或解析失败时只记录证据和降级状态，不自动创建资产生成任务。
- 双周期离线数据是规则回放证据，固定不发布正式事件；正式网络现状报告只能由巡检链路生成。
- 规则样本 API 使用 `/api/v1/parser/asset-source-jobs`，离线巡检 API 使用 `/api/v1/patrol/offline-jobs`；两类任务以 `purpose` 隔离，不是迁移别名。

---

## 现状逐项判断（是否存在）

### 1）可追溯链路（已建立，继续强化）

- `EvidenceTimeline -> Parser Control` 深链机制已存在，`focus/id/workspace/subtab` 参数链路已可工作。
- `parserControlFocus.ts` 已有 focus 映射与容错解析逻辑，`resolveParserControlFocus` 可以落到对应 workspace。
- `ParserControlTab` 的 `event-rules` 表已接入 `rowClassName` 高亮，支持从 Evidence 回跳定位。

> 结论：**方向正确，属于“已建立”级别，不是“未建立”问题。**

### 2）组件边界与可维护性（**存在较大问题**）

- `web/react_frontend/src/components/skills/ParserControlTab.tsx` 已由 2352 行降至约 1730 行，治理、运行时资产、版本资产和详情抽屉已拆分；任务与 Parser 资产编排仍偏重。
- 解析/资产链路的专用组件虽已抽出来（如 `RuleAssetChainWorkspace`、`RuntimeAssetsWorkspace`），但主文件仍承担大量 UI 拼装，扩展单一场景会引入连带回归风险。
- `parserWorkspaceModel`、`useParserControlData` 已在做模型与数据分层，`ParserControlTab` 仍未与这些能力形成“稳定的一对一映射”。

> 结论：**存在明显工程复杂度问题，后续很难稳定扩展。**

### 3）状态治理与错误体验（**存在问题**）

- `loading`、错误提示、空态的策略在不同 tab 不统一；很多列表都依赖自定义样式，缺乏统一容器。
- 原语法级构建缺陷已修复，并由 `check:utf8 + lint + unit + build` 组合门禁防止回归。
- 深链参数回退已展示原因、建议动作，并允许一键规范化 URL。

> 结论：**状态治理未统一，且存在阻塞编译的直接缺陷。**

### 4）用户体验与可用性（中等）

- 视图信息较完整，但大量表格列定义重复，用户需要在多个 tab 重复确认同类动作。
- 关键操作（如 `snapshot` 链接）虽然可跳转，但缺少统一“动作后反馈”模式（成功/失败/重试）与统一文案。
- Parser 控制面的关键开关、筛选器和横向滚动表格已补齐语义；axe 已覆盖桌面和移动端。

> 结论：**可用，但一致性不足。**

### 5）回归保障（**薄弱**）

- focus 归约、表格空态/错误重试、规则资产碰撞阻断和 EvidenceTimeline 已有单测。
- Playwright 已覆盖按需加载、证据深链高亮、非法参数回退、审批碰撞阻断、导航、移动端布局和 axe 检查。

> 结论：**验证链不完整，仍偏向手工验收。**

---

## 总体整改策略（前端设计最佳实践）

原则：  
1. **先稳定（可建构、可调试、可回归）**；  
2. **再优化（性能、体验、一致性）；**  
3. **最后增强（自动化覆盖与运营级可观测）。**

---

## 分阶段改造计划（建议执行）

### Phase 0：编译与可观测性修复（优先级 P0，1~2 天）

#### 目标
- 先把 Parser Control 视图恢复到可构建、可预览、可回归的健康状态。

#### 任务
1. 修复 `web/react_frontend/src/components/skills/ParserControlTab.tsx` 中的字符串语法缺陷（如 `message.success`、`message.error` 未闭合）。
2. 增加一次最小构建门禁：
   - `npm --prefix web/react_frontend run build` 必须通过。
3. 增加组件层日志（只用于开发调试）：在 `focus` 解析失败时记录 `kind/id/workspace/subtab`。
4. 在 `showInvalidFocusBanner` 增加回退建议文本（目前已具备方向，但建议再补“可操作动作”）。

#### 验收标准
- 前端构建通过；
- 深链参数异常时不崩溃，并能回退到明确工作区；
- `Parser Control` 首屏可加载。

---

### Phase 1：组件拆分与数据边界（P1，1~2 周）

#### 目标
- 建立稳定的 UI 模块边界，减少 `ParserControlTab` 的变更半径。

#### 任务
1. 按 workspace 切分主视图：
   - `today` -> `ParserJobsPanel`
   - `parser-assets` -> `ParserAssetsPanel`
   - `rule-assets` -> `RuleAssetsPanel`
   - `governance` -> `ParserGovernancePanel`
2. 为每个 panel 建立统一表格工厂（`StandardTableShell`）：
   - 统一 loading/empty/分页/错误提示
   - 固定 `rowKey`、`locale.emptyText`、`scroll`、`size`
3. 将字段格式化与状态胶囊统一映射到 `parser/parserUi.tsx`（如 status/chip/摘要文本）。
4. 将“弹窗/抽屉控制”收敛为统一 hooks（减少重复开闭逻辑）。

#### 验收标准
- 单文件行数显著下降，职责边界可读；
- 任一 tab 的修改不需要触及其他 tab 的核心逻辑；
- 关键表格在空态与 loading 表现一致。

---

### Phase 2：交互一致性与回溯体验（P1，1 周）

#### 目标
- 将“事件可追溯”从“能点”提升到“能理解 + 能修复”。

#### 任务
1. 统一 focus 落位：
   - 统一 `hasFocusMatched` 使用入口（`parser-control` 与 `RuleAsset` 子组件都复用）。
2. `EvidenceTimeline` 与 `ParserControl` 约定：
   - `no_event_reason / collision_reason / policy_blocking_reason` 全链路展示；
   - 在 Parser 页面中对同一个 `change`、`snapshot`、`rule` 支持 2 层动作入口（主动作 + 证据动作）。
3. 优化 `showInvalidFocusBanner`：
   - 展示回退原因 + 推荐点击入口（例如“转到 Rule Assets/Change Policies”）。
4. 统一按钮语义：
   - 关键按钮都加 `aria-label`；
   - 破坏性操作添加二次确认和明确文案。

#### 验收标准
- Evidence 的跳转在 Parser 页面上 100% 可落位到对应 tab；
- 同一个 id 的高亮行为在所有子表保持一致；
- 低优先级操作不误触发，破坏性操作有确认。

---

### Phase 3：性能与可维护性优化（P2，1~2 周）

#### 目标
- 降低 Parser Control 页面对大数据/高频刷新的系统压力。

#### 任务
1. 将 polling 策略参数化：
   - 针对“高变更 tab”与“静态 tab”设置不同刷新频率；
2. `useParserControlData` 引入“按 workspace 精准拉取”与“可取消请求”；
3. 对大列表接入虚拟化预案（至少预留接口），并对固定列宽/文本截断做统一策略；
4. 对关键查询错误做分类标记，区分“网络问题/权限问题/空数据问题”。

#### 验收标准
- 常规巡检时内存/CPU 采样曲线下降；
- 不必要轮询不再持续请求；
- 关键 tab 的首屏渲染可控，交互卡顿明显下降。

---

### Phase 4：回归体系与发布门禁（P2，1 周）

#### 目标
- 将“人工验收”转为“自动化门禁”。

#### 任务
1. 新增/完善测试：
   - `parserControlFocus.test.ts`：参数映射 + fallback 场景；
   - `ParserControlTab.test.tsx`（或拆分到 panel 测试）：高亮、过滤、空态、操作按钮状态；
   - `EvidenceTimeline` 新增 focus 行为断言（已有基础可扩展）。
2. 新增 Playwright e2e：
   - 证据链路跳转；
   - `no_event` 原因摘要可见；
   - event-rule 命中定位稳定。
3. 在 `docs` 和前端 README 增补“Parser Control 运行检查清单”：
   - 构建；
   - 核心页面快照；
   - 深链回归用例。

#### 验收标准
- 引入回归测试后，主干分支能在 CI 中稳定运行；
- 关键链路不再出现 silent break（跳转失效/高亮失真）。

---

## 交付清单（与本文件绑定）

### 实施状态（2026-07-12）

| 阶段 | 状态 | 已落地证据 | 后续项 |
|---|---|---|---|
| Phase 0 | 完成 | 构建恢复、UTF-8 扫描、深链开发日志、可操作回退提示 | 无阻断项 |
| Phase 1 | 部分完成 | `ParserGovernancePanel`、详情抽屉、规则版本与运行时资产组件、`StandardTableShell` 已进入真实渲染链路 | 继续拆分 `ParserJobsPanel`、`ParserAssetsPanel`，收敛父层 mutation/state |
| Phase 2 | 完成 | canonical focus、冲突参数回退、统一高亮、Evidence 原因分类、审批碰撞硬阻断 | 后续新增 focus 类型时同步扩展 resolver 测试 |
| Phase 3 | 完成 | workspace 按需加载、离开工作区取消查询、可见页分级 polling、错误分类 | 大数据量达到压测阈值后再启用虚拟化 |
| Phase 4 | 完成 | 19 个前端单测、18 个功能 E2E、4 个 axe 场景、20 个视觉快照场景、生产构建 | CI 需持续执行 `check:ci` |

| 目录 / 文件 | 本阶段目标 | 状态 |
|---|---|---|
| `web/react_frontend/src/components/skills/ParserControlTab.tsx` | 修复构建并降低主组件职责 | 已修改，后续继续拆分任务/Parser 资产区 |
| `web/react_frontend/src/components/skills/parser/` | 统一分层组件、focus、查询与表格规范 | 已修改 |
| `web/react_frontend/src/components/devices/EvidenceTimeline.tsx` | no_event/collision 原因链路增强 | 已修改 |
| `web/react_frontend/src/components/skills/parser/parserControlFocus.ts` | canonical focus 与可观测回退 | 已修改并测试 |
| `web/react_frontend/src/components/skills/parser/useParserControlData.ts` | 查询策略与加载边界优化 | 已修改 |
| `web/react_frontend/src/components/skills/parser/parserUi.tsx` | 统一状态与错误语义 | 已修改 |
| `web/react_frontend/src/components/devices/EvidenceTimeline.test.tsx` | 证据链路回归 | 已修改并测试 |
| `web/react_frontend/e2e/parser-workspace.spec.ts` | 深链、工作区、审批与可访问性门禁 | 已新增并测试 |

### Parser Control 运行检查清单

发布前依次执行：

```powershell
npm --prefix web/react_frontend run check
npm --prefix web/react_frontend run test:a11y
npm --prefix web/react_frontend run test:e2e
```

人工冒烟检查：

1. 打开 `/skills/parser`，确认今日待办只加载任务类 API。
2. 切换到 Parser 资产、规则资产、治理与审计，确认对应 API 按需加载且页面无横向溢出。
3. 打开带 `focus/id/workspace/subtab` 的 Evidence 深链，确认进入 canonical 工作区并高亮目标行。
4. 使用冲突 workspace 的深链，确认显示回退原因；点击“使用推荐视图”后 URL 被规范化。
5. 对 `collision_count > 0` 的候选规则确认审批按钮不可用并显示阻断原因。
6. 在桌面与移动视口检查键盘 Tab 顺序、横向表格聚焦和关键操作可读名称。

---

## 风险提醒（执行前确认）

- 这是前端“大改造”型工作，不建议一次性全部并行推进；需按 phase 依次推进，保证每个阶段都有可发布最小形态。
- 编译阻塞已经解除；后续任何字符编码或语法回归会由 `npm run check` 拦截。
- 你若决定上线里程碑，需要同步确认后端 API 契约不变（尤其是 focus 链路字段）。

---

## 结语

UI 的方向不坏，最大的缺口在于**工程化执行质量**。  
先把 Parser Control 的可编译、可测试、可追溯这三件事做透，后面任何“规则资产生成体验增强”都会更容易落地，也更不容易回退。
