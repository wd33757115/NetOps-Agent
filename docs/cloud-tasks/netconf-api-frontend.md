# 云端任务：netconf-api 的完整前端

先读 [公共约定](README.md)。分支：`cloud/netconf-api-frontend`。

## 1. 背景

netconf-api 是维护 NETCONF API 定义的 App（`src/apps/netconf_api`，App id `netconf-api`，当前版本 0.3.1）。它的工作方式：
- 一条"定义"把设备 NETCONF 数据模型里的一张表映射成规则治理某个检查项的值行，和 CLI + TextFSM 产出的值行同名、同写法；
- 拓扑采集时 NETCONF 优先，表不可用时回退 CLI。

背景、契约和实测依据见 [docs/netconf-api-handoff.md](../netconf-api-handoff.md)。

后端已完成并测试过。前端现在只是一个只读的占位页（`frontend/Page.tsx`，68 行），只列出已发布的定义。本任务把它做成完整的前端。

## 2. 后端接口

前端用 `import { api } from '@netops/config/api'`，路径前缀 `/apps/netconf-api`。请求和返回结构以 `backend/service.py` 为准，下面只列用途：

| 方法与路径 | 用途 | 请求体 / 参数 |
|---|---|---|
| `GET /definitions` | 定义列表（`_summary`：id、检查项、厂商、平台、说明、已发布版本、是否有草稿） | |
| `GET /definitions/{id}` | 一条定义的详情：各版本（`_version_view`）和草稿 | |
| `PUT /definitions/{id}/draft` | 保存草稿；`revision=0` 表示新建定义 | `Draft{body, revision}` |
| `POST /definitions/{id}/discard` | 丢弃草稿 | `Discard{revision}` |
| `POST /definitions/{id}/publish` | 把草稿发布为不可变版本；发布时检查是否覆盖了检查项的字段，不通过会报错 | `Publish{revision, note}` |
| `POST /devices/{device_id}/capabilities` | 经平台打开 NETCONF 会话，读设备 hello，记录通告的模块并匹配定义 | |
| `GET /devices/{device_id}/capabilities` | 读已记录的能力（`_capability_view`） | |
| `POST /trials` | 在设备上试运行一条定义（已发布版本或草稿），返回值行和问题清单（`_trial_view`） | `TrialRequest{device_id, definition_id, draft}` |
| `GET /trials?definition_id=&limit=` | 试运行记录列表 | |
| `GET /trials/{trial_id}` | 一次试运行的详情 | |

- **定义体**的格式由 `backend/mapping.py` 校验，种子定义见 `backend/seeds/h3c_comware7.yaml`，可以作为编辑界面要展示的字段的参照。
- **`revision`** 用于防止并发覆盖：后端在版本不一致时报冲突，前端要提示用户刷新。
- **`device_id`** 是平台设备清单（`NetworkDevice`）的 ID。目前 netconf-api 没有列设备的接口。如果界面需要选设备，可以在 netconf-api 后端加一个最小的 `GET /devices`：列出设备清单里的设备（名称、管理地址、厂商、平台），加上最近一次能力发现的摘要。要求：
  - 配单测（参照 `tests/unit/test_netconf_api_service.py`）；
  - 提升 `app.yaml` 的版本号；
  - 不改其他接口的语义。

  **除此之外不改后端。**

## 3. 页面要覆盖的工作

左侧工作流导航建议分 4 步，具体布局由你设计：
1. **接口定义**：按厂商、模块、检查项浏览定义，能看到状态（已发布版本号、是否有草稿）、对应的检查项、说明。
2. **编辑与发布**：
   - 查看某个版本的定义体：模块/表/行、每个值从哪个叶子取、类型、代码对照表、关联、是否必填。
   - 编辑草稿：后端返回的校验错误要原样显示。
   - 丢弃草稿；发布时填说明，发布失败时显示覆盖检查的结果。
   - 版本历史，以及两个版本之间的差异。

   编辑体验可以先用结构化表单加 YAML/JSON 源码视图，不用做成可视化拖拽，大道至简。
3. **设备能力**：选设备，执行能力发现；展示通告的模块和版本，以及哪些定义可用、哪些不可用。发现失败时显示失败码。
4. **试运行**：
   - 选定义和设备（可以选"用草稿"），运行。
   - 结果展示值行表格（列就是检查项的值名）和问题清单（未映射代码、关联未命中、必填缺失等，要醒目）。
   - 显示回包大小和耗时。
   - 可以浏览历史记录。

另外：
- 拓扑的采集问题接口（`src/apps/topology/backend/api.py` 的 `GET /issues`，前缀 `/apps/topology`）记录了 NETCONF 回退到 CLI 的原因。可以在"设备能力"或首页给一个只读的入口，可选。
- 界面文字用中文。空状态、加载中、无权限（接口返回 403）、出错，都要有明确的提示。

## 4. 风格

严格沿用防火墙策略 App 的外壳和令牌，见公共约定 §3 的"前端风格"。CSS 类名前缀用现有占位页的 `nc-`，占位页里已有外壳的骨架可以直接沿用。按钮、表格、标签等用 antd 组件。

## 5. 测试和验收

- **组件测试（vitest）**：参照 `src/apps/device_access/frontend/Page.test.tsx` 的写法，把 `api` 模拟掉。至少覆盖：
  - 列表渲染；
  - 定义详情和版本切换；
  - 保存草稿时，后端校验错误和版本冲突都要显示出来；
  - 发布成功和失败；
  - 能力发现的成功和失败码；
  - 试运行结果（值行和问题清单都有的情况）；
  - 403 的提示。
- `npm run lint` 和 `npm run build` 通过；如果仓库本来就有的问题导致失败，写明哪些是原有的。
- 加了后端接口的话，对应的 pytest 通过。
- 提升 `src/apps/netconf_api/app.yaml` 的版本号（前端改动也算代码改动）。

## 6. 留给本地会话

- 合并、部署（`start.ps1 -UpgradeApps netconf-api`）。
- 在浏览器里对 BR1/BR2 实际走一遍"能力发现 → 试运行 → 发布草稿"。
