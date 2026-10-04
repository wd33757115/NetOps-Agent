# 网络数字孪生开发交接

本文最前面记录 2026-10-04 的 M5 完成情况与解析归属调整；其后保留 2026-10-03 交叉连线修复、模型修复、Claude → GPT 与 2026-09-30 的历史交接。历史工作约束以负责人后续明确指令为准，模型能力与验收范围以最新记录为准。

## 当前交接 2026-10-04 M5（VPN/EVPN/VXLAN/DC fabric）进度与解析归属调整

**状态（2026-10-04 傍晚，Claude）：M5 的 VPN/EVPN/VXLAN/DC fabric 部分已完成并部署（topology 0.7.0、规则治理 1.5.0），实机只读验收 17 项中 15 项通过，未通过的两项原因在实验网本身，见下文"M5 完成情况"。同日完成解析归属调整：拓扑改为规则治理的消费方。全部为本地提交，没有推送。**

### M5 设计（已批准）
| 层次 | 标准 | 说明 |
|---|---|---|
| 设备层 | RFC 8529 NI；BGP draft-21 afi-safi；MPLS RFC 8960；LDP RFC 9070 | 设备上的 RD/RT/EVI/VNI 只有过期草案，只作证据 |
| 叠加拓扑 | RFC 8944 VXLAN 终结点 + SIMAP 支撑关系；RFC 8542 DC fabric | 由证据推导，证明不了的不填 |
| 网络服务 | L3NM RFC 9182；L2NM RFC 9291；vpn-common RFC 9181 | 按 RT 导入导出关系（EVPN 另看 EVI/VNI）分组；`vpn-id` 等网络层标识由平台分配 |
| 客户层 | L3SM/L2SM | 不从设备生成 |

### 实验网 netops-vpn-fabric（`scripts/topology/vpn_fabric/`，README 中有拓扑与地址表）
- **DC fabric**：SPINE1/2（IOL L3，OSPF 底层，EVPN 路由反射器）；LEAF1/2（IOL L2）；LEAF3（H3C VSR）。
  - RED、BLUE 两个租户都用 192.168.10.0/24。
  - BLUE 用 L3VNI 50020 做对称 IRB，到 LEAF3 的 192.168.20.0/24。
- **MPLS L3VPN**：PE1（IOL）— P（IOL）— PE2（VSR）。
  - CUST-A 和 CUST-B 都用 10.1.1.0/24。
  - SHARED 和 CUST-A 按 RT 互相泄漏。
- **管理地址** 10.10.1.21–28，每台设备的账号由负责人运行 `lab.py account` 创建。
- **已验证的转发**：
  - 跨厂商 L2VNI、L3VNI 对称 IRB、MPLS VPN 跨站点、RT 泄漏均可达；
  - 租户之间和 VPN 之间的隔离均成立。
- **GNS3 界面一次只能打开一个工程**：打开本实验网时 campus-wan 会断电。
- **VSR 首次启动**：要等出现 "Line con0 is available" 后再运行 `lab.py type`。工具默认会给 aux 口加上 `authentication-mode none`。

### 已完成（检查点提交）
- **采样与夹具**：
  - 采样器新增 VPN/EVPN/VXLAN/LDP 命令和 `--vrf` 参数；
  - 实验工具读取实验口令统一走 `collect_lab_samples.lab_password()`：先环境变量，再 `.env`，最后才提示输入；
  - 批次 `20261003-225812` 导入 `tests/fixtures/topology/vpn-fabric`：203 份观测，密文扫描命中 0。
- **修正旧解析器**：IOS 的 Tunnel/nve 接口和 H3C 的 Tunnel/Vsi 接口别名；H3C ARP 的 VSI 列及折行；LLDP all802 管理地址；H3C 点到点 OSPF 邻居和接口行；H3C VPN 实例列表；未配置时为空的 BGP 地址族。
- **新增解析器与采集接入**（`tiers/generic_vpn.py`、`tiers/generic_evpn.py`）：
  - 新增 9 类：`vrf_detail`、`ldp_peers`、`mpls_lfib`、`evpn_instances`、`evpn_peers`、`vxlan_vnis`、`tunnel_interfaces`、`evpn_mac`；`bgp_vpnv4`/`bgp_evpn` 复用 BGP 邻居解析器。
  - 已接入 IOS 和 H3C 档案以及拓扑采集模式。
  - **按负责人新决定，这些手写解析器要迁移为规则治理的检查项加 TextFSM。**
- **按 VRF 采集改为按厂商区分**：
  - 采集侧统一为一个 `scoped_context(capability, scopes, family)`；
  - H3C 的 VPN 实例路由表通过"直连路由出接口不得属于其他实例"做一致性校验；
  - H3C 的实例详情只按实例采集。
- **叠加层解读** `model/overlay.py` 的 `prepare()`：
  - H3C ARP 按接口或 VSI 网关的绑定归入实例；
  - 出接口属于另一实例的下一跳视为本地泄漏；
  - 经 L3VNI 接口的下一跳是 VXLAN 隧道；
  - VRF 里的 BGP 路由在有 VPNv4 会话时是 MPLS 隧道；
  - `egress_instances()` 按 RT 和"是否本实例始发"选出对端实例。
- **路径引擎** `model/path.py` 增加隧道语义：
  - 入隧道后按端点在底层转发，MPLS 每跳检查标签，VXLAN 检查隧道状态，到端点后在接收实例里重新路由；
  - trace、API、助手工具增加可选参数 `src_vrf`/`dst_vrf`。
- **测试**：`tests/unit/test_topology_vpn_fabric.py` 6 个通过；拓扑全部测试 1281 个通过。

### M5 完成情况（详见 `models/network-twin/m5/README.md`）
| 项 | 结果 | 提交 |
|---|---|---|
| 1. 拓扑构建读取 `prepare()` 后的事实 | 完成；修正 `_Topology.__init__` 中 ARP 循环仍读原始事实的问题（VSI 学到的 ARP 行在 prepare 后才有 ifname） | `cee08e3`、`a73ae18` |
| 2. vpn-fabric 路径用例（MPLS、RT 泄漏、隔离、L3VNI、同网段、隧道 down、缺标签） | 完成；路径终点增加 `other_instance` | `cee08e3` |
| 3. 标准投影 | L3NM/L2NM（`model/services.py`，`GET /snapshots/{id}/services`）；RFC 8944 VXLAN 叠加网络和 RFC 8542 fabric（`model/fabric.py`，进 SIMAP 树）；BGP 地址族、LDP（RFC 9070）、MPLS 接口（RFC 8960）。全部通过 libyang 校验，M1 正反例 48→72 | `a73ae18`、`b170ad4` |
| 4. 文档、部署、实机验收 | 规则治理 1.5.0（`bootstrap_version` 3）和 topology 0.7.0 已部署；实机 15/17 通过 | 本节所在提交 |

- **实机验收中两项未通过**：VNI 50020 的 VXLAN 链路，以及 H-B2→H-B3 的 L3VNI 路径。
  - 原因：实验网重启后，LEAF2 收到了 LEAF3 的 type-5 路由（属性齐全）却不导入 VRF BLUE；LEAF3 也不导入 LEAF2 的路由。
  - 已尝试：BGP 软刷新、硬复位，以及原样重下 stitching RT，都没有恢复。
  - 孪生的输出（no_route、无链路）与设备显示一致。
  - 接手后：先恢复实验网（例如重启 LEAF2/LEAF3 后核对 `show ip route vrf BLUE`），再重跑 `.runtime/m5-live.py submit / poll / verify`。
- **发现的模型缺陷**：RFC 9291 的 evpn-bgp 分支里，`pbb` 的 when 条件用了不带前缀的 `'pbb-evpn'`，导致该分支的任何实例都无法通过校验，IOS 的服务接口类型因此不导出（M1 反例 `l2nm-evpn-signaling-option`）。
- **yangson**：校验不了 L3NM/L2NM（忽略 when 条件下的 mandatory 叶子、编译不了 L3NM 的 leafref），这两个模型只用 libyang 校验；叠加网络在 App 冻结模块集下用 yangson 校验通过。
- **实验网主机仍在持续 ping**（保持 ARP/MAC/EVPN 表）。需要停止时运行 `venv/Scripts/python.exe scripts/topology/vpn_fabric/lab.py traffic --stop`。

### M5 后续（未开始）
- L3SM/L2SM 与 SAP/AC 绑定：需要服务订单或部署事实，不从 VRF 猜。
- ACL、NAT、QoS、IPsec 与流条件分析；服务保障（RFC 9418）、告警与 OAM。
- 设备层的遗留项：
  - RIB（含 MPLS RIB）仍不导出，原因是 RIB 名称不可得；
  - BGP 前缀数不导出，原因是 CLI 没有说明计数在入口策略之前还是之后；
  - Comware 的 LDP 发现源，以及 NX-OS 的 M5 检查项实现，都没有样本。

### 方向调整（负责人决定，2026-10-04）
1. **拓扑改为规则治理的消费方。** 规则治理生产 TextFSM、检查项、变化规则和事件，巡检与拓扑都只消费。规则治理要增加一组拓扑所需、不可删除的"系统基础检查项"，并定义它们的同名 Value（`value_contract: same-name/v1`）。
2. **按型号优先、厂商兜底查找模板。** 规则治理现有的 `CheckItemImplementationResolver` 已经按具体程度打分：型号 40 > 系统家族 30 > 平台 25 > 厂商 20。
   - **"万能 TextFSM"已评估，结论是不作为主方案。** 单行表（如 ARP）可以跨厂商合成一个模板，两家共 8 份样本通过。路由表合不了：TextFSM 的 Value 选项（Required/Filldown）对整个模板生效，IOS 与 H3C 的要求相反。
   - **采用的方案**：一个检查项一组同名 Value，每个系统家族一份默认模板，型号有差异才加例外模板；Value 名尽量沿用 ntc-templates 的命名。
3. **先让拓扑接入消费接口，巡检的迁移另开一张卡。**

**现状问题（10-04 核查）**：
- `src/core/parser` 全部是 4 行转发，实现都在 `src/apps/rule_governance/backend/parser`。
- `src/core/check_items` 有 29 个转发：15 个转到规则治理，14 个转到巡检；另有 26 个真实实现仍留在核心（含 `value_contract`、`command_adapter`、`canonical`、`execution` 等）。
- 巡检和拓扑都经这些转发调用规则治理的代码，巡检还直接 import 了 `rule_governance.backend.rollout_tasks`。这属于跨 App 的隐性依赖，消费接口要解决它。

### 解析归属调整的进度（2026-10-04，Claude）
清单和接口方案见 `docs/topology-rule-consumption.md`（负责人已认可，"直接做"）。已完成：
- **规则治理侧**：
  - 系统基础检查项资产 `check_items/assets/system_check_items.yaml` 和 `system_items.py`，随目录引导写入数据库；
  - 删除检查项或其默认实现返回 409，新版本不能去掉必填 Value；
  - 消费契约 `runtime.py` 新增只读的 `CheckItemSelectionReader`：`select`（型号优先）、`parse`（坏行记下继续读，声明式的必需行与条目数校验）、`commands`（供命令授权）；
  - 模板按 sha256 钉住，改动未经评审即拒绝。
- **已全部迁移（29 项）**：IOS/IOS-XE 与 Comware 7 的全部拓扑事实都经规则治理采集和解析，清单见设计文档第 2 节。
  - 每项迁移前用全部实验网夹具（OSPF 等还加上原测试的全部变异输入）比对新旧事实，一致才删除对应的手写解析器；唯一有意的差异是 LLDP 系统描述现在完整读出。
  - 采集档案里这些能力只写 `parser: rules`，命令和按 VRF 的命令形式取自已发布的实现，并仍须通过拓扑的 L1 只读校验。
  - 拓扑只经 `src.apps.rule_governance.runtime` 使用规则治理；`src.core.parser.*` 引用和固定 NTC 模板层已移除。NX-OS 等家族仍走通用解析层，等有实机样本再补实现。
- **测试**：`tests/unit/test_topology_rules.py` 等；拓扑全部测试通过；其余单元测试的失败与上一提交时相同，非本次引入。
- **未做**：系统模板登记到规则治理的模板库（数据库）以便页面评审；目前模板文件由资产清单按 sha256 钉住。巡检迁移在独立会话进行。

### 接手后
先读 `docs/network-digital-twin-implementation.md` §8 M5、`docs/topology-rule-consumption.md`、`models/network-twin/m5/README.md` 和本节。
1. 恢复实验网的 L3VNI 后，重跑 M5 实机验收中未通过的两项。
2. 与负责人确认 M5 后续的范围和顺序（服务绑定、策略、保障），再动手。

带 yangson 的测试要这样运行：`PYTHONPATH=.runtime/p0-2/yang-deps venv/Scripts/python.exe -m pytest ...`。

## 历史交接 2026-10-03 交叉连线修复与剩余验收

**状态（2026-10-03 13:40，Claude）：本节剩余验收已完成，并修复了验收中发现的悬停闪烁回归（topology 0.4.17），见本节末“完成记录”。下一项按完整实施方案 M1。**

负责人要求因 token 将用尽立即写交接。本节是接手入口，**先完成本节剩余验收，再进入完整实施方案 M1**。本轮代码已经冻结，不再扩展功能。主项目 `C:/Users/wangd/PycharmProjects/PythonProject/netops-agent`，分支 `feat/network-twin`；隔离树 `C:/Users/wangd/.codex/worktrees/twin-vlan-trunk/netops-agent`。此前本地提交：`3f09edc`（模型/作用域修复）、`6eef512`（IETF 研究与完整方案），均未推送。本节写入时本轮画布修复尚未提交；后续提交以 Git 日志为准。

### 用户反馈与确切根因

ACC1 的实际 LLDP 为 `Ethernet0/1 → DIST1 Ethernet2/0`、`Ethernet0/2 → DIST2 Ethernet2/1`，用户看到画布像只有 DIST1，其他交叉线也类似。已经独立核对原观察、RFC 7951 L2 树、canvas API 与用户 Edge 当前 DOM：两条都存在，链路尾 ID 是 `ae508bbdfc62`、`4e339d459824`，两端 LLDP 证据完整。当前快照 **`edc1514b188643f0aec13f7913fe940a`**，网络 `d537b0de1fab427683413b81290db718`，15 节点、24 条画布关系。

问题是**画布几何错误及渲染验收遗漏**：BFS 每六设备一排、同侧单连接点、默认曲线让长边穿过中间节点，让平行边完全重合。浏览器实测两组完全重合：BR1/BR2 的 GE1/0 关系与 GE5/0 关系；DIST1/DIST2 的 E1/0 与 E1/1 链路。按实际 DOM 节点尺寸和 SVG 曲线采样，另外发现 6 条边穿过第三方设备节点。上轮 16 对端口验收检查的是 L2 模型，不能据此证明可见画布正确。

### 已完成并冻结的修改

- 通用连通分量布局，不硬编码设备名、角色或 GNS3 坐标；每条边独立两端句柄，并行分道、长边绕节点。
- 固定节点几何尺寸并更新动态句柄；图结构不变的轮询不会重新缩放。
- 悬停/选择一条边突出该边和两端；选中节点突出其全部邻接边。侧栏顶部逐条展示本地端口、对端设备/端口，可点击打开原链路详情。长标签只在关注单边时显示。
- 无法生成路线时明确提示未绘制关系数量及查看入口，不能静默漏线。
- 后端生产模型、采集、API、标准模块和依赖均未改动；升级仅为 **topology 0.4.16**。

本轮 8 个文件已从隔离树按字节/哈希同步主项目：`src/apps/topology/app.yaml`，`src/apps/topology/frontend/{Page.tsx,layout.ts,layout.test.ts,topology.css,Canvas.test.tsx,fixtures/campus-layout.json}`，`tests/unit/test_topology_canvas_preservation.py`。精确文件清单/同步前后哈希在 `.runtime/topology-crosslink-sync-manifest.json`；原文件备份 `.runtime/topology-crosslink-before-source/`。**不要直接重复运行同步脚本，它会拒绝覆盖已有备份。**

### 已通过与尚未通过的验收

- 前端四个套件 **29 passed**，相关 ESLint 零警告，生产构建通过。后端新增 canvas 完整投影回归与园区回放合计 **33 passed**，Ruff/格式通过。没有因为只改画布重复宣称本轮执行全部 1224 项后端测试。
- 真实 15 节点/24 边 fixture 的算法检查：24 条路线、48 个独立句柄、无穿节点、无共线重叠，逆序输入稳定。独立复核修复了第六条及以后并行边可能漏画的参数问题，复核至 12 条并行边、6 条自环；多点 segment spokes 也保留。
- **尚未完成修复后的真实浏览器验收**：算法测试不是浏览器渲染证明。不能将下面“待完成”项目写成已完成，也不要再次仅拿边数或模型校验作为画布正确的依据。

### 部署与运行状态

已确认没有活动发现后，同步文件并执行 `scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps topology -NoBrowser`。日志为 `.runtime/topology-crosslink-deploy.log`。写入本节时部署正在收尾，最终结果见本节末尾运行状态补记；不要因 source version 为 0.4.16 就推断 App 已启用。

运行目录里的只读 helper：`venv/Scripts/python.exe -X utf8 .runtime/topology-fix-live.py preflight` 检查接口、当前快照和活动任务。**不要执行旧 helper 的 submit**：本轮需要同一快照前后对照，无需重采样，更不能重新 shutdown。App 摘要门禁仍有效，部署后改 `src/apps/topology/` 会导致 503；发现问题时回隔离树修复、测试后重新部署。

### 接手后立即完成

1. 核对 `.runtime/topology-crosslink-deploy.log` 最终结果及 0.4.16 状态。若需单独验证，先 PowerShell `. ./scripts/windows/_port_utils.ps1`、`Initialize-WindowsRuntimeEnvironment -ProjectRoot (Get-Location).Path`，再运行 `venv/Scripts/python.exe web/django_backend/manage.py app_packages verify topology --gateway-url http://127.0.0.1:8000 --frontend-url http://localhost:3000 --wait-seconds 60`。不能遗漏运行环境初始化，默认 Redis 环境不同会让命令等待。
2. 用 `cua_repl` 获取 Edge 的 `http://localhost:3000/apps/topology` 标签页并刷新。交接前 browser id 为 `3`、tab id 为 `1399402181`，但应先重新检查当前清单。浏览器已经登录，用户密码无需重问。保持当前快照，确认 24 条边都在。
3. 浏览器验收 ACC1 两上联：点击 ACC1，应同时突出到 DIST1、DIST2 的线；侧栏有 `Ethernet0/1 → DIST1 Ethernet2/0` 和 `Ethernet0/2 → DIST2 Ethernet2/1`。逐条点击打开对应详情与两端 LLDP 证据。再检查 DIST1/DIST2 两条并行边可分别选中。
4. 保存新 SVG 路径和实际 DOM 尺寸，用 `.runtime/topology-crosslink-geometry.py after` 复测 0 完全重合、0 穿第三方节点。该脚本读取 `.runtime/topology-crosslink-after-{routes,geometry}.json`；数据格式看同目录 `before-*`。浏览器 `path.getTotalLength()` 在本工具 DOM 接口不可用，所以已用保存的 M/L/C/Q 路径做离线采样，不能据此报浏览器 API 异常。新路线使用 M/L。
5. 保存修复后截图 `.runtime/topology-crosslink-after.jpg`；更新此交接与路线图 §14 的真实结果，再按本任务精确路径本地提交。**不要推送。**

### 证据与工作区保护

当前模型和画布证据 `.runtime/topology-crosslink-{proof,graph}.json`。修复前可见证据 `.runtime/topology-crosslink-before.{jpg}`、`topology-crosslink-before-{routes,geometry,collisions}.json`。`proof.json` 保存当前画布规范化 JSON 哈希 `6ef7a48abca8b4a1438498b7ae965a595da5f345dbf624e5b34adb2d8665c57c`，可用于证明同快照数据未变。旧 helper `.runtime/topology-crosslink-live.py` 会覆盖 proof.json，因此读取/复核时先保留已有 before 字段或另存结果，不要无意识覆盖证据。

主工作区另有与本任务无关的暂存改动：`web/react_frontend/src/pages/{ChatPage.test.tsx,ChatPage.tsx,SkillsPage.tsx,StatusPage.tsx,StoragePage.tsx}`；未跟踪 `src/workflows/custom/`；部署会生成 `web/react_frontend/src/apps/generated.{ts,meta.json}`。全部保留，不能 `git add .` 或重置。提交本任务使用精确文件清单以及 `git commit --only`，防止带走别人的暂存改动。隔离树还有上一轮同步过的旧脏文件，不要按该树全部 diff 再覆盖主目录。

本轮未修改实验设备配置、未重新发现、未再次注入故障，也未推送。IETF 优先、不自建 YANG、设备配置与推送需明确授权等约束继续遵守。画布修复收尾后按 [完整实施方案](network-digital-twin-implementation.md) M1 开始，勿把本次 UI 修复当作完整物理拓扑或全转发模型已完成。

### 完成记录（Claude 接手，2026-10-03）

- **部署核对**：`.runtime/topology-crosslink-deploy.log` 中 0.4.16 已启用；13:22 平台另有一次重启（非本线发起），等待前端/API 恢复后才验收。当前快照仍为 `edc1514b188643f0aec13f7913fe940a`，未重新发现、未改设备配置。
- **浏览器验收方式**：内置浏览器拒绝访问 localhost，改用本机 venv 的 Playwright 1.61 + Chromium 真实渲染同一页面；Chromium 把 `localhost` 解析到 IPv6 而前端只监听 IPv4，脚本用 `http://127.0.0.1:3000`。脚本 `.runtime/topology-crosslink-browser.py`，结果 `.runtime/topology-crosslink-after-browser.json`。
- **结果（14/14 通过）**：15 节点、24 条边与快照 graph 的 ID 一一对应，无路径重合、无“未绘制”提示；点击 ACC1 时两条上联同时高亮，侧栏列出 `Ethernet0/1 → DIST1.lab.local Ethernet2/0`、`Ethernet0/2 → DIST2.lab.local Ethernet2/1`，逐条打开均有 LLDP 证据；DIST1–DIST2 两条并行边用真实鼠标点中各自中点时只高亮自身。导出的路径与 DOM 尺寸存为 `.runtime/topology-crosslink-after-{routes,geometry}.json`，`topology-crosslink-geometry.py after` 复测 **0 完全重合、0 穿第三方节点**（修复前 2 组、6 条）。截图 `.runtime/topology-crosslink-after.jpg`、`-after-acc1.jpg`。
- **验收发现的回归并已修复（0.4.17）**：鼠标停在画布的节点或边上时，全部连线周期性消失再出现（每 250 ms 采样在 0 与 24 之间跳）。根因见 `@xyflow/react` 12.10.2 的 `adoptUserNodes`/`parseHandles`：悬停每次重建节点对象，新对象既无 `measured` 也无 `handles` 时连接点边界被清空，所有边在重新测量前不渲染；边消失又改变指针下元素、再触发悬停，形成循环。修复为在 `Page.tsx` 的节点上给出布局已知的固定 `width/height/measured` 与静态 `handles`（连线路径仍由布局给出，连接点只决定能否渲染）。新增 `Canvas.test.tsx` 用例；拓扑前端 4 个套件 **30 通过**，ESLint（`-c web/react_frontend/.eslintrc.cjs`）零警告；后端 canvas/园区 **33 通过**。部署 `start.ps1 -SkipLangfuse -UpgradeApps topology -NoBrowser` 退出 0、topology **0.4.17** 启用，日志 `.runtime/topology-hoverfix-deploy.log`；修复后悬停节点与点中边后连续 3 秒均为 24 条。
- 早先“每条平行边可分别选中”在旧版能通过，是因为闪烁把悬停状态冲掉；设计上悬停优先于选中，验收须让指针停在被选的边上。

## 最新修复交接：2026-10-03，topology 0.4.15

负责人要求先研究 IETF 模型、审查当前建模，再修复代码并给出完整实现方案。研究见 [IETF 模型分析](network-digital-twin-ietf-model-landscape.md)与[逐项目录](network-digital-twin-ietf-model-catalog.md)；下一阶段以[完整实现方案](network-digital-twin-implementation.md)的 M1—M6 为准。

**审查发现的是实际实现错误，YANG 校验通过不能替代事实正确。** 本轮已修复多设备/NI 同 IP 的后写覆盖、同 MAC 跨 VLAN 错归属、路径入口固定 default、相同本地 VRF 名被当全网域、缺邻接仍成功、ARP/MAC 被确定标为主机、IOS 非 default ARP 漏采，以及 incomplete/conflict LLDP 参与强身份的问题。新投影版本为 `rfc8345+simap-yang-03:l2-l3:scoped-v2`；历史快照不改写，分析响应标明快照与分析两个版本。

在隔离工作树完成回归后同步 30 个任务文件并升级部署。最终后端 **1224 passed、2 skipped**（可选模板源），前端 **20 passed**；冻结 YANG、相关 Ruff/格式检查、ESLint 与生产构建通过。一次新发现 `43618be6e2a447638ece2f20d3d351cc` 的 8 台设备全部成功，新快照 `edc1514b188643f0aec13f7913fe940a` 通过拓扑 YANG 完整校验；六台 IOS 的 default/MGMT ARP 均完成，MGMT 对应六个设备本地 NI 节点。16 对受管 LLDP 端口与 GNS3 实际接线集合完全相同，7 个未识别端点不再冒称主机。BR1→CORE1 loopback 候选路径双向 complete 仅表示当前路径契约所需证据齐备，不是全策略转发验证。

**更正历史表述：下文“平台模型与实际接线对应”仅有 16 条受管设备间接线的局部验收依据，不成立为完整物理网络等价结论。** GNS3 文件有 18 节点、34 接线；未受管 ISP、无管理交换机、管理设施、被动资源及其全部接线尚未由发现完整还原。设备原生树仍为 partial；完整桥/FDB、STP、IPv6 ND、各 NI 路由及策略转发仍按方案继续实施。本轮动态实例采集只扩展 IPv4 ARP，NX-OS 尚无实机验收。

详细实现、检查与部署记录见路线图 §14 最新条目。修复代码本地提交 **`3f09edc`**；采集完成后全 App 部署复核 **9/9 通过**，状态/设备接口再次返回 200。原始本机证据为 `.runtime/topology-fix-{verification,live-proof}.json`、`.runtime/topology-fix-rfc7951.json`、`.runtime/topology-fix-observations.json` 与 `.runtime/topology-fix-path.json`。本轮没有修改设备配置，也没有重新执行 shutdown；只读发现复用平台已保存登录，未记录或输出设备口令。未推送。

## 历史交接：2026-10-03，Claude → GPT

项目根目录 `C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，分支 `feat/network-twin`；`origin`（`https://github.com/wd33757115/net-ops.git`）已推送到 `6855949`，本交接文档的提交在其后，是否推送以 `git status -sb` 为准。Windows + PowerShell；Git Bash 也可用（本轮偶发 Bash 工具退出码 107，换 PowerShell 即可）。

**一句话现状：拓扑建模已在一套 8 台纳管设备的园区 + 双出口实验网（H3C VSR + Cisco IOL 混合，ISP 不纳管）上端到端跑通，平台模型与实际接线对应，并按 IETF 标准补了 VRF、VRRP、递归下一跳、聚合与节点管理属性；导出模型通过冻结 YANG 模块校验。负责人倾向继续完善建模，参考 IETF 与成熟产品；下一步建议见“接下来的计划”。**

### GPT 接手更新：2026-10-03

负责人已授权按计划开始，并确认 ACC1/ACC2 两条新 SSH 指纹；使用平台已保存的默认登录完成只读采样，未记录口令、未修改设备配置。第一项 VLAN/trunk 已部署为 **topology 0.4.13**：真实发现 8 台全成功，快照 `894ad28d1fdc415cb942fafa9247786c`，11 个 trunk TP 导出 `outer-tag="10,20"`，access/PVID 留在设备事实中，导出通过冻结 YANG 完整校验。16 条设备间接线按 LLDP 验收；额外管理网 ARP 逻辑邻接不当物理网线。最终相关回归 636 通过、2 个可选模板源用例跳过；详情、限制与证据路径见路线图 §14 新台账。

本轮后续已部署 **topology 0.4.14**，按计划接入状态、OSPF 和独立设备树：冻结 SIMAP 无通用状态 leaf，沿用 Q-IETF-1 应用层快照身份访问 RFC 8343/8344/8347 部分接口树及独立路由/LLDP 树，不补自定义 YANG 外键。只消费本次最新 attempt；失败且没有新证据时不借旧 UP。链路历史仅保留同网络、身份明确的 L2 两设备关系，原始来源与最后确认时间不随新快照刷新。路径遇不可用端口终止，缺状态保留候选并标 partial。最终后端 **1149 通过、2 个可选模板源跳过**，前端 **18 通过**；真实 8 台再次全成功，快照 `660c20b7ae0b42f6b3e48f1b0a3c5b57`，16 条 LLDP 接线、11 个 trunk 与冻结拓扑校验通过，6 个默认 VRF RID 不传播到 MGMT。明细、度量与原生部分实例的缺项见路线图 §14 最新台账；追加授权的临时关闭接口实测已完成，结果与限制见下段。代码本地提交 `28defe6`，未推送。

**BR1 GE3/0 临时故障实测**：负责人明确授权后，仅对该接口发一次 `shutdown`；北京时间 11:05:32.879 至确认恢复 UP 的 11:08:24.630，窗口 **171.749814 秒，小于 180 秒**，首次 `undo shutdown` 即恢复，未保存配置。基线、故障、恢复三轮均 8/8 采样成功，24 条新 Observation 未复用；故障时同 ID 链路 `2e281abc1e04` 保持 Down、`available=false`。故障轮全部 8 条观察位于北京时间 **11:05:51.615423～11:08:13.713896**，均早于 undo 开始 **11:08:14.337315**，其中 BR1 Down/AdminDown 的当前证据时间为 11:07:53。故障正向路径为 **BR1 GE5/0 → BR2（入 GE5/0、出 GE4/0）→ CORE1 Ethernet0/2**，反向沿同一路径，均 delivered；恢复后双向回切 BR1 GE3/0↔CORE1 Ethernet0/1 直连。三个 RFC 7951 拓扑通过冻结 Yangson 完整校验，HTTP 再读三份 snapshot.state 均与原记录相同，独立审查 `accepted=true`、`errors=[]`；恢复验收为 8 设备、11 trunk、16 接线，原生设备树仍为 partial。

本轮链路关系来自 CORE1 当前 ARP，`current_lldp_proven=false`、`historical=false`，没有 `last_seen`，因此**未实测触发历史保留/时间保持或 `interface_unavailable` 终止分支**，这些分支仍以纯回归为证。165 秒故障轮询超时后 finally 已恢复，只用 GET 取回已有故障任务，没有重演 shutdown 或重发故障发现。快照 ID 与证据 `.runtime/state-shutdown-{proof,audit-proof,http-proof}.json` 见路线图 §14 正式记录。

**标准核查修正**：下文 Claude 原计划把 RFC 8346 `metric1` 标成 IGP cost，这是该标准没有规定的具体语义；也不能把 `metric1/metric2` 当两个方向。真实 BR=1/Cisco=10 分别保留在设备事实，仅当全部参与 TP 均唯一绑定当前 OSPF 接口、明确 scope 且成本相同，按本项目声明的投影政策填共同 `metric1`，其余省略并给 gap；PID 是设备本地键，不要求两端相同。L3 TP 的 `ip-address`、`unnumbered-id`、`interface-name` 在同一 choice，现有 IP TP 不加互斥接口名，接口关联使用既有 IP 事实。[RFC 8346](https://www.rfc-editor.org/rfc/rfc8346.html)

设备树为部分实例：统计中断时间、VRRP version、原生 RIB name、LLDP agent/time-mark/remote-index 等缺项如实给 gap；不造默认值、表项键、计数器或时间，未宣称完整设备 datastore 已通过校验。完整冻结模块与独立 library 位于 `src/apps/topology/resources/yang/`，校验控制与反例在新增 native 投影测试中。下文 Claude 原始统计及计划保留为接手时的历史基线。

### 网页 GPT 报告核实：2026-10-03

对照负责人提供的 `Downloads/deep-research-report (1).md`、本地源码与 RFC 原文后，报告可用作领域清单，**不能作为本仓库的静态审计结论**：它的背景明确承认没有仓库链接，大部分判断使用假设，但执行摘要和表格却写成确定的实现现状。

| 报告结论 | 核实结果 |
| --- | --- |
| 单层拓扑、没有支持网络映射、TP/端口未实现 | 与接手时的代码不符。`model/builder.py` 已构建 SIMAP L2 和按 VRF 分开的 L3，包含 TP 与 supporting-network/node/TP/link；有冻结模块校验和真实园区回放。 |
| 尚未引入标准 YANG、标准导出和测试 | 与代码不符。接手前已支持 RFC 7951 标准树导出及 Yangson 冻结模块验证；本轮补充独立部分设备树。 |
| 缺少完整设备树、服务模型、策略、清单与协议层 | 方向基本成立，需逐项用采集事实与实例验证衡量，不能因某个 RFC 存在就把所有模型列为当前必做项。 |
| 没有 NETCONF/RESTCONF/gNMI 即拓扑模型不合规 | 混淆数据模型与协议能力。RFC 8345 定义拓扑结构；协议服务和采集渠道需另外设计，当前 JSON 导出不冒称 NETCONF/RESTCONF 服务。[RFC 8345](https://www.rfc-editor.org/rfc/rfc8345.html) |
| NMDA 就是配置表和状态表分开；RFC 6241/8040 直接公开 operational | 表述不准确。NMDA 是概念 datastore 与实例语义，并未要求所有协议公开所有 datastore；访问 NMDA 的 NETCONF/RESTCONF 扩展分别由 RFC 8526/8527 定义。[RFC 8342](https://www.rfc-editor.org/rfc/rfc8342.html)、[RFC 8526](https://www.rfc-editor.org/rfc/rfc8526.html)、[RFC 8527](https://datatracker.ietf.org/doc/html/rfc8527) |

本轮执行顺序继续以本地路线图和有证据的标准映射为准，报告中的工期与优先级没有仓库依据，未直接采纳。

### 负责人本轮新定的要求（与第 2 节同等优先）

- **“建模时优先采纳 IETF 相关标准”“我们的第一任务就是参考 IETF 完善拓扑建模”**。遇到建模选择先查 IETF/IEEE 原文（RFC、SIMAP 草案、YANG），能从标准得出答案的不要再请负责人二选一；本轮负责人就因为被问 VRF 表示法而要求“再搜搜，IETF 一定有相关标准”。只用标准已有字段，仍然**不加项目自定义 YANG 字段**。
- 可以参考成熟产品（Batfish、IP Fabric、SuzieQ、NetBox 等）的做法，但落到模型上以 IETF 定义为准。
- **改实验设备配置要先征得负责人同意**（本轮在 BR1/BR2 加 VRRP 就是先问再做）。只读采样已获授权。
- **在设备上建账号、往任何地方输入设备密码，只能由负责人自己做**：实验账号用负责人运行的 `lab.py account` 建立；平台“拓扑默认登录”的密码也由负责人在页面上改。负责人在对话里给过密码，也不能记录或代填。
- 推送 GitHub 要负责人明确说；提交可以照常做（明确路径）。

### 09-30 之后完成的内容（按时间，均已提交并推送）

| 提交 | 内容 |
|---|---|
| `c245d4e` … `79eeb72` | 终端按键直通、巡检原文基线与整段文本对比、诊断提示改为能力描述、DeepSeek 对照实验记录（详见路线图 §14 对应条目） |
| `70f9320` `7a9a652` `9337087` `b75b0ab` | 回放时间线、工具失败原因告知模型、执行视图显示推理、推理与笔记用中文 |
| `727ed5b` | 一次对比多台设备与基线；诊断提示按 ROLE/DIAGNOSIS/DOCS/EVIDENCE 重写；路径工具注明是“之前的路径” |
| `9fe5284` | 本地按原样记录每轮发给模型的请求（`runs/requests/`，`trace_timeline.py --requests`） |
| `bb440f3` | 引用校验接受本会话更早的证据并点名未知引用；全部时间转北京时间 |
| `5427b3d` | 平台启动与 App 启动解耦（排空、`--force`）；采集数据不再关键词脱敏，保持原文 |
| `a10c346` | 表格按条目对比、摘要与按需查行（`compare_rows`）；大表改用统计命令 |
| `422fc89` | 知识问答会话级模型与思考强度（只对当前会话生效，默认关闭思考） |
| `2cec677` | **园区 + 双出口实验网**与 Cisco 真机建模（见下） |
| `cd12948` | **每个 VRF 一个三层网络**（RFC 8345 §3、RFC 9375 §4.2、SIMAP REQ-CONTROL-PLANE） |
| `0bf421b` | **VRRP 按 RFC 8347**：采集、虚拟地址定位为 `virtual_router`、路径第一跳取主设备 |
| `8e62f84` | 旧格式（SIMAP 前）快照导出改为 URI 标识（另一会话完成） |
| `ed1dbcf` | **递归下一跳按 RFC 8349** 在本机 RIB 解析，证据 `resolved_via`；新缺口 `unresolved_next_hop` |
| `6855949` | **聚合按 RFC 8944** `lag`/`member-link-tp`；节点 `management-address`/`management-mac` |

各项的设计理由、实测与测试数字在路线图 §14 台账 2026-10-03 的几条里，以台账为准。

### 实验环境（新）

- **园区 + 双出口实验网 campus-wan**：工具包 `scripts/topology/campus_wan/`（`README.md` 有拓扑图和地址表）。路由参照 Batfish 示例网络，二层参照园区三层组网。BR1/BR2 为 H3C VSR（eBGP 上联、iBGP、OSPF area 0；BR1 本地优先级 200）；CORE1/2 为 Cisco IOL L3（路由反射器、/31 互联）；DIST1/2 为 IOL L2（ABR、VRRP、LACP Po1、rapid-PVST）；ACC1/2 为 IOL L2（双上联 trunk）；ACC2 后接傻瓜 VLAN 交换机（PC4、PC5）；ISP 不纳管、不发 LLDP；Cisco 管理口在 VRF MGMT。管理地址 10.10.1.11–18，主机 10.10.1.254。BR1/BR2 的 GE1/0 另有 VRRP（VRID 1，虚拟地址 10.10.1.10，负责人批准）。
- GNS3 工程 `~/GNS3/projects/netops-campus-wan/`，跑在 GNS3 VM（Hyper-V，10.10.10.2，10GB）。与旧的两台 VSR 工程（`untitled`）内存不够同时运行，旧工程已关闭，VSR-1/2 配置已保存。
- `lab.py` 子命令：`project`（生成工程，已生成过就不要再跑）、`push NODE|all`（经控制台推配置并保存，报被拒行）、`console NODE "命令" ...`（VSR 自动走串口 aux）、`type`/`screen`（VSR 的 VNC）、`traffic [--stop]`（PC 持续 ping，保持 ARP/MAC 表有内容；采样前先开）、`account`（只能负责人运行）。
- 账号：8 台纳管设备的 admin 账号已由负责人建好，密码与 `.env` 的 `NETOPS_LAB_PASSWORD` 一致；脚本从环境变量读取，不要打印、不要写入任何文件。平台的拓扑默认登录已由负责人更新为同一密码。
- 平台里园区网对应的网络 ID 是 `d537b0de1fab427683413b81290db718`，以 8 个管理地址为种子、`allowed_cidrs` 为 `10.10.1.0/24` 提交发现即可（API `POST /api/apps/topology/runs`，本机开发账号见登录页；请求体含中文时用 UTF-8 发送，Git Bash 下的 curl 会报解析错误）。
- 采样：`scripts/topology/collect_lab_samples.py <地址...>`（按 SSH 横幅区分 Comware/IOS，只发只读命令）。回归素材：`tests/fixtures/topology/campus-wan/`（批次 `20261003-010038`，`expected.json` 是实际接线、终端位置、三层网段、VRRP 主备），导入用 `scripts/topology/import_lab_fixtures.py --lab campus-wan`（秘密扫描；导入器拒绝覆盖已有且不同的素材，换批次要先删目录）。

### 部署与测试

- Claude 交接时平台版本：topology **0.4.12**、assistant **0.8.9**；GPT 本轮部署版本见文首更新。App 源码改后要升 `app.yaml` 版本并执行 `scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps topology -NoBrowser`；部署会先停用并排空，**进行中的发现任务会被取消**。
- **代码摘要准入**：部署后工作区里该 App 的源码只要再变（包括其他会话的改动），App 就返回 503“应用不可用”，直到重新部署。本轮就因另一会话在同一工作区改 `normalize.py` 而中断过一次。
- 测试：`venv/Scripts/python.exe -m pytest -q tests/unit -k "topology or twin" -p no:cacheprovider`（交接时 478 通过）。YANG 校验依赖 yangson，不在 venv 里，放在 `.runtime/p0-2/yang-deps`：加 `PYTHONPATH=.runtime/p0-2/yang-deps` 后 YANG 用例才真正运行，否则跳过。园区网回归 `tests/unit/test_topology_campus_wan.py`（25 项，含导出 YANG 校验）。

### 本轮踩过的坑（照做可避开）

- GNS3 工程文件里**不要写 `startup_config_content` / `startup_script`**：控制器会一直留着它，之后改节点设置都报 400，且每次打开工程都会用它覆盖设备已保存的配置。`lab.py` 已改为全部经控制台推送。
- IOL 第一次 `write memory` 会问“Overwrite the previous NVRAM configuration?[confirm]”，不回车确认就什么都没保存（重启后配置和 SSH 密钥都丢）；`lab.py` 的 `run()` 已处理，其他 `[confirm]` 一律答否。IOS-XE 17.15 用 `crypto key generate rsa general-keys modulus 2048`（不带 general-keys 是隐藏命令）。
- VSR 首次启动卡在 DHCP 自动配置，在 VNC 上按 Ctrl+C；串口（aux）默认只读角色且要求认证，已在两台 VSR 上设 `user-role network-admin` 与 `authentication-mode none` 并保存。VSR 不支持 STP，只能当路由器；VSR 默认不回 TTL 超时，traceroute 在它那一跳显示 `*` 是正常的。
- VPCS 的 MAC 在工程重开后会重新分配，IP 不变；跨重启对比时 MAC 变化不是故障。
- IOS 在 ARP 表、VRRP 为空时**一个字都不打印**，按 ios 族认作“已确认为空”；`show lldp neighbors detail` 不打印端口 ID 子类型；`show ip route` 的递归 BGP 路由没有出接口。
- 本机 `python` 是应用商店占位（退出码 49），一律用 `venv/Scripts/python.exe`。连接 GitHub 偶发 TLS 握手失败，重试即可。
- 工作区与其他会话共享：只按明确路径提交；`web/react_frontend/src/pages/{ChatPage,ChatPage.test,SkillsPage,StatusPage,StoragePage}.tsx` 暂存改动、`generated.ts`/`generated.meta.json` 与未跟踪的 `src/workflows/custom/` 都不是本线的，不要提交。

### 与完整网络模型的差距（2026-10-03 实测统计）

**1. 已导出的拓扑模型：骨架齐，属性约一半。** 用 YANG 模式树枚举冻结模块的全部数据节点，与园区网导出逐项对照（不含通知与 SIMAP 历史树；模型中没有任何字段落在标准之外）：

| 模块 | 已用/定义 | 未用的主要节点 |
|---|---|---|
| RFC 8345 `ietf-network` | 11/11 | — |
| RFC 8345 `ietf-network-topology` | 11/17 | 单向链路的 source/destination（按 SIMAP 用双向链路，有意不用） |
| RFC 8944 二层 | 11/26 | 链路 rate/duplex/auto-nego/delay；节点 bridge-id、management-vlan；TP port-number、unnumbered-id。**VLAN/QinQ/VXLAN 三个 feature 在 `src/apps/topology/resources/yang/library.json` 未启用**，`outer-tag` 等 VLAN 节点目前不可用 |
| RFC 8346 三层 | 8/21 | 链路 `metric1/metric2`、节点 `router-id`/`name`、前缀 metric、TP `interface-name`/`unnumbered-id` |
| SIMAP -03 | 16/36 | name/description/label；`timestamp`、`live-network-ref`（历史/推演）；`subnetwork`（分层分域）；`tp-role` |

**2. 设备级模型：有采集，未按 YANG 导出。** 生产采集的事实（接口、IP、ARP、LLDP、MAC、聚合、VRRP、路由、版本）都对应 RFC 8343/8344、IEEE 802.1AB/802.1Q/802.1AX、RFC 8347/8349 与网络清单草案，但存的是字段名对齐 RFC 的项目 JSON，不是 YANG 实例。`models/network-twin/` 研究包只对旧两台 VSR 做过离线映射，未接入 App。拓扑节点 ↔ 清单 ↔ 设备树的关联**标准本身没有定义**（研究包 `virtual-association` 已记录）。

**3. 尚未涉及的整块：** VLAN/trunk 成员（RFC 8944 VLAN feature + IEEE 802.1Q）；链路/节点状态进模型（SIMAP REQ-STATUS + RFC 8343 oper-status）；路由协议层（RFC 9129 OSPF、BGP 模型草案，SIMAP 把 IGP/EGP 列为图层）；策略（RFC 8519 ACL、RFC 8512 NAT）；SIMAP 历史与推演；服务层（RFC 9182/9291/9408）；清单（序列号、板卡）。STP 端口状态在 RFC 8944/IEEE 802.1Q 拓扑模型中无对应，只能作设备事实。

### 接下来的计划（负责人倾向继续完善建模，可参考成熟产品）

每项先查标准原文、给负责人一段简短设计（落在哪些标准节点、采哪些命令），确认后实现；先用园区网真实回显写解析与测试，再部署、重新发现、实测验收，最后写台账。成熟产品的做法只作对照，模型以 IETF 为准。

1. **VLAN 与 trunk（建议先做）**
   - 标准：RFC 8944 的 `VLAN` feature（TP 的 `outer-tag`，类型为 VID 范围）；设备侧 IEEE 802.1Q bridge（`bridge-port` 的 `pvid`、`bridge-vlan` 成员）。先读 RFC 8944 原文确认 access 口与 trunk 口在 `outer-tag` 上的语义，再决定只启用 feature 还是同时引入 802.1Q 设备事实；启用 feature 要同步 `library.json` 并跑 YANG 校验。
   - 成熟产品：Batfish `interfaceProperties` 有 `Switchport_Mode`、`Access_VLAN`、`Allowed_VLANs`、`Native_VLAN`；NetBox 接口有 802.1Q Mode（Access/Tagged/Tagged (all)/Q-in-Q）、Untagged VLAN、Tagged VLANs；IP Fabric 的路径查找在二三层边界用 VLAN+ARP+MAC 关联。
   - 素材：园区网 dist/acc 的 `vlan.txt`、`trunk.txt` 已入库；Comware 的 access/trunk 回显（`display port trunk`、`display interface brief` 桥模式表）需另采。
   - 验收：园区网 trunk 口带出 VLAN 10/20，access 口的 VLAN 正确；能对应故障集 v1 的“trunk 未放通 VLAN”“端口 VLAN 配错”。
2. **状态进模型**
   - 标准：SIMAP REQ-STATUS 要求宕掉的链路/节点仍出现，状态“在 SIMAP 中实现或可从 SIMAP 访问”；SIMAP -03 冻结版没有状态节点，接口运行状态的标准落点是 RFC 8343 `oper-status`。表达方式需要设计并请负责人确认（不要自定义字段）。
   - 成熟产品：Batfish `Active`/`Admin_Up`，NetBox `Enabled`，SuzieQ interfaces 表的状态字段。
   - 验收：注入“接口 shutdown”后，模型与路径中能看出该链路不可用，链路仍保留。
3. **三层链路度量与 router-id**
   - 标准：RFC 8346 `l3-link-attributes/metric1`（IGP cost）与节点 `router-id`。需新增 OSPF 接口事实（Comware `display ospf interface`、IOS `show ip ospf interface brief`），先采样。
   - 成熟产品：Batfish `ospfInterfaceConfiguration` 的 `OSPF_Cost`；SuzieQ 的 ospf 表。
   - 验收：园区网各三层链路带 cost，能解释等价路径来自 cost 相同。
4. **设备树按 YANG 导出（底座，量大）**：把已采事实输出为 RFC 8343/8344/8349/8347 与 IEEE 802.1AB 的实例，复用研究包映射经验。拓扑节点与设备树如何关联标准未定义，**动手前须请负责人定方向**。
5. **其后**：OSPF/BGP 邻接作为协议图层；ACL（RFC 8519）进入路径（对应路线图 M2，Batfish/IP Fabric/Forward 都做策略感知路径）；SIMAP 历史/时间戳对接已有快照；清单。

**待负责人决定的事项**
- **H3C 聚合真实样本**：两台 BR 之间只有一根线，Comware 非空聚合输出还没有真机回显（解析器遇到会如实失败）。需要负责人在 GNS3 里把 BR1/BR2 网卡从 5 改为 6、加一根 GE6/0↔GE6/0，再由我们配 Route-Aggregation 并采样。
- STP 端口状态作为设备事实采集与否。

### 先读这些

1. 本节，然后第 2 节的硬约束与 Git 纪律。
2. 路线图第 0 节原则、§14 台账 2026-10-03 的各条（每条有文件、命令、结果、未完成项）。
3. `scripts/topology/campus_wan/README.md`、`tests/fixtures/topology/campus-wan/README.md`。
4. IETF 资料：[IETF 研读记录](network-digital-twin-ietf-study.md)、[原生模型审计](network-digital-twin-model-audit.md)、[验证包](../models/network-twin/README.md)；成熟产品资料：[开源参考](network-digital-twin-open-source-reference.md)（偏采集与解析）。

参考链接：[RFC 8345](https://www.rfc-editor.org/rfc/rfc8345.html)、[RFC 8346](https://www.rfc-editor.org/rfc/rfc8346.html)、[RFC 8944](https://www.rfc-editor.org/rfc/rfc8944.html)、[RFC 8347](https://www.rfc-editor.org/rfc/rfc8347.html)、[RFC 8349](https://www.rfc-editor.org/rfc/rfc8349.html)、[RFC 9375](https://www.rfc-editor.org/rfc/rfc9375.html)、[RFC 8529](https://www.rfc-editor.org/rfc/rfc8529.html)；[Batfish 配置属性](https://pybatfish.readthedocs.io/en/latest/notebooks/configProperties.html)、[NetBox 接口模型](https://netboxlabs.com/docs/netbox/models/dcim/interface/)、[SuzieQ 表](https://suzieq.readthedocs.io/en/latest/tables/)、[IP Fabric 发行说明](https://docs.ipfabric.io/latest/releases/release_notes/previous_releases/nimpee_v2/)、[Batfish 示例网络](https://github.com/batfish/batfish/tree/master/networks/example)。

---

## 历史交接：2026-09-30，GPT → Claude

交接日期：2026-09-30，本文已追加 GPT 接手后的验收与支线记录。项目根目录：`C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，Windows + PowerShell（也可用 Git Bash），分支 `feat/network-twin`。Claude 原始交接时已推送到 `origin`，止于 `de6a28f`、路线图 v1.19；后续本地提交不代表已经推送。上一份交接（Codex → Claude，2026-09-28，止于 `ec81310`）的内容已并入本文；其第 6 节的工具与标准边界仍然有效，保留在本文第 7 节。

**一句话现状：拓扑发现（SIMAP L2/L3）→ 终端定位 → 双向路径计算 → 知识问答自动登录路径设备取证诊断已打通。D-8 八个场景的首轮盲测已结束，根因命中 6/8，AI 未执行网络配置命令；场景 3 漏判，场景 4 用满预算后没有答案。达到原定数值及安全门槛，但结论质量问题尚未闭合，不能称每句话均有充分证据。负责人已追加授权 GPT 自行注入、测试和恢复实验故障。**

### 2026-09-30 交接内容：原始基线配置对比（历史记录；其后已完成并实测，见路线图 §14 与上文提交表）

负责人因 token 将尽要求本次停止扩展、写好交接。**Claude 下一步先验收下面这项，不要继续旧的巡检变化列表方案，也不要先重跑 D-8 全故障集。** 下文既有记录保留，若描述“生产提示未改”，指此前 D-8 收尾修复阶段；本次配置比较已按确认范围修改 `DIAGNOSIS` 提示。

### 负责人最新确认的方向

负责人先要求把巡检基线用于故障分析，随后明确纠正：“巡检这个应用……有大量的无效的变化数据，应该诊断时找基线原始数据，再做一个配置对比工具，然后直接对比需要的部分”。对当前配置来源，负责人选择了 **“诊断时读取指定配置块”**；已告知这是现有 D-8 配置导出限制的受控只读例外，保持脱敏、白名单和审计。

因此最终链路是：按管理地址和故障前时间找**原始配置快照** → 选择明确配置块 → 现场读取同范围 → 确定性文本差异 → AI 结合运行状态解释影响。不依赖 `NetworkChangeRecord` 或预生成差异，不启动新巡检，不新增 UI/迁移/YANG 字段。旧 `inspection__read_changes` 试作没有部署，已从源代码/工具注册撤回，只有本次自建文件移入忽略目录 `.runtime/abandoned-baseline-changes/`；不要恢复或提交它们。

### 已完成的实现

- `src/platform/device_access/config_sections.py`：纯函数范围验证、固定命令、同范围原始行提取、统一脱敏和有界对比，复用现有 `src/core/patrol/config_diff_bridge.py`（未修改旧巡检核心）。首版仅 Comware：`section=interface,target=GigabitEthernet3/0` 等完整接口名；`section=ospf,target=1` 等进程号。接口查询 `display current-configuration interface <完整接口名>`；OSPF 查询 `display current-configuration configuration ospf`，再严格提取指定进程。语法有 H3C 官方文档依据，**尚未在本实验设备上实测**。缺块、范围不唯一、未闭合、CLI 错误、截断不会报 unchanged；脱敏行不参加差异，不同占位符不会制造假变更；只有隐藏字段而可见部分相同时返回 unavailable。
- `src/apps/inspection/backend/config_baseline.py` 与 `tools.py`：内部工具 `inspection__read_config_section`，`evidence=False`、只读，经 registry 正常 App 鉴权。核验 snapshot 原始文本 SHA-256、完整性、命令和设备身份；返回基线 ID/运行/时间/摘要/行段及质量。已有资产关联必须严格一致；无关联的离线快照可精确匹配其持久 `management_ip`，并明确说明只能证明历史地址对应，不能证明地址未被复用。不能从设备名称猜 IP。
- `inspection/backend/diagnostics.py`：`list_snapshots` 新增可选 `host` 精确过滤并返回 `management_ip`；原设备/运行/时间分页仍保留。`read_snapshot` 补读也沿用统一脱敏。AI 找到快照后按 `run_id` 查询来源、用途和质量；历史基线不自动等于健康基线。
- `src/apps/device_access/backend/config_compare.py` 与 `tools.py`：AI 工具 `device_access__compare_config(snapshot_id, host, section, target, purpose)`。先经 registry 授权读取有效基线，再用公共 SessionHandle 发送 **1 条服务器构造的固定命令**；凭据沿用默认/已保存会话，失败不自动重试，finally 关闭。结果 ≤24KB，保留两侧脱敏片段、时间、差异和会话/命令引用。没有硬性 inspection App 依赖，普通 SSH 终端可单独用。
- `assistant/backend/diagnostic_workspace.py`：仅在有权限的基线提供方存在时开放比较工具；`platform`、`turn_id` 服务端填入，设备仍只能来自本轮定位/路径结果。每次比较计 1 条，与普通诊断共用 8 台/20 条预算。`dsh.py` 提示 AI 用原文和指定块，不用巡检变化列表，比较后还需运行状态证据。
- `assistant/backend/evidence.py`、`workspace.py`、`api.py`、`turns.py`：复合比较证据固定要求 `device-access` 和 `inspection` 两个 App 当前权限；最终提交引用、引用回读、步骤展开均重新鉴权。沿用原有展开结果，没有新增界面。
- `platform/device_access/governance.py`：接口/OSPF 可能携带的 authentication-mode、cipher、keychain 等敏感行在**平台审计和服务器会话日志持久化之前**脱敏。`device_access/backend/diagnosis.py` 同时窄修普通 run 的大小写/缩写配置导出绕过；普通 run 仍不能用 `display cur`、`show RUN` 等导出配置，比较是独立受控例外。

### 验证及当前部署

- 配置范围/基线与原诊断查询组：`venv/Scripts/python.exe -m pytest tests/unit/test_config_sections.py tests/unit/test_inspection_config_baseline.py tests/unit/test_assistant_diagnosis.py -q`，**56 passed**，只有 2 个既有 websockets 弃用提示。随后仅对新增文件做格式与等价函数写法整理，Ruff 通过。
- 新服务、真实 SQLite 基线、真实 registry/SessionHandle、假 SSH、平台白名单/审计/日志及官方 DSH 闭环组：77 项通过，随后新增 2 项（含密文一致不造假差异、超大块）后对应新工具组 27 项通过；共覆盖 79 个不同用例，分组间重复不累加为测试总数。DSH 测试用 stub 模型和假 SSH，**不是实机/真实模型验收**。
- 根会话最终相关查询/引用/展开/设备范围组：`venv/Scripts/python.exe -m pytest tests/unit/test_assistant_evidence_access.py tests/unit/test_assistant_tool_outputs.py tests/unit/test_device_access_app.py tests/unit/test_assistant_diagnosis.py tests/unit/test_assistant_progress.py -q -p no:cacheprovider`，**44 passed**。与前述组存在重叠，不相加。此前 30 项是中间检查，不另算。Ruff 对本项 21 个 Python 路径通过，其他线程 5 个暂存文件的 index/worktree 哈希核对未变。独立审查未发现未解决阻断项。
- 升级命令已执行：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps inspection,assistant,device-access,topology -NoBrowser`。目标版本 inspection **1.3.2**、assistant **0.8.2**、device-access **0.9.3**；topology 仍 **0.4.3**。命令退出 **0**，`verify.ps1` **9/9 App** 及 FastAPI/BFF/React/Worker 检查全部通过；项目已运行于 `http://localhost:3000/chat`。未修改设备配置。

- 对应新服务组合的精确命令：`venv/Scripts/python.exe -m pytest tests/unit/test_device_access_config_compare.py tests/unit/test_device_access_config_compare_dsh.py tests/unit/test_ssh_governance.py tests/unit/test_device_access_sessions.py -q`（77 passed，34.46 秒）；新增 2 项后仅重跑 `venv/Scripts/python.exe -m pytest tests/unit/test_device_access_config_compare.py -q`（27 passed，21.13 秒）。79 个不同用例由服务 27 + DSH 2 + governance 20 + sessions 30 组成，非同一轮同时运行。
- **代码已本地提交 `33ec796`**（`feat(diagnosis): compare scoped live config with raw baselines`），25 个本项代码/测试文件；此前 D-8 收尾修复是 `089c270`。本次交接与路线图另作文档提交，**均未推送**。没有执行 `.runtime/verify-config-scope.py`，也没有本次实机证明文件。其他线程暂存的 5 个页面以及环境生成注册文件保留。

### Claude 从这里继续

1. 先检查 `git status --short` 与上面的部署/提交状态，保留第 5 节的无关暂存页面、`generated.ts`/`generated.meta.json`；不恢复旧 read_changes。只有新问题需要改代码时才重跑对应测试；App 代码变更后必须重新部署。
2. **尚未实机执行任何本次配置查询。** 已准备 `.runtime/verify-config-scope.py`，但未运行；`.runtime/config-scope-live-proof.json` 在交接准备时不存在。脚本对两台 VSR 各用公共 SessionHandle 读取 GE3/0 与 OSPF 1（共 4 次独立只读会话，正常初始化另计），关闭后直接下载已有 MinIO `.log` 验证，不调用可能补写日志的 read_log/sync_log。最后通过真实 registry 读现有离线快照 `192.168.2.30` 的 GE1/0/1，仅核验原文块，不连接该离线设备。脚本不含口令，只保存脱敏后的元数据/摘要。
   ```powershell
   . scripts/windows/_port_utils.ps1
   Initialize-WindowsRuntimeEnvironment -ProjectRoot (Get-Location).Path
   $env:PYTHONPATH = (Get-Location).Path
   venv/Scripts/python.exe .runtime/verify-config-scope.py
   ```
   脚本用 `open('x')` 创建证明并逐次保存，若已有证明会拒绝再次启动；若中途失败或结果不明，先看已保存记录、stdout 和会话日志，只补核未完成部分，不删除证明后盲目重跑全部 SSH。脚本尚未执行，因此也可能存在验收脚本自身的小问题，应先审阅。
3. 本机只读数据库核查：`NetworkDevice` **0 条**，`display current-configuration` 原始快照 **127 份**，全是无 inventory 关联的离线数据，但有明确 `management_ip`。两台实验 VSR 的 `10.10.1.1/2` 没有对应巡检基线。**不能造一份历史基线或用其他设备快照冒充。** 实机固定查询通过只能说明命令/日志可用，不等于“真实历史基线 → 当前 VSR → AI 根因”的整体验收。需要后续取得合法对应基线后再做这一端到端验证，当前完整对比仅隔离 fixture 已验证。
4. 本次验收并未改变 D-8 首轮 **6/8**，不宣称推理缺陷解决。既有实验故障已于上一轮全部恢复；本轮未注入/改动设备配置。不要为了补齐证明擅自重跑全故障集或扩大厂商/配置范围。

## 1. 先读这些

1. 本文。
2. [路线图](network-digital-twin-roadmap.md)：
   - 第 0 节开头 2026-09-29 的负责人原则（最高优先级）。
   - 第 14 节台账中 D-1～D-8、P2-3 各行（每行有文件、命令、结果、未完成项）。
   - 文末 D-5、D-6、D-7、D-8 四张设计卡：这是负责人逐条确认过的范围，**只做卡上列出的内容**。
   - 第 11.0 节实验环境和迷你故障集 v0。
3. [首版方案](device-access-topology-plan.md) 第 1、3、7 节：SSH 公共能力的分层（平台核心 + SSH 终端 App，拓扑和诊断是调用方）。
4. 需要回看 IETF 模型研究时，再读 [IETF 研读记录](network-digital-twin-ietf-study.md)、[原生模型审计](network-digital-twin-model-audit.md)、[验证包入口](../models/network-twin/README.md)。

## 2. 负责人的工作方式与硬约束（务必遵守）

负责人对以下几点非常在意，违反过就会被严厉纠正：

- **简单即强大，简洁、可靠。这是开源产品。只做负责人要求的，不要自己加层级、字段、选项、权限机制或界面。** 曾因加了管理域、访问范围、凭据集、连接授权、按页面加侧栏等被要求全部删掉。
- **先想清楚再动手**：较大的改动先给出简短设计（要素、分层、标准怎么建模），等负责人确认后再实现；确认后可以自主推进，不必逐步请示。真正需要负责人决定的（尤其安全边界）要停下来问，并给出推荐。
- 顺序：**先建模 → 再用实验数据填充验证 → 最后补采集**；能力是从基础上“长出来”的，基础没做好不要跳到上层。
- 标准基线：**RFC 8345 + SIMAP YANG -03**（负责人称 8345bis），用 SIMAP 双向链路、多点链路；**不增加项目自定义 YANG 字段**。分析结果（定位、路径）以接口 JSON 返回，不新造 YANG 模型。
- **App 是可插拔包**：功能按归属放在各自 App 内，**不要把 App 的页面放进平台“系统管理”**（做过一次已回滚）。**SSH 是平台公共能力**：`src/platform/device_access` 是核心，`device-access` App（显示名“SSH 终端”）是终端入口，拓扑发现和 AI 诊断只是调用方，不要把 SSH 代码并入拓扑，也不要删掉终端。
- 界面风格与防火墙策略 App 一致（`src/apps/firewall_policy/frontend/pages/FirewallPolicyPage.tsx/.css`：面包屑顶栏、圆角窗口、#1671e8、圆角 9、控件高 36）。**负责人当前说“先不做 UI”**，除非明确要求，不要改界面。
- 原巡检 `src/core/patrol`、`src/core/ingestion` 及原 Netmiko 实现不修改。
- **实验设备口令不得写入代码、文档、测试、提交或报告。** 负责人已给实验 VSR（10.10.1.1、10.10.1.2）永久的只读登录授权；口令已加密保存在本机平台的“SSH 终端默认登录”和“拓扑默认登录”里，程序化调用走平台，不要把口令写进脚本。本机开发平台账号见登录页提示（仅 localhost）。
- 回答使用中文。

### Git 纪律

- 不要 `git reset --hard`、`git clean`、`git add .`；**只提交本项文件，用 `git commit -F <消息文件> -- <路径...>`**（工作区可能同时有其他会话暂存的改动，见第 5 节）。
- 提交前 `git diff --cached --check`（或对路径 `git diff --check`）必须通过；失败就停下修正，不要带病提交。
- 修改前后对比时，可 `git worktree add ../netops-agent-verify HEAD` 在干净检出上跑同样的测试，用完删除。
- 提交消息末尾保留 `Co-Authored-By` 之类署名按你自己的工具约定；推送前确认负责人同意（本分支此前已按负责人要求推送）。

## 3. 已完成到哪里（2026-09-28 之后）

| 提交 | 内容 |
|---|---|
| `acb9893`～`192eaf1` | IETF 研究收尾：inventory-topology 虚拟 NE 追踪（Q-IETF-1 定为模型层不关联）、IEEE LLDP/Bridge 模型、ARP/VLAN/LLDP 映射、P2-11 实机补采核查、采集计划按模型缺口重排 |
| `47957d7`～`62749ea` | D-1～D-4 拓扑发现：SIMAP L2/L3 构建器入 App（`model/builder.py`）、发现任务采集全部拓扑事实、快照/导出/画布、按域主机密钥（后被 D-5 简化）、`start.ps1 -ApplyDatabase/-UpgradeApps`、实机界面验收 |
| `f346301`、`f551399` | 画布显示双向链路两端、按角色布局 |
| `2796851`～`b892e8e` | 访问范围相关的来回修改（最终被 D-5 整体删除，不必再看） |
| `c248cb8`、`5287e29` | **D-5**：SSH 终端改为 SecureCRT 式（已保存会话、默认登录、批量改密、标签页、登录失败弹窗）；拓扑只用默认/临时登录，节点登录失败弹窗输入；删除管理域、访问范围、凭据集、连接授权（云端 Claude 会话完成） |
| `476918a`、`f34bfae` | **D-6** 终端定位（`model/locate.py`，`GET /snapshots/{id}/locate`，工具 `topology__locate`） |
| `154c968` | D-5/D-6 实机验收中修的 3 个问题（平台终端未对 SSH 异常分类、窄屏会话列表、节点登录失败自动弹窗） |
| `0c305dd` | 拓扑页统一为防火墙策略外观（布局保持原样，负责人否决了侧栏方案） |
| `3ac3f0c`、`2b86259` | **P2-3 恢复并完成**（固定版本 NTC 模板层；修 VLAN 前言）；**D-7** 双向路径计算（`model/path.py`，`GET /snapshots/{id}/path`，工具 `topology__trace_path`），发现任务开始采集路由表；实机验收 |
| `393b145` | 研究脚本的路由实例改用 App 路由解析器，RFC 8349 实例逐字节不变，26/26 |
| `d652dc4`、`de6a28f` | **D-8** 诊断闭环（见下节）；场景 0 实机通过 |
| `5810e4f`、`46a6c1d` | GPT 接手：场景 1 实机记录；知识问答执行过程展开查看参数与结果 |
| `6899ded`、`a64428f`、`2701275` | 部署排空修复；全平台 SSH 白名单、批量执行与审计；CRT 风格服务器会话日志 |
| `0e82ad3`、`3e95be3` | SSH 终端内编辑命令；恢复受控 Comware 远端 Tab 补全 |
| `089c270` | D-8 预算收尾只允许提交校验后的答案；场景 4-R2 独立记录 |
| `33ec796` | 原始基线与指定现场配置块对比，已部署；实机验收待 Claude 继续 |

## 4. 当前进行中：D-8 诊断闭环验收

设计见路线图文末 D-8 设计卡。负责人选定方案 A：AI 可用保存的默认登录登录路径上的设备，只执行白名单只读命令，每步审计。

实现要点（便于接手排查）：

- `src/apps/device_access/backend/diagnosis.py`：按地址取该地址的已保存会话或默认登录，经平台受控会话（`src/platform/device_access/sessions.py`）执行；L1/L2 执行，L3/L4 不执行；配置导出命令不发送；一次调用一台设备 ≤5 条命令；输出每条截 3000 字节；工具 `device_access__run`。
- `src/apps/assistant/backend/diagnostic_workspace.py`：每轮的登录范围来自本轮 `topology__trace_path`/`topology__locate` 结果中的已采集且平台已识别设备（≤8 台、≤20 条命令）；`platform`、`turn_id` 由服务端填，模型不可见。
- `src/apps/assistant/backend/turns.py`：每轮启用诊断工作区；单轮 300 秒、30 次工具调用、15 轮（平台上限），租约 320 秒。
- `src/platform/agent_runtime/dsh.py`：`DIAGNOSIS` 提示词（排查方法、结论四小节：已证实/候选/已排除/待验证）。
- 审计：`app_device_access_session_audit`，`target_ref` 形如 `diagnosis:<轮次ID>:<设备地址>`。

**2026-09-30 GPT 接手补验**：按 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 启动，全部部署检查通过，无需迁移或升级应用。负责人注入场景 1 后，AI 在不重新发现、不获知故障位置的情况下，命中 VSR-1（10.10.1.1）GE2/0 管理性关闭。15 条命令全部 L1，4 次会话/42 条审计，5 个答案引用核验通过；输出和完整证据分别在 `.runtime/d8-case1.txt`、`.runtime/d8-case1-evidence.json`。两台设备拒绝 `display ospf peer brief` 的参数，本轮未直接取得邻居状态；答案关于邻居断开及对端不可达的部分表述属于推断。详细记录见路线图第 14 节，不能把根因命中等同于每句结论均已证实。

**2026-09-30 自主验收续测**（各轮 ID、统计及限制见路线图第 14 节）：

- 负责人授权覆盖本轮 D-8 故障注入和恢复，不等于给诊断 AI 放开配置权限。平台人工实验操作记 `d8-lab:*`，AI 审计按 `diagnosis:<轮次>` 单独统计；全局 SSH 白名单仍是版本 1、`[".*"]`。实验设备口令不落文件。
- 首轮 0/1/2/5/6/7 命中；3 未提出 PC1 默认网关错误，4 达到 15 轮上限，只返回通用失败提示。场景 4 等待 ARP/MAC 自然老化后才提问，盲测前核实 VSR-2 GE3/0 仍 UP；使用 GNS3 PC1–Switch1 链路暂停传包，没有清表或修改老化参数。各首轮及后续补测的注入均已恢复；最后的场景 4-R2 于 14:22:16 验证恢复，此后最终检查 16 项全部通过，双向 PC ping 各 3/3、OSPF Full、业务路由、VLAN 与临时 ACL 均恢复。最终证明 `.runtime/d8-final-state-proof.json`；13:52 的旧证明单独保存在 `.runtime/d8-final-state-before-case4-r2-proof.json`。
- 场景 3 已按旧方案补测“重新发现后再诊断”：两台 VSR 发现成功，新快照 `a07321f029a04c20b0bc600cf91c39ab` 成为当前快照，旧快照保留。新 MAC 已正确采到，仍未命中网关错误，且答案把实际存在的互联 ARP 读成缺失，不能归因于旧快照一项。记录 `.runtime/d8-case3-fresh-{discovery,evidence,review}.json`；网关已恢复。
- 首轮场景 2–7 合计 101 条 AI 命令（96 L1、5 L2）、26 个会话，诊断审计 280 条、平台审计 332 条。服务器已有的 26 份日志共 86,355 bytes，与审计投影逐字节一致；含初始化共 127 条命令结果。另行核对本轮拓扑发现的 4 个连接、18 条命令（含 2 条初始化），每条均记录策略版本 1；4 份已有日志共 29,995 bytes，同样匹配。以上仅验证默认放行路径及已脱敏留存，未收紧规则做实机拒绝测试；补测和人工实验操作另计。
- 证据留存完整不等于推理正确。重点未闭合项：设备/接口归属混读、旧 MAC 差异误判 IP 冲突、路由器 ping 被称作 PC 间单向连通、未取得 ACL 绑定却确认丢弃、错误引用和单次计数推断趋势。不能用注入脚本知道的真值替 AI 补证。
- 两版 `DIAGNOSIS` 提示词在同一批脱敏固定证据上试验均出现明显错误，全部保留在 `.runtime/d8-prompt-replay*`，没有应用到生产。生产提示词、模型和预算均未改。仅针对场景 4 的预算收尾缺口，平台收尾阶段只提供答案提交工具，在执行前拒绝其他工具，未通过原引用检查的内容不能发布。相关回归 29 项通过、1 项因 Windows 缺少 Linux `/proc` 跳过；13:59 平台重启及 9/9 应用部署检查通过，无需升级 App。补测同时使用新快照，结果须独立记录，不能把变化完全归因于收尾修复。
- 场景 4-R2 轮次 `1e4fee9f18814eefa4f13d92319b3415` 在 8 轮／12 次工具／52.063 秒内完成答案提交，首候选指向 PC1 接入／主机失联；20 个命令条目中 1 条多管道命令被判 L4、发送前拒绝，实际发送 19 条（17 L1、2 L2），4 个会话、52／59 条诊断／平台审计。未达到收尾阈值，不能宣称本次实机触发了强制收尾。证据 `.runtime/d8-case4-r2-evidence.json`，逐句限制见独立复核；补测不改写首轮 6/8。
- R2 仍存在过度排除：未指定源地址的路由器 ping 不能证明 PC 间对称转发；GE3/0 的 UP 和零错误不能排除下游链路／主机问题；未读取 PC1 配置却在开头排除网关问题。此次 `inspection__list_events` 只传 `limit:20`，返回其他设备的历史事件且还有下一页，不能据此排除实验设备告警。命令、引用和日志留存核验通过不消除这些结论缺陷。
- 本轮场景 2–7 及两次补测合计 37 个 AI 会话日志均核验通过，共 122,870 bytes、175 条命令结果（含 37 条分页初始化），不含先前场景 0、1。人工注入／观察／恢复另计：113 个关闭的 SSH 会话、1,111 条审计、166,028 bytes 的已有服务器日志均匹配，证明 `.runtime/d8-lab-server-logs-proof.json`；命令返回完整不代表 CLI 语义成功，原错误和纠正记录保留。
- 复测脚本：`.runtime/d8-case-run.py <2|3|5|6|7> --suffix r2`；场景 4 用独立脚本及持久状态，异常恢复支持 `--restore-only`。单次诊断 `.runtime/d8-eval-run.py <唯一标签>`，若提问结果不明，只用 `--collect <证据文件>` 只读补收，不能重复提交。脚本和原始证据留在忽略目录，不提交。

**下一步**：先解决 D-8 的预算内收尾和结论可靠性，保留首轮与补测各自成绩；不能把固定证据回放或补测成功覆盖原失败。这里只做现有卡的必要修复，尚不扩展快照自动刷新、VRF 或服务端结构化诊断校验。

**负责人后续追加已实施（最新状态见文首交接）**：巡检接入方向最终确定为“基线原文 → 指定块现场读取 → 配置比较”，代码提交 `33ec796`，本机部署通过，实机比较尚待验收。原先设想直接读取变化列表已撤回。`inspection__launch` 仍不开放给诊断，不发起新巡检；`read_batch` 仍不是普通诊断证据工具。本次只改已确认的配置比较提示与工具，`thinking` 仍为 `disabled`，模型和既有轮次/时间上限没有改动。

注意：Comware 默认不回 TTL 超时，`tracert` 中间跳显示 `* * *`，这是设备默认行为，不是故障；开启需要改配置，不要做。

验收完成后，下一张卡由负责人决定。路线图里的候选：快照增量刷新（P4-2）、P2-10 LLM 兜底与模板沉淀、多厂商、VRF。**先给设计、等确认。**

### 4.1 执行结果展示（2026-09-30 负责人追加要求）

知识问答已升至 0.8.1：每条执行记录默认一行收起，点击后展示真实参数与结果；SSH 输出按设备、命令逐条展示，保留表格列对齐。业务结果展开时才读取，并核对本轮归属和源 App 当前权限，公共进度/SSE 不直接携带业务正文。历史记录只恢复可唯一匹配的已留存证据：场景 1 的 4 次 SSH 与 1 次路径结果可看，2 次定位结果未留存，页面明确提示。未为展示重跑诊断或设备命令。测试、部署与浏览器验收详见路线图第 14 节“负责人追加：执行过程展开查看结果”；本次界面授权仅覆盖此项。

### 4.2 全平台 SSH 白名单、批量执行与操作记录（2026-09-30 负责人确认，已完成部署与本地验收）

负责人另行确认本支线，设计与台账见路线图 **S-1**。本次明确授权 SSH 终端相关界面及既有在线 SSH 调用接线；只在此范围内调整前文“暂不做 UI／原巡检不修改”的限制，不扩展 D-5～D-8，不改采集模型或解析语义。

- 平台统一维护一份管理员正则白名单，完整命令匹配任一规则才发送；默认 `[".*"]`，空列表拒绝全部。规则覆盖终端、批量、诊断及既有在线平台 SSH 调用，含初始化/识别命令；**AI 仍叠加 D-8 的 L1/L2、设备范围和预算限制**，不因默认放行而获得配置权限。
- 终端内本地编辑完整单行命令，按回车发送；本地按键不直接转发设备，不提供任意控制字符透传。受控远端 Tab 的后续追加见 4.5。设备要求密码、分页或交互确认时停止；命令失败不自动重试。批量按顺序执行，最多 20 个会话、每台 50 条命令，整批 15 分钟；支持停止，取消不能回滚已经执行的命令。
- 白名单存平台 `netops_ssh_policy`（迁移 `0107_ssh_governance`）；所有在线 SSH 操作写入平台 `netops_audit_logs`，普通用户查看本人、管理员查看全部，命令和输出按已知 secret 及敏感内容脱敏，现有 D-8 会话审计保留。源码中的独立实验采样脚本不属于在线平台调用范围；实验设备口令仍不得写入任何文件。
- 已部署：`device-access 0.9.0`、`device-management 1.0.4`、`inspection 1.3.1`、`rule-governance 1.4.6`；拓扑仍为 0.4.3，知识问答仍为 0.8.1。部署前备份：`backups/20260930-023847-before-ssh-governance/postgres.dump`，109079394 bytes。升级命令包含依赖拓扑：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology,device-management,inspection,rule-governance -NoBrowser`，退出 0；最终 `verify.ps1` 退出 0，9/9 已启用 App 以及 FastAPI/BFF/React/Worker 全部通过。排空旧/新包摘要比较修复已单独提交 `6899ded`，生命周期回归 10 项通过。
- 验证：公共 SSH 回归 663 项通过，修补后相关 72 项重跑通过（与前批部分重复，不相加）；App/主机密钥/人工命令/终端分组最终 56 项通过（包含此前 18 + 5）；治理分组 20 项通过（含审计时间修复）；前端 22 项通过，改动文件 ESLint 与 diff 检查通过。旧在线调用链最终隔离回归为 119 通过、1 跳过、7 排除；跳过项需要独立 PostgreSQL 验证 advisory lock，7 项为原始 HEAD 已复现的既有失败，详情见 S-1 台账。
- 实机：两台 VSR 各执行 `display clock`、`display ip interface brief`，4 条均成功；批次 `30fe653e4efd44c08a06b536c05183a7` 留存 16 条审计。另在 VSR-1 单终端执行 `display clock` 成功后停止，审计完整。默认 `[".*"]` 未改，未改实验设备配置、未重跑 D-8。截图 `.runtime/ssh-batch-preview.png`、`.runtime/ssh-audit-preview.png` 不提交。
- 审计页面曾多显示 8 小时，现于查询侧按既有数据库 `TimeZone` 解释无时区时间，不改写入方式、不迁移历史。网关重启后健康检查正常，浏览器确认时间从错误的 18:54 恢复 10:54，展开命令仍保留设备返回的 02:53:44 UTC。

### 4.3 按连接自动保存 SSH 会话日志（2026-09-30 负责人追加确认，已完成部署与本地验收）

- S-1 追加 CRT 风格的会话日志：平台复用 MinIO，在 `platform/ssh-session-logs` 前缀下按连接 ID 隔离目录，自动保存“设备名_北京时间（精确到微秒）.log”。新连接的日志逐事件持久化，写入失败即停止；重试使用新连接 ID，首条日志写入失败也正确收尾。终端、批量、诊断和既有在线调用都传递连接时的设备名快照；Netmiko 初始化先记录日志，再检查命令白名单。
- 操作记录默认显示“日志文件／状态／下载”三列，一次连接一行；文件名下显示地址、连接时间、来源，管理员另见用户。点击预览连续纯文本命令与回显，下载经过现有认证；地址筛选、详情失败重试及切换文件的竞态保护已实现。管理事件保留在独立“管理记录”入口，普通用户仍只读本人、管理员可读全部。
- 原平台审计不删，白名单不改，本次追加不新增数据表或迁移。历史只按连接恢复已留存内容，没有设备名快照时用 IP；不补执行 SSH，也不把恢复内容称作完整原始 PTY。日志沿用脱敏边界，保留实际缺失、失败或截断提示。
- 验证：前端 18 项、App 日志 API 7 项通过；平台日志相关 86 项与拓扑识别 92 项是不同用例，合计 178 项通过；名称接线 73 项、drivers/legacy 61 项（含 4 项新增）通过。后两组与其他分组有重叠，不累加为独立总数。相关 Ruff、前端 ESLint、diff 检查通过，真实 MinIO 健康检查返回 200。
- 已部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9 个应用及全套启动检查全部通过；`device-access 0.9.1` 已启用，拓扑仍为 0.4.3。
- 本轮实机通过页面批量仅在 VSR-1、VSR-2 各执行一次 `display clock`，均成功。打开日志列表或预览前，只读脚本直接读取 MinIO 两个新对象，证实服务器已自动保存 `VSR-1_2026-09-30_11-31-16.065586.log`、`VSR-2_2026-09-30_11-31-18.535872.log`，各 483 bytes；该核验未调用 `read_log`／`sync_log`。随后页面列表与完整预览验证通过；浏览器实际点击下载成功，VSR-1 文件保存到 `C:/Users/wangd/Downloads/VSR-1_2026-09-30_11-31-16.065586.log`，483 bytes，SHA-256 为 `476c7e1f7b3b0590d37605d857d977d85b56dd19001078e0193c4cf27ce346ff`，与预览前 MinIO 原文件完全一致。未改设备配置或白名单。
- 自动保存证明 `.runtime/ssh-session-files-proof.json`，页面截图 `.runtime/ssh-session-files-preview.png`、`.runtime/ssh-session-log-preview.png`，均留本地、不提交。

> **2026-09-30 已被取代**：4.4 本地编辑与 4.5 远端 Tab 已删除，改为按键直通 + 回车拦截白名单（参照 JumpServer），见路线图第 14 节“终端按键直通 + 回车拦截白名单”。以下两节仅作历史记录。

### 4.4 终端内本地编辑命令（2026-09-30；**已被 `c245d4e` 按键直通取代，勿恢复浏览器侧行编辑**）

- 共享前端 `DeviceTerminal.tsx` 改为直接在 xterm 内输入，去掉底部独立输入框、执行按钮和旧说明。新增 `LocalCommandLine` 仅处理本地编辑；Enter 仍发送完整 `command` 帧，沿用后端白名单和会话日志，不改变后端协议、App 版本或数据库。
- 支持插入、左右、Home/End、退格/Delete、↑↓当前会话历史、Unicode 及长命令单行水平滚动。单行粘贴不会自动提交，多行或控制串粘贴拒绝；执行中禁止新提交，被拒绝后恢复草稿。远端回显前擦除本地草稿，避免命令重复显示。
- 验证：组件 12 项、编辑辅助 13 项联合通过，共 25 项，包含真实 xterm 的缩宽和回显，以及 UTF-8 字节帧超长时不发送、可修正后再提交；4 个前端文件 ESLint 全部通过。生产构建通过（15.95 秒），刷新页面已生效，无需升级 App，SSH 终端仍为 0.9.1；最后仅追加测试，生产代码与实机验收版本一致。
- 实机在 VSR-1 浏览器终端直输 `display clocx`，退格修正为 `display clock`，仅按一次 Enter，命令成功且回显仅出现一次；↑↓切换草稿及拒绝多行粘贴均已验证，随后停止连接。直接只读 MinIO 的 `VSR-1_2026-09-30_11-47-33.663235.log`（716 bytes），确认仅有 1 条 `display clock`、成功结果及连接结束，多行粘贴未产生额外执行记录。核验脚本 `.runtime/verify-inline-input.py`、截图 `.runtime/ssh-inline-input-preview.png` 留本地、不提交。
- 本轮本地编辑未包含远端 Tab；负责人后续确认其独立设计后另行实施，进展见 4.5。

### 4.5 Comware 受控远端 Tab（2026-09-30；**已被 `c245d4e` 取代：按键直通，设备自己做 Tab/?/历史，回车时服务器按白名单检查**）

- 负责人已确认方案，平台、App 协议与共享前端接线已完成。首批仅对 Comware 7 开放，须同时匹配 SSH banner 与 CLI 提示符；首次补全通过原白名单和审计执行 `display hotkey | include (hotkeys|CTRL_[EXR])`，只接受系统保留的 Ctrl+E/X/R 清行与重绘定义，不使用可重绑快捷键或禁分页配置命令。
- 光标在行末时，Tab 发送独立 `complete` 帧，包含请求 ID、草稿版本和 `tab_count`（默认 1，上限 32，仅作前端轮次与审计）。每个请求只向设备发送一次初始草稿加 Tab，连续请求沿用初始草稿，候选随设备自身状态推进；任何本地编辑重置前端轮次，不假定设备候选索引归零。补全期间冻结本地编辑，过期响应不应用。输入限受支持的单行 ASCII 前缀、最多 256 字符，返回最多 511 字符；不发送换行。完成后清行并核对空提示符，无法确认、超时或日志失败即停止连接。Enter 仍发送完整 `command`，重新检查当前白名单，补全结果不授予执行权限。
- 请求、补全结果及前置校验拒绝沿用平台审计和服务器会话日志；协议去重区分命令与补全，同一请求 ID 改内容会终止连接。补全纳入既有会话时限、输入/输出预算，并另设草稿长度、响应大小与等待时限边界；单次草稿补全及清理共限 3 秒，首次快捷键查询单独限 15 秒。
- 验证：最终 4 组后端联合 95 项通过（29.38 秒），前端 37 项通过，相关 ESLint、Ruff 和 14 个任务路径的 diff 检查通过；最新生产构建通过（8.40 秒）。这些是本轮分组结果，不与前文各轮回归累加。
- 已部署 `device-access 0.9.2`，拓扑仍为 0.4.3：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9/9 应用检查通过。最终平台代码以 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 重新加载，数据库检查通过、无需再次升级 App；该命令退出 0，最终 9/9 应用及 React/BFF/FastAPI/Celery 全部通过。
- 实机公共 `HumanCLI` 连续 3 个请求各发送一次 Tab，`display cl` 依次得到 `client-verify`、`clock`、`cloud-management`，执行 `display clock` 成功后仍可再补全。浏览器最终实测第一次 Tab 得到 `client-verify`，第二次得到 `clock`，按 Enter 成功返回 `04:22:25 UTC`，命令回显仅一次，随后停止连接。
- 页面预览前直接只读 MinIO 的 `VSR-1_2026-09-30_12-21-42.776582.log`，1534 bytes，会话 `aa1aa0ddfbd146258a0c13648b288a27`；仅执行一次初始化只读快捷键查询和一次 `display clock`，两次补全请求/结果、远端已清空和连接结束均留存。`.runtime/verify-tab-completion.py` 断言通过，证明 `.runtime/ssh-tab-log-proof.json`，截图 `.runtime/ssh-tab-completion-preview.png`、`.runtime/ssh-tab-result-preview.png`，均留本地、不提交。本轮没有修改设备配置或白名单，实验设备口令未写入文件。

## 5. 本机状态与注意事项

- 已部署（本机）：知识问答 0.8.2、SSH 终端 0.9.3、拓扑 0.4.3、设备管理 1.0.4、巡检 1.3.2、规则治理 1.4.6。数据库在 D-5/D-6 前备份于 `backups/20260930-001850-before-d5-d6/`；S-1 前备份于 `backups/20260930-023847-before-ssh-governance/postgres.dump`（109079394 bytes）。
- 本机平台里已配置：SSH 终端默认登录与两条会话 VSR-1/VSR-2；拓扑默认登录；两台 VSR 的主机密钥。“实验网络 lab”的当前快照为 `a07321f029a04c20b0bc600cf91c39ab`（本轮拓扑 0.4.3 发现，含路由表与当前终端 MAC），原快照保留。
- 部署命令：`scripts\windows\start.ps1 -SkipLangfuse -UpgradeApps <app,...> -NoBrowser`。
  - **修改任何 App 的代码后，该 App 的摘要与已部署版本不一致，平台会把它判为不可用**（依赖它的 App 也会不可用），直到用 `-UpgradeApps` 重新部署。
  - 只改平台代码（`src/platform`、`src/gateway`）时不用升级 App，但要重启服务（不带参数运行 `start.ps1`）。
  - 如果服务全停（机器重启后常见），`-UpgradeApps` 会在“排空”步骤报 `ConnectError: drain incomplete`：先临时起一个网关让排空检查通过，再重跑。做法见台账 D-5 行；临时网关会被脚本在第 3 步自动停掉。
  - Docker Desktop 需在运行；刚启动时 PostgreSQL 可能短暂连不上。
- 工作区里有**不属于本线工作的改动**，不要提交也不要丢弃：
  - `web/react_frontend/src/pages/{ChatPage,ChatPage.test,SkillsPage,StatusPage,StoragePage}.tsx` 已暂存：另一个会话在修 `components/Layout` 的大小写引用（Linux 上构建失败的既有问题），由它自己提交。
  - `web/react_frontend/src/apps/generated.ts`、`generated.meta.json`：按本机环境生成的 App 注册摘要，历来不提交。
- `git stash` 里的 “P2-3 paused work…” 和 `backups/p2-3-paused-20260929/` 已经过时（P2-3 已在 `3ac3f0c` 提交），只作备份，删除前征得负责人同意。
- 已知与本线无关的失败测试（在干净检出上同样失败）：`test_workflow_plugins.py` 3 项、`test_rule_asset_bundles.py`、`test_command_autopilot_integration.py`、`test_check_item_assessment_runtime.py`、`test_chat_intent_match.py` 各 1 项。`dsh.py`、`src/gateway/schemas.py` 有既有的 Ruff 行长/导入顺序问题，不要顺手整文件格式化。
- S-1 旧调用链另有 7 项原始 HEAD 既有失败（设备管理 2 项、巡检 runner 5 项，名单见路线图 S-1 台账），与上一条旧交接的 7 项不是同一组。最终隔离回归为 119 通过、1 个 PostgreSQL advisory lock 用例跳过、7 项排除，不能称全套无失败。
- 早期旧测试隔离不完整，在本机留下 3 条 `operator-1`／`127.0.0.1`／`patrol-1` 的回环测试审计（opening/open_failed/closed）。未连接实验设备，记录保留，不是实验设备故障；后续治理及旧调用边界单测已使用临时 SQLite 并阻断连接本机 PostgreSQL。本项以实机批次及用户 `1` 的记录为验收依据。
- 本机 pip 配置的镜像是 http，会被拒绝；需要安装时用 `--index-url https://mirrors.aliyun.com/pypi/simple/`，不要改全局配置。

## 6. 常用验证

```bash
# 拓扑 / SSH 终端 / DSH / 知识问答相关后端（约 3 分钟）
venv/Scripts/python.exe -m pytest tests/unit -k "topology or device_access or dsh or assistant" -q -p no:cacheprovider
# 前端（在 web/react_frontend 下）
npx vitest run ../../src/apps/topology/frontend ../../src/apps/device_access/frontend src/platform/terminal
npx eslint --no-eslintrc -c .eslintrc.cjs --resolve-plugins-relative-to . <文件>
npx playwright test e2e/topology.spec.ts
# 模型研究（研究环境，见下表）
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py
```

| 用途 | 路径/版本 |
|---|---|
| 应用 Python、Ruff | `venv/Scripts/python.exe` |
| 独立模型研究 Python | `.runtime/yang-model-tools/Scripts/python.exe`，pyang 2.7.1、yangson 1.6.3；2026-09-30 加装 textfsm 2.1.0、ntc-templates 9.1.0（与拓扑 App 锁定一致，供实例构建调用 App 的路由模板层；本机 pip 镜像为 http，安装时显式用 `--index-url https://mirrors.aliyun.com/pypi/simple/`） |
| 已构建模型包 | `.runtime/ietf-study/model-package/` |
| 锁定源缓存 | `.runtime/ietf-study/reproducible-audit/sources/` |
| libyang 验证镜像 | `netops-ietf-mount-verify:5.8.6`（构建与来源锁在 `models/network-twin/mount/`） |

## 7. 已踩过的工具和标准边界（沿用上一份交接）

1. SIMAP 原文不能编译是原文问题（作者仓库同类错误），沿用锁定的兼容补丁，不要改写原文或草案语义。
2. Yangson 不自动实现 Schema Mount；挂载验证用 libyang 5.8.6（3.12.2 会崩溃，不算负例）。严格验证不传 `-t`，用 `--merge`；`-t get` 只是解析。
3. RFC 8342 §5.3 不是缺字段的豁免；部分观测保留缺项诊断，不编造值（如计数器中断时间）。
4. 核对真实字段名：RFC 8529 用 `bind-ni-name`；路由状态下一跳用 address、无 index；双下一跳不证明数据平面 ECMP。
5. 生成文件的行尾会影响摘要：`.gitattributes` 固定了 LF 的目录，新增生成目录注意同步；Windows 下 Python `write_text` 会写 CRLF，脚本里用 `write_bytes` 或 `newline="\n"`。
6. 路由表按设计走模板层（7.3），**不写通用正则解析路由**。模板固定 ntc-templates 9.1.0 与逐文件 SHA-256。
7. 定位/路径只用快照观测，确定性计算，不让 LLM 猜；数据不足就以明确原因结束分支。
