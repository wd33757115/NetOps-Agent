<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 工作台桌面与巡检应用迁移实施计划

状态：首期代码已实现；部署与真实业务环境验收待执行。原计划保留作范围对照。  
日期：2026-09-05  
依据：用户引用的“改造公文写作”讨论、本地仓库代码，以及本轮明确的首期范围。

## 1. 首期目标

在左侧增加“工作台”，进入后呈现桌面式应用入口：图标可拖动、可新建文件夹归类，底部提供常用应用 Dock，布局按用户保存。首期接入两个已有 UI 的应用：**网络巡检、巡检规则生成**。

双击桌面应用进入独立业务页面；页面提供“返回工作台”。业务仍由现有 API、Workflow 和 Skill 执行。

首期完成标准是“桌面可用 + 两个应用业务闭环可用”，不能仅给旧页面增加两个快捷图标。

本期边界：

- 网络巡检包含现有在线巡检、离线巡检、批次历史、结果、报告和证据入口。
- 巡检规则生成承接现有巡检规则资产 UI，包括检查项目录、样本导入，以及现有生成、验证、审核、发布和运行资产入口。
- 防火墙策略生成、公文写作在其专用 UI 建成后接入；首期桌面不放无可用页面的占位应用。
- 设备资产管理、Skill 管理继续承担配置和管理职责；报告先作为巡检应用内部能力。
- 首期做单桌面、一层文件夹、Dock；多桌面、嵌套文件夹、框选、应用叠放自动建文件夹后续再做。
- 不引入浮动多窗口、虚拟文件系统、进程管理；本期不重构业务执行引擎。

## 2. 已核实的代码现状

以下路径均相对于仓库根目录。

| 现状 | 代码位置 | 实施影响 |
|---|---|---|
| React 18、Router 6、Zustand 4、Ant Design 5、React Query 3；尚无 dnd-kit | `web/react_frontend/package.json` | 保留现有技术栈，拖拽依赖单独验证和锁版 |
| 路由有 `/devices`、`/skills/parser`，没有工作台和独立巡检应用 | `web/react_frontend/src/App.tsx` | 新增工作台及应用路由 |
| 页面自行包含 Shell；AppLayout 仅渲染 Outlet | `web/react_frontend/src/components/Layout/AppLayout.tsx` | 新业务页面只能包一层 Shell，不能嵌套整个旧 Page |
| Shell 仅有 page/chat 两种模式，主导航实际来自 ChatShellSidebar | `web/react_frontend/src/components/Layout/GrokShellLayout.tsx`、`components/chat/ChatShellSidebar.tsx` | 改造活动导航，不把旧 NavMenu 当作主入口修改 |
| 侧栏可折叠，但当前 collapsed 是组件 useState，无本地偏好持久化 | `web/react_frontend/src/components/chat/ChatShellSidebar.tsx` | 不能沿用历史讨论中“已经持久化”的假设 |
| 巡检位于 `/devices?tab=patrol`，大量查询、状态、操作和弹窗位于 DevicesPage | `web/react_frontend/src/pages/DevicesPage.tsx`、`components/devices/PatrolOperationsPanel.tsx` | 抽取完整巡检容器及依赖，单独搬 Panel 无法工作 |
| 规则 UI 由 SkillsPage 包装 ParserControlTab | `web/react_frontend/src/pages/SkillsPage.tsx`、`components/skills/ParserControlTab.tsx` | 可先复用控制面主体，再调整应用外壳和导航 |
| 规则资产生产主视图已归并为检查项目录、样本导入 | `web/react_frontend/src/components/skills/parser/RuleAssetProductionWorkbench.tsx` | 迁移保留当前结构，避免恢复已合并的旧工作区 |
| 规则候选基于 CheckItem 标准语义；样本/TextFSM 用于解析和映射验证 | `web/react_frontend/src/components/skills/parser/FocusedRuleAssetGeneration.tsx` | 迁移不恢复“必须先有样本才能生成规则”的旧依赖 |
| 路由携带 workspace、subtab、focus、id 及来源任务等上下文 | `web/react_frontend/src/components/skills/parser/parserControlFocus.ts` | 必须兼容深链接及历史参数归一化 |
| 用户登录与 API 入口在 Django BFF；未找到现成桌面偏好接口 | `web/django_backend/bff/auth.py`、`bff/urls.py` | 桌面布局建议新增为 Django 用户偏好数据 |

## 3. 产品与路由方案

| 入口 | 首期目标路由 | 内容 |
|---|---|---|
| 工作台 | `/workbench` | 桌面、文件夹、Dock |
| 网络巡检 | `/apps/patrol` | 默认在线巡检，应用内切换离线巡检；保留结果、证据、报告入口 |
| 巡检规则生成 | `/apps/patrol-rules` | 默认检查项目录，保留样本导入与规则资产工作流 |
| 规则来源任务详情 | `/apps/patrol-rules/source-jobs/:jobId` | 复用现有详情页面内容，返回同一应用 |
| 设备管理 | `/devices` | 设备资产、连接、分组与相关配置 |
| 技能管理 | `/skills` 等现有路由 | Skill、Workflow 和管理能力 |

桌面默认只摆放两个可用应用，默认 Dock 固定这两个应用。图标只显示名称，说明放入工具提示或应用信息。网络巡检和规则生成均有独立标题、主操作和“返回工作台”。

左侧保留现有系统/管理入口，增加“工作台”，不再追加独立巡检菜单。进入工作台默认折叠成窄图标栏并隐藏历史对话，允许手动展开；这次展开仅影响工作台当前会话。离开工作台恢复普通页面原有偏好。应用页面继续复用现有 Shell，不同时全面重做其他页面导航。

旧入口兼容规则：

- `/devices?tab=patrol` 迁移至 `/apps/patrol`，去掉旧 tab 标志，保留其他查询参数和 hash；`/devices` 的设备管理视图继续可用。
- `/skills/parser` 及来源任务详情路径映射到对应新路由，保留 query/hash；旧参数继续经过现有 focus 解析器归一化。
- 集中新增业务链接构造函数，更新通知、证据、报告、缺口修复及页面返回链接；禁止散落复制新的路径字符串。
- 新页面不依赖临时路由 state 才能加载。当前批次、在线/离线子页等需要刷新恢复的状态写入 URL；未提交表单草稿单独处理。
- 兼容跳转使用 replace，验证浏览器前进/后退，避免跳转环。

## 4. 技术设计与责任边界

### 4.1 桌面 Shell

建议新增：

```text
web/react_frontend/src/
  apps/appRegistry.ts
  apps/appRoutes.ts
  pages/WorkbenchPage.tsx
  pages/PatrolAppPage.tsx
  pages/PatrolRulesAppPage.tsx
  components/desktop/
    DesktopCanvas.tsx
    DesktopIcon.tsx
    DesktopFolder.tsx
    DesktopDock.tsx
    DesktopContextMenu.tsx
    desktopLayout.ts
    desktop.css
  components/patrol/PatrolWorkspace.tsx
  hooks/usePatrolWorkspace.ts
  store/useDesktopStore.ts
  services/desktopApi.ts
```

这些是拟新增文件，不代表当前已经存在。现有巡检展示组件可保留原路径，优先抽取容器和查询逻辑，不为改目录批量移动全部组件。

App Registry 统一保存 id、名称、图标、入口、启用状态和访问条件；布局只引用 appId。第一期使用与既有页面一致的访问规则，不凭空新增 permission 字符串。前端可见性、路由保护和后端授权分别保留。

新增 `workbench` Shell 模式，桌面使用独立画布区域，避免继承普通页面最大宽度和多层滚动。CSS 限定在桌面作用域内。

拖拽按已有讨论，以 `@dnd-kit/core` 和 `@dnd-kit/sortable` 作为首期验证候选。官方已将该 API 文档标为 Legacy；实施前验证 React 18、StrictMode、触屏和跨容器行为，通过后锁定版本，不混用新旧 API。若技术验证失败，在 P0 内形成替代决策后再实现，不在业务迁移中途换拖拽框架。

参考：[官方 Legacy Sortable 文档](https://dndkit.com/legacy/presets/sortable/overview/)、[官方迁移指南](https://dndkit.com/react/guides/migration/)。本计划不沿用历史讨论对第三方完整 WebOS 成熟度的判断。

### 4.2 布局与交互合同

布局建议采用带类型约束的节点模型：

```ts
type DesktopNode =
  | { id: string; kind: 'app'; appId: string;
      containerId: 'desktop:0'; col: number; row: number }
  | { id: string; kind: 'app'; appId: string;
      containerId: `folder:${string}`; order: number }
  | { id: string; kind: 'folder'; name: string;
      containerId: 'desktop:0'; col: number; row: number }

interface DesktopLayout {
  schemaVersion: 1
  revision: number
  nodes: DesktopNode[]
  dockAppIds: string[]
}
```

- 拖动时跟随指针，落下时吸附逻辑格子；存 col/row，不存屏幕像素。
- 应用单击选中、双击打开；Dock 单击打开；键盘支持 Enter 打开、Esc 取消，菜单提供移动替代操作。
- 桌面空白右键可新建文件夹；文件夹单击打开浮层，可重命名、拖入、拖出、内部排序。
- 同桌面普通占位互换；拖到文件夹中心区域表示加入文件夹。落点预览必须明确区分这两种意图。
- 文件夹只在桌面上，不允许嵌套；解散文件夹把内部应用放回空位，不删除应用。
- 每个 appId 只拥有一个桌面/文件夹节点；Dock 是额外快捷引用，固定不搬走原图标，移除只取消固定。
- 跨容器移动一次提交完整布局；取消拖动恢复原状，避免图标丢失或重复。
- 窗口缩小或浏览器缩放时，生成可见布局投影并提供滚动；仅 resize 不回写用户存储位置。新拖动落点需与规范布局协调，避免隐藏占位冲突。
- 手机使用点击打开和菜单管理；不把双击、右键或精细拖拽作为唯一操作途径。
- 注册表变化时校验并去重；暂时禁用/无权限应用隐藏但保留位置引用，新增可用应用填入空位。未知 appId 默认保留兼容信息，只有明确移除清单才清理。

### 4.3 用户布局持久化

建议由 Django BFF 新增 `DesktopPreference` 模型，与现有用户一对一关联，保存 layout JSON、revision、updated_at；使用 Django migration，不放入巡检业务 Alembic 数据模型。

拟新增接口：

```text
GET /api/auth/me/desktop-layout/
PUT /api/auth/me/desktop-layout/
```

接口复用现有登录认证及 BFF 响应封装，服务端从已认证用户确定归属。PUT 校验 schema、节点唯一性、容器引用、坐标、文件夹名称及负载大小；更新使用原子 revision 比较，冲突返回 409，不能静默覆盖。

前端本地立即更新，约 600ms 防抖保存；串行发送，保存中发生的后续修改继续排队，成功回包不得覆盖更新的本地编辑。读取失败时提示重试，不能把默认布局自动覆盖到服务器。

localStorage 仅作为按 userId 和 schema 隔离的缓存/待同步草稿。离开页面时保留待保存状态；退出登录清空内存状态并取消旧用户请求，防止跨账号串用。出现冲突保留本地草稿，提示加载服务器版本后重试；不做自动强制覆盖。正式验收包含刷新、重新登录与跨标签页冲突。

## 5. 分步实施与交付

以下为一名熟悉仓库的开发者按顺序完成的粗估，共 **10–15 个工作日**；不含既有业务缺陷修复、外部测试环境等待或新增产品需求。P0 后依据抽取范围修订估算。

| 步骤 | 粗估 | 主要工作 | 完成条件 |
|---|---:|---|---|
| P0：迁移基线与拖拽验证 | 1 天 | 记录两个旧入口和关键深链接；梳理 DevicesPage 的巡检依赖；验证桌面/文件夹/Dock 跨容器拖拽；记录当前失败基线 | 组件归属清单、旧新 URL 映射、拖拽选型可以实施 |
| P1：桌面与应用外壳 | 2–3 天 | App Registry、工作台模式、图标拖动、文件夹、Dock、临时本地布局；新增两应用路由与页面标题/返回入口 | 桌面交互完整，两应用能以独立路由打开现有主体；此时为内部里程碑 |
| P2：网络巡检迁移 | 3–4 天 | 抽取查询、轮询、执行/取消/重试、范围选择、预检和弹窗；复用在线/离线及报告组件；整理 URL 状态 | 在独立巡检应用中可选择设备/分组并执行、查看结果、导出；设备管理仍可独立使用 |
| P3：巡检规则生成迁移 | 1–2 天 | 新页面承载 ParserControlTab；迁移来源任务详情；更新所有业务链接；旧路由兼容 | 从目录或样本导入进入现有流程，生成/验证/审核/发布/资产查看入口完整可达 |
| P4：布局服务与恢复 | 1–2 天 | Django 模型/API/migration、串行防抖保存、账号隔离、revision 冲突与失败恢复 | 布局刷新和重新登录保持，多标签页不静默覆盖 |
| P5：回归与切换 | 2–3 天 | 单元、组件、浏览器业务回归；桌面与窄屏视觉验收；文档更新；验证开关回退 | 全部首期验收项通过，工作台成为推荐业务入口 |

P1 可以展示桌面和应用入口，P2/P3 完成真实迁移，P4/P5 后才算首期交付。实施顺序允许 P2 发现的共享状态问题先处理，再推进 P3，避免带着重复副作用完成切换。

### P2 的关键拆分

1. 将巡检查询、分页、当前批次与操作 mutation 收敛到巡检容器/hook；同一路由只保留一份轮询和事件订阅。
2. 抽出共享设备范围选择组件，让巡检应用可按设备/分组发起任务，复用当前预检与执行请求。
3. 设备页保留设备 CRUD、连接测试、分组、Profile 配置等管理职责；“发起巡检”可携带范围进入巡检应用。
4. 保留现有生产就绪检查、生命周期约束、报告签发和证据关联。离开页面不取消服务端任务，回来按任务 ID 恢复；未提交表单需要退出提示或会话草稿。

### P3 的关键拆分

1. 第一轮复用 ParserControlTab 的完整业务内容，不重写生成和审批逻辑。
2. 新应用名称为“巡检规则生成”，页面内继续沿用准确的检查项、命令适配、样本、规则与发布术语。
3. 检查项目录与样本导入保持现有主视图；审批、发布、运行资产保留现有入口，不新增一套平行状态机。
4. 保留“CheckItem 标准语义生成规则、样本验证解析映射”的当前边界，并验证巡检结果跳转规则依据再返回的完整路径。

## 6. 测试与验收清单

| 类别 | 必须通过的场景 |
|---|---|
| 桌面 | 默认两个应用；移动后刷新位置保持；建文件夹、改名、拖入拖出、解散后应用仍在；Dock 去重/排序/取消固定 |
| 交互边界 | 拖动不误触打开；Esc 取消；键盘/触屏可操作；缩放、窄屏和侧栏展开后图标可达 |
| 导航 | 工作台往返恢复布局；普通页侧栏偏好不被改写；新旧 URL、query/hash、详情刷新、前进后退正确 |
| 在线巡检 | 设备/分组选择、预检、创建、运行状态、取消/重试、批次历史、结果/证据/报告导出 |
| 离线巡检 | 文件上传、单周期巡检、双周期比较、任务恢复、结果和产物入口 |
| 规则应用 | 检查项目录、样本任务详情、规则候选与草稿、验证、审核和发布入口可用；旧 focus 参数兼容 |
| 业务边界 | 标准语义规则生成无需样本；生产巡检不因页面迁移自动触发模型生成；原有发布门禁及授权生效 |
| 持久化 | 无布局首次初始化、保存失败重试、读失败不覆盖、离页待保存恢复、账号隔离、409 冲突、过期回包 |
| 系统回归 | Chat、设备管理、Skill 管理、知识库、网盘和通知正常；打开桌面不加载两业务页完整数据 |

验证方式：布局算法和路由映射用单元测试；组件测试覆盖交互与请求生命周期；持久化 API 使用 Django 测试；浏览器回归沿用 Vitest/Playwright 现有体系。前端交付运行 `npm run check`，并完成受影响 E2E 和视觉用例；按现有 CI 要求完成 `check:ci` 门禁。已知基线失败与本次新增失败分别记录。

真实业务联调使用已有测试设备、样本与测试环境。Mock 可以证明 UI 行为，不能替代真实巡检执行和规则发布链路的验收证据。

## 7. 发布与回退

- 增加统一工作台功能开关，控制桌面入口、新应用推荐入口和旧路由跳转；关闭时旧业务容器仍可访问，不能留下跳转到关闭路由的入口。
- 首先部署兼容的布局 API 与新增数据表，再发布前端；初次启用前完成旧入口与新入口对照验证。
- 业务查询与操作共用同一份实现，回退只切换入口，不分叉执行逻辑。
- 回退保留用户布局数据和原业务数据，不回滚或删除巡检批次、规则资产、审批记录。
- 首期验收后，再分别为防火墙策略生成、公文写作编写专用 UI 计划；届时复用 Registry、Shell、权限和布局能力。

## 8. 本次计划交付状态

P0–P4 已完成首期代码实施。P2 采用共用 `DeviceOperationsWorkspace` 的方式保持单一业务实现，新增独立巡检页面与设备范围选择、会话草稿；暂未把共享容器进一步拆成所有领域 hook。

P5 自动化结果：前端检查及构建通过，36 个单元/组件测试文件共 167 项通过；Django 布局接口 5 项及既有 BFF 回归 10 项通过；浏览器 E2E 25 项通过，3 项桌面专用交互在手机项目中按设计跳过（手机替代操作已单独验证）。桌面、平板、手机四种尺寸的 24 项视觉用例通过，已人工查看主要工作区截图。

后续按用户要求，已对当前本地 PostgreSQL `localhost:5432/netops_agent` 执行并验证布局表迁移；工作台视觉调整为 macOS 风格，导航收进左上角 NetOps 菜单，普通页面导航偏好不变。没有执行真实设备巡检或真实模型生成发布。部署步骤、回退开关和后续环境验收要求见 [工作台接入说明](./workbench-apps-guide-2026-09-05.md)。
