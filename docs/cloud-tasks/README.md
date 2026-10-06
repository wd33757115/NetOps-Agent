# 交给 Claude 云端的任务：公共约定

2026-10-06，负责人决定把部分任务交给 Claude 云端会话。每个任务一份说明，在本目录下。开工前先读本文，再读对应的任务说明。

| 任务 | 说明 |
|---|---|
| netconf-api 完整前端 | [netconf-api-frontend.md](netconf-api-frontend.md) |
| PBR 按 OpenConfig 模型实现（含文档、单测） | [pbr-openconfig.md](pbr-openconfig.md) |

## 1. 云端碰不到什么

云端从 GitHub 克隆仓库，**到不了**：
- GNS3 实验室（10.10.1.x）；
- 本机运行的平台（端口 8000/8001/3000）、PostgreSQL 和 Redis；
- 负责人本机的 `.env`（实验室密码在那里）；
- 本机的 `.runtime/`（实验脚本，不入库）。

所以：
- **不登录设备，不做在线试运行，不部署。** 只靠代码、仓库里的测试夹具（`tests/fixtures/`）和单测完成任务。
- 需要实验室或在线平台验证的部分，在任务结束报告里列为"留给本地会话"，由本地会话合并后实测。
- 任何密码、密钥都不要写进代码、文档或提交，也不要去找。

## 2. 分支与提交

- 从 `feat/network-twin` 拉一个新分支：`cloud/<任务名>`，例如 `cloud/netconf-api-frontend`、`cloud/pbr-openconfig`。
- **只推自己的分支**，不要直接推 `feat/network-twin`，不要强推，不要改写已推送的历史。
- 提交时逐个写明路径，不用 `git add .` 或 `git add -A`。
- 不要提交 `web/react_frontend/src/apps/generated.ts` 和 `generated.meta.json`，它们是部署时生成的。
- 提交信息用英文，祈使/陈述句，说明为什么；末尾加：
  `Co-Authored-By: Claude <noreply@anthropic.com>`
- App 有代码改动时，提升该 App `app.yaml` 里的 `version`（部署靠代码摘要和版本号识别变更）。

## 3. 项目的工作约定

以下是负责人长期以来的决定，任务中必须遵守。

- **大道至简，IETF 优先。** 能用已有标准模型就不自造字段，也不增加项目自定义 YANG；没有 IETF/IEEE 标准时，用负责人认可的公开模型（例如 PBR 用 OpenConfig）。不要过度设计。
- **YANG 原始文件从 GitHub 上对应的项目取**（负责人 2026-10-06）：缺哪个模块，就去发布它的项目找。IETF、IEEE 等标准模块和多数厂商的模块在 YangModels/yang（厂商在 `vendor/<厂商>`），OpenConfig 在 openconfig/public。固定一个提交，按 `models/network-twin/openconfig/sources.lock.json` 的格式记 URL 和 SHA-256，不改源码。GitHub 上找不到的，写明查过哪里，再从设备用 `get-schema`（RFC 6022）取，并记下设备型号和软件版本。H3C Comware 的模块就属于这种情况（2026-10-06 查过，见 `models/network-twin/m5/README.md`）。
- **统一数据流水线**（[实施文档 §13](../network-digital-twin-implementation.md)）：原始响应（证据） → 解析或映射为标准数据树（RFC 7951 JSON，校验；不完整的标为 partial） → 从标准树抽取查询表 → 路径与影响分析读查询表。查询表的字段沿用标准节点的键和名称。
- **厂商差异写成数据，不写进采集代码。** 设备族、命令、模板、映射都是数据；拓扑采集代码里不能出现厂商或族名（有守护测试检查）。
- **解析归规则治理。** CLI 解析靠规则治理的检查项和 TextFSM 模板（模板在 `src/skills/shared/textfsm-templates`，按 sha256 钉住）；拓扑里不写手工解析器。NETCONF 走转换层（负责人 2026-10-06）：设备回包 → 按标准叶子命名的记录（netconf-api `backend/standard.py`，厂商部分是 `backend/mappings/<厂商>.yaml` 数据）→ 拓扑按 IETF/IEEE 模型建树（`model/standard_trees.py`）→ 查询表 → 路径。标准模型放不下时先查有没有更新的 RFC/草案。原有按 CLI 写法产出值行的定义（[docs/netconf-api-handoff.md](../netconf-api-handoff.md)）仍服务 ACL、PBR 等尚未迁移的表。
- **App 边界。** SSH/NETCONF 是平台能力（`src/platform/device_access`），App 只调用。App 是可插拔的包，彼此依赖要在 `app.yaml` 里声明。
- **前端风格。** 所有 App 页面沿用防火墙策略 App 的外壳和视觉规范，参照 `src/apps/firewall_policy/frontend/pages/FirewallPolicyPage.tsx` 和 `.css`：
  - 整页 `#e9edf3` 背景；顶部工具栏是"← 工作台 / App 名"的面包屑，操作放在右侧。
  - 内容放在圆角白色窗口里：左侧边栏依次是产品图标、产品名、"NETOPS WORKSPACE"和编号的工作流导航；右侧主区依次是小标题、31px 的 h1、灰色说明和操作按钮。
  - antd `ConfigProvider` 的令牌：`colorPrimary #1671e8`、`borderRadius 9`、`controlHeight 40`。
  - CSS 类名加 App 前缀。
- **不要修改** `src/core/patrol`、`src/core/ingestion` 和 Netmiko 的实现。
- **只说事实。** 没有证据的映射不猜，遇到未知值就报告；测试没通过就照实写出失败输出。
- **文档和报告用中文。**

## 4. 测试

- Python：`pip install -r requirements.txt`，然后在仓库根目录运行 `python -m pytest tests/unit/<文件> -q`。
  - 模型校验相关的测试需要 `yangson==1.6.3`、`pyang==2.7.1`，缺了会报错；需要时自行安装。
  - 已知原本就失败的测试：核心检查项相关的约 50 个（`test_check_item_*` 等），以及缺 yangson 时的 `test_topology_device_ip`、`test_real_campus_ospf_ids_costs_and_topology_are_native`。判断是否回归时，和改动前的结果对比。
- 前端：`cd web/react_frontend && npm ci`，然后：
  - 跑测试：`npx vitest run <路径>`；
  - 代码检查：`npm run lint`；
  - 构建：`npm run build`。

## 5. 结束时的报告

用中文写，包括：
- 分支名和提交列表；
- 改了什么、为什么；
- 实际运行的测试命令和结果（失败要写出来）；
- 没完成的部分；
- 留给本地会话的事项：在线验证、部署、实验室操作。
