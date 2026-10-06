# M5：VPN、EVPN/VXLAN 与 DC fabric

按[实施方案 §8](../../../docs/network-digital-twin-implementation.md) 的 M5 执行（负责人 2026-10-03 批准的设计），topology App 0.7.1（0.7.0 加上助手工具 `vpn_services`），依赖规则治理 1.5.0。原则同 M1—M4：

- 只输出标准节点。
- 设备没有给出的列表键不构造；网络层标识（`vpn-id`、`fabric-id`）由平台按固定规则分配。
- 证明不了的不填，作为缺口报告。

实验网是 `netops-vpn-fabric`（[拓扑与地址](../../../scripts/topology/vpn_fabric/README.md)），包括：

- EVPN-VXLAN fabric：IOL 做 spine/leaf，H3C VSR 做 leaf。
- MPLS L3VPN：IOL 做 PE/P，VSR 做 PE。

回归素材是 `tests/fixtures/topology/vpn-fabric`（批次 `20261003-225812`，203 份观测，密文扫描命中 0）。

## 采集与解析

全部经规则治理的系统基础检查项采集和解析：命令和模板都由规则治理发布，拓扑只消费（见 [拓扑消费规则治理](../../../docs/topology-rule-consumption.md)）。M5 新增的检查项及其用途：

| 检查项 | IOS | Comware 7 | 用途 |
|---|---|---|---|
| `vrf_detail` | `show vrf detail` | `display ip vpn-instance instance-name <名>` | 接口绑定、RD、各地址族 RT |
| `bgp_vpnv4_peers` / `bgp_evpn_peers` | `show bgp vpnv4 unicast all summary` / `show bgp l2vpn evpn summary` | `display bgp peer vpnv4` / `display bgp peer l2vpn evpn` | VPN 地址族会话 |
| `ldp_peers` | `show mpls ldp neighbor` | `display mpls ldp peer` | LDP 会话、发现源 |
| `mpls_lfib` | `show mpls forwarding-table` | `display mpls lsp` | 标签转发 |
| `evpn_instances` | `show l2vpn evpn evi detail` | `display evpn instance verbose` | EVI、RD/RT、VNI、VTEP、网关 |
| `evpn_peers` | `show nve peers` | `display evpn auto-discovery imet` | VNI 对端 |
| `vxlan_vnis` | `show nve vni` | `display l2vpn vsi verbose` | L2/L3 VNI、绑定的隧道与端口 |
| `tunnel_interfaces` | — | `display interface tunnel` | VXLAN 隧道源/目的 |
| `evpn_mac` | `show l2vpn evpn mac` | `display evpn route mac` | EVPN MAC 路由 |

## 叠加层解读与路径

`model/overlay.py` 的 `prepare()` 在不改原始事实的前提下，把实例绑定和隧道写明，拓扑构建和路径引擎都读同一份结果：

- Comware ARP 按接口或 VSI 网关归入实例。
- 下一跳的出接口属于另一个实例时，视为本地泄漏。
- 经 L3VNI 接口的下一跳是 VXLAN 隧道。
- VRF 中的 BGP 路由，在设备有 VPNv4 会话时，是走 MPLS 的隧道。

路径引擎（`model/path.py`）的隧道语义：

- 进隧道后按端点在底层转发：MPLS 逐跳检查标签，VXLAN 检查隧道状态。
- 到达端点后，在接收实例中重新路由；接收实例按 RT 以及"是否本实例始发"选出。
- 指定了某端的实例、但实际送达了另一个实例时，结论是 `other_instance`。

已在实验网素材上验证的路径用例（`tests/unit/test_topology_vpn_fabric.py`）：

| 场景 | 结果 |
|---|---|
| A1→A2，MPLS VPN | pe1[CUST-A]〈mpls〉→ p → pe2[CUST-A]，往返都 complete |
| A1→S2，RT 泄漏 | 到 pe2[SHARED]，complete |
| B1→S2，CUST-B 隔离 | 去程 no_route；回程到的是 CUST-A 的 10.1.1.10，结论 other_instance |
| H-B2→H-B3，L3VNI 对称 IRB | leaf2[BLUE]〈vxlan 50020〉→ spine1/spine2 → leaf3[BLUE]，两条等价路径 |
| RED/BLUE 同网段 | 没有网关能证明主机在哪个租户：same_subnet_unproven；指定 RED 时 no_gateway |
| 隧道 down、缺标签 | tunnel_down；lsp_not_observed 缺口，partial |

## 标准投影

### 网络服务：L3NM（RFC 9182）与 L2NM（RFC 9291）

`model/services.py`，词汇用 vpn-common（RFC 9181）。接口是 `GET /snapshots/{id}/services`，和定位、路径一样由该快照自己的观测计算。助手工具 `topology__vpn_services`（0.7.1）在各网络的当前快照上回答同一问题，只返回存在 VPN 的网络。

- **分组**：导出同一个 RT 的实例属于同一个服务，因为它们始发的路由带同一个 RT，任何导入策略都分不开。只靠导入关联的两组（共享服务泄漏、hub 与 spoke）仍是两个服务：导入写在各实例的 RT 里，并列在 `relations` 中。没有任何 RT 的实例（如管理 VRF）是本地实例，不是 VPN。
- **标识**：
  - `vpn-id` 取服务里最小的导出 RT（如 `…:l3vpn:0:65200:1`）。
  - `vpn-node-id` 是该设备本地实例在 SIMAP 中的节点 ID，`ne-id` 是设备节点 ID。
  - 所有成员的本地名相同时，才填 `vpn-name`。
- **RT/RD**：按 RFC 8294 写成带类型的形式。CLI 的 "ASN:nn"、"IPv4:nn" 按 RFC 4360/4364 分别是类型 0、2、1，如 `65200:1` → `0:65200:1`，`10.255.10.12:2` → `1:10.255.10.12:2`。asdot 等无法判断类型的值记缺口，不导出。RT 按方向分组：两向都有的为 both，否则 import 或 export。
- **拓扑**：每个成员都至少导入其他每个成员的一个导出 RT 时为 any-to-any，否则为 custom。
- **承载**：所有成员都能证明时才写。MPLS VPN 要求有运行中的 LDP 会话和已建立的 VPNv4 会话，写 `ldp`。EVPN 的 IP VRF 要求绑定了 L3VNI，写 `vxlan-trans`。
- **接入**：实例的接口，L3VNI 接口除外（它属于承载，不是接入）。附带观测到的 IPv4 地址和管理/运行状态；EVI 的网关接口标为 `irb`。
- **IP VRF 走 EVPN 时**：如 BLUE 的对称 IRB，它是 L3NM 服务，承载为 VXLAN；它下面的 EVI 另作 L2NM 服务。
- **EVI 的 RD 和 RT**：写在 `bgp-auto-discovery` 下。RFC 9291 只允许它与 `bgp-ad-enabled` 同时出现，因此要求每个成员设备都有已建立的 BGP EVPN 会话；否则记缺口 `bgp_auto_discovery_not_proven`。

实验网结果：

| 模型 | 服务 | 成员 | 说明 |
|---|---|---|---|
| L3NM | CUST-A | pe1、pe2 | any-to-any，承载 ldp；两端都导入 65200:99（来自 SHARED） |
| L3NM | CUST-B | pe1 | 与 CUST-A 同为 10.1.1.0/24，属于不同服务 |
| L3NM | SHARED | pe2 | 导入 65200:1；与 CUST-A 的泄漏是 relation，不合并 |
| L3NM | BLUE | leaf2、leaf3 | 承载 vxlan-trans，接入为 Vlan20 / Vsi-interface20（irb） |
| L2NM | `…:l2vpn:0:65100:10` | leaf1 EVI 10、leaf3 VSI RED | 跨厂商靠共同 RT 65100:10010 关联；本地名不同，所以没有 vpn-name |
| L2NM | 20 / BLUE-20 | leaf2 / leaf3 | VNI 10020 和 10021 是两个二层段，只在 BLUE 中三层互通 |

### 叠加拓扑：VXLAN（RFC 8944）与 DC fabric（RFC 8542）

`model/fabric.py`。叠加网络追加在 L2/L3 网络之后，进入同一棵 SIMAP 树（`/rfc8345` 导出）：

- **VXLAN 网络**（`…:vxlan`，l2-topology，VXLAN feature）：
  - 节点：每个 VTEP 一个，支撑节点是 VTEP 地址所在实例的 L3 节点。
  - 终结点：设备显示的每个 VNI 一个，`encapsulation-type vxlan`、`vni-id`，支撑终结点是持有 VTEP 地址的 L3 TP（Loopback0）。
  - 链路：每个 VNI 一条，条件是一端显示另一端为该 VNI 的对端（IOS nve peers；Comware IMET，以及绑定到 VSI 的 VXLAN 隧道），并且另一端也有该 VNI。对端不在采集范围内（`vtep_outside_collection`）或对端没有该 VNI（`vni_not_on_peer`）时记缺口。底层路径不作支撑链路，因为隧道走哪条路由由底层决定。
  - 实验网结果：leaf1–leaf3 VNI 10010（两端都看到）、leaf2–leaf3 VNI 50020。
- **fabric 网络**（`…:fabric`，fabric-network）：
  - 成员：由已建立的 BGP EVPN 会话连起来、两端都已采集、且其中至少有一个 VTEP 的设备，构成一个 fabric 节点。
  - 角色：有 VTEP 的是 leaf，没有 VTEP 的 EVPN 路由反射器是 spine。border 不推断。
  - `device-links` 取成员之间的 L2 链路。`fabric-id` 取成员中最小的 BGP router ID。
  - 实验网结果：2 个 spine、3 个 leaf、6 条 spine–leaf 链路。MPLS 核心没有 VTEP，不构成 fabric。
- 链路状态和链路保留只处理物理 L2 网络（没有 supporting-network 的 l2-topology 网络）。叠加网络不当作端口和网线处理。

### 设备层：BGP 地址族、LDP（RFC 9070）、MPLS（RFC 8960）

`model/protocols.py`，写进 RFC 8349 路由树：

- **BGP**（draft-21）：设备显示的每个地址族汇总（IPv4、VPNv4、EVPN）都加进全局 `afi-safis` 和对应邻居的 `afi-safis`（IANA 标识 `ipv4-unicast`、`l3vpn-ipv4-unicast`、`l2vpn-evpn`）；该行已建立时 `active` 为 true。
  - 只跑 VPN 地址族的路由器（实验网全部 8 台的 IPv4 表都是空的）现在也有 BGP 实例。
  - 前缀数不导出：CLI 没有说明计数在入口策略之前还是之后，而模型区分这两者。
  - BGP 协议邻接覆盖全部地址族：6 个 leaf–spine EVPN 会话，以及 PE 之间的 VPNv4 会话。
- **LDP**：一个实例，名为 "ldp"（CLI 每台路由器只有一个 LDP 进程）。
  - 内容：LSR ID（所有会话行一致时）、按 LSR ID 加标签空间为键的对端及其会话状态，以及各发现接口上的 hello 邻接（IOS）。
  - RFC 9070 要求 LDP 接口在同一棵树中有 IPv4，所以复制接口时带上它观测到的 IPv4。
  - Comware 的 `display mpls ldp peer` 不显示发现源和本端 LSR ID，因此不填这两项。
- **MPLS**：只列出能证明启用了 MPLS 的接口：LDP 发现源，或 LFIB 中压入标签的出接口。LFIB 本身不导出：RFC 8960 把它放在 MPLS RIB 里，而 RIB 名称不可得，这与 M4 的结论相同。

## 校验

M1 正反例从 48 个增至 72 个，全部符合预期（`scripts/topology/m1_cases.py --build`，libyang 5.8.6）。

正例：以下 App 输出在 get、data 模式下通过；strict 模式只缺未采集的 RFC 8343 接口节点。

- `vpn-fabric-topology`：L2、L3、VXLAN、fabric 四个网络。
- `vpn-fabric-l3nm`、`vpn-fabric-l2nm`。
- `pe1-routing-default`：OSPF、BGP VPNv4、LDP、MPLS。
- `spine1-routing-default`：BGP EVPN。

反例（均为对 App 输出的单项修改）：

| 用例 | 分类 | 含义 |
|---|---|---|
| l3nm-route-target-cli-text | semantic/type | RT 写成 CLI 原文 `65100:1`，没有 RFC 8294 类型 |
| l3nm-access-profile-not-active | semantic/reference | 接入引用了节点没有激活的实例 profile |
| l2nm-auto-discovery-without-bgp-ad | semantic/when | 没有 `bgp-ad-enabled` 却出现 `bgp-auto-discovery` |
| l2nm-evpn-signaling-option | schema/unresolved-identity | **RFC 9291 模块缺陷**（见下） |
| vxlan-vni-out-of-range | semantic/type | VNI 只有 24 位 |
| fabric-device-outside-tree | semantic/reference | `device-ref` 是 instance-identifier，引用的设备节点必须存在 |
| ldp-interface-without-ipv4 | semantic/must | LDP 接口没有 IPv4 |
| ldp-session-state-cli-text | semantic/type | 会话状态写成 IOS 原文 `Oper` |
| bgp-afi-safi-cli-text | semantic/unknown-identity | 地址族写成 `evpn`，正确的 IANA 标识是 `l2vpn-evpn` |

**发现的模型缺陷（RFC 9291）**：

- 现象：evpn-bgp 信令分支里 `pbb` 容器的 when 条件写的是 `derived-from-or-self(../../evpn-type, 'pbb-evpn')`，身份名没有前缀。按 RFC 7950，它在本模块 ietf-l2vpn-ntw 中解析，但该身份定义在 vpn-common 中。
- 后果：这个分支的任何实例都无法通过校验。因此 IOS 显示的服务接口类型（"vlan-based"，即 `service-interface-type`）不导出。
- 证据：缺陷由反例 `l2nm-evpn-signaling-option` 固定下来。

**yangson**：

- App 冻结模块集（`resources/yang/library.json`）已加入 RFC 8542 的两个模块和 l2-topology 的 VXLAN feature；叠加网络在 yangson 下通过（`test_overlay_export_validates_against_the_frozen_modules`）。
- L3NM/L2NM 无法用 yangson 校验：它忽略 when 条件下的 mandatory 叶子，也编译不了 L3NM 的 leafref。这两个模型只用 libyang 校验（与 BGP 草案相同）。

## 策略路由：OpenConfig policy-forwarding（2026-10-06）

IETF 和 IEEE 都没有策略路由模型，负责人决定用 OpenConfig `openconfig-policy-forwarding`（[实施方案 §13](../../../docs/network-digital-twin-implementation.md)）。topology 0.11.0。

**模型来源**（`models/network-twin/openconfig`）：

- `sources.lock.json`：openconfig/public 提交 `8c3a6cf65ac8a21ad029d252352c6319745b06c2` 上 `openconfig-policy-forwarding` 的完整依赖闭包，99 个文件，每个记 URL 和 SHA-256。源码未改动，没有自造模块。
- 闭包引用的 IETF 模块（`ietf-interfaces`、`ietf-inet-types`、`ietf-yang-types`、`iana-if-type`）用 topology App 已钉住的版本（`src/apps/topology/resources/yang`），不另取一份。
- `library.json`：RFC 7895 模块库，由 `scripts/topology/build_openconfig_library.py` 生成；`--check` 先核对每个文件的 SHA-256，再核对模块库与文件一致。
- 编译：pyang 2.7.1 无错误；yangson 1.6.3 能加载。yangson 只在"实现"的模块里解析 leafref，而 network-instance 树里有指向 routing-policy、defined-sets、qos、keychain 的 leafref，所以闭包里的 OpenConfig 模块全部标为实现；IETF 模块只有 `iana-if-type` 标为实现（接口类型要用它的身份），其余只导入。

**厂商模块（H3C）**：按"缺 YANG 原始文件就去 GitHub 上对应的项目找"（负责人 2026-10-06），查过两处，都没有 H3C Comware 的 YANG：

- YangModels/yang（提交 `3dc40afb4ab93b41709d94e974375afefed935e4`）：`vendor/` 下只有 alliedtelesis、arrcus、ciena、cisco、fujitsu、huawei、juniper、nokia、readylinks；
- H3C 自己的 GitHub 组织：H3C/h3c_open.comware（`d728dd0`）、H3C/pycw7-ansible（`47de72c`）是 Ansible 集合，没有 `.yang` 或 `.xsd` 文件。

H3C 的文档说明 Comware 7 的 YANG 文件内置在设备软件里，用 `netconf-state/schemas` 列出、`get-schema` 读取。BR1 的 hello 通告了 `ietf-netconf-monitoring`（2010-10-04），以及定义用到的模块：PBR 2022-08-18、ACL 2021-11-09、NAT 2023-01-03、Ifmgr 2021-11-25、ARP 2019-12-07、Route 2019-12-17。因此 `h3c-comware7.pbr-rules`/`pbr-nexthops` 里的代码目前只以 BR1 样本为据（与同一时刻的 CLI 对照）：节点 Mode 1=permit、2=deny；ApplyIPDF 2 对应"没有 apply ip-df"；下一跳表只读 Mode 0、VrfIndex 0、NexthopDirect false 这一种形式，其余报 `lookup_unexpected`。平台的 NETCONF 会话目前只发 `get`/`get-config`，不能发 `get-schema`；取到 PBR 等模块后，按 OpenConfig 的方式钉住，再核对这些代码。

**宿主树**：policy-forwarding 只能挂在 `openconfig-network-instance` 下，接口绑定是指向 `/oc-if:interfaces/interface/name` 的 leafref。只生成最小宿主树：网络实例（名称、类型）、绑定的接口（名称和模块要求的类型）和 policy-forwarding。名称与 IETF 树一致（全局表在 OpenConfig 里叫 `DEFAULT`，类型 `DEFAULT_INSTANCE`；VRF 用 RFC 8529 的实例名，类型 `L3VRF`）。接口类型取自 RFC 8343 树，取不到时不造值，该绑定不导出并记缺口。

**校验**：`tests/unit/test_topology_pbr.py` 用钉住的模块校验 BR1 样本导出的树；把绑定的接口名改成不存在的名字时，yangson 报 `instance-required`（leafref 生效）。

**查证的建模问题**（来源：OpenConfig 模块 description、H3C 配置指导）：

| 问题 | 结论 |
|---|---|
| 按 ACL 匹配 | OpenConfig 规则直接匹配报文字段。节点的 ACL 展开成每条规则一个 rule（`sequence-id = 节点号 × 65536 + 序号`），节点内按 ACL 顺序，节点间按节点号；规则之间是"第一个命中生效"。H3C 文档对 ACL 中 deny 规则和时间段的处理有两种说法（多数版本"PBR 忽略 ACL 的 permit/deny 和时间段"，另一些版本"deny 规则或 ACL 不存在时节点不匹配"），因此含 deny 规则或时间段的 ACL 不展开，标 partial。 |
| deny 节点 | H3C：命中 deny 节点的报文按路由表转发，不再匹配后面的节点。OpenConfig：`forwarding-action FORWARD`（"Send the packet"），没有动作的规则在 OpenConfig 里是错误。permit 节点没有 apply 子句时同样处理。 |
| 下一跳不可达 | H3C：下一跳无效时按路由表转发（另有 default-next-hop 时先用它，样本中没有）。路径计算按此回退，并记 `pbr_next_hop_unresolved` 缺口。 |
| 本机策略 | OpenConfig policy-forwarding 只在网络实例的接口上应用，没有本机报文的位置。不导出，记 `local_policy_not_expressible` 缺口，策略名列在 metadata 的 `local_policies`。 |
| 执行顺序 | OpenConfig 说明策略在 QoS/ACL 之后执行，没写规则间的顺序；按 sequence-id 升序（与 Comware 节点号一致）。路径上为入向 ACL → 策略路由 → NAT → 查路由（负责人决定）。 |

## 未做与限制

- **服务分组**：不从 VRF 猜客户服务，也不生成 L3SM/L2SM。hub-and-spoke 会按导出 RT 分成两个服务，导入关系列在 relations 中。
- **RIB 与标签**：IP 路由已用推出的 RIB 名导出（2026-10-06，见 M4 README），隧道（MPLS VPN、VXLAN L3VNI）仍由 `overlay.prepare` 在树来的路由上标注；MPLS RIB / LFIB 仍不导出（标签栈与 LSP 下一跳尚未映射）。
- **未导出的字段**：BGP 前缀数；IOS EVI 的服务接口类型（受 RFC 9291 缺陷所限）；LDP 地址绑定和标签绑定。
- **IPv6**：没有样本。
- **其他厂商与规模**：NX-OS 等家族还没有 M5 的检查项实现；border leaf、多 fabric 互联没有样本。
- **SAP/AC、ACL/NAT/QoS、服务保障**（M5 的后续部分）未开始。
- **策略路由**：只有 Comware 采集内容（CLI 与 NETCONF），IOS 只有绑定；IPv6 策略、apply 的其他动作（出接口、default-next-hop、优先级等）不导出。

## 实机验收（2026-10-04，只读发现）

部署：规则治理 1.5.0（`bootstrap_version` 3，写入系统基础检查项）和 topology 0.7.0（声明依赖规则治理 ≥1.5.0）。`start.ps1 -UpgradeApps rule-governance,topology` 退出 0，9 个 App 全部启用，日志在 `.runtime/topology-m5-deploy.log`。

发现：平台新建网络"vpn-fabric 实验网"（`63c0b8d8…`）。种子是 8 台实验设备，深度 0，只发 show/display 命令，使用平台保存的默认登录。核对脚本是 `.runtime/m5-live.py`，证据在 `.runtime/m5-live-*.json`。

- **第一次**（run `3524b812…`，快照 `9e148d2f…`）：8 台全部成功。
  - 主机距上次通信已有数小时，PE 上客户主机的 ARP 已老化，MPLS 路径的终点判为 `endpoint_unresolved`。这与设备显示一致：没有 ARP 就不能判为交付。
  - 证据在 `m5-live-proof-1.json`。
- **在实验网主机上开启持续 ping 后的第二次**（`lab.py traffic`，只在 VPCS 上 ping；run `b72b0323…`，快照 `e0ad3469…`）：8 台全部成功，17 项核对中 15 项通过。

| 核对 | 结果 |
|---|---|
| M5 能力在每台设备上都执行，经规则治理模板完整解析 | 通过。PE2 没有 Tunnel 接口，Comware 拒绝 `display interface tunnel`（"Wrong parameter"），判为不支持，与夹具一致 |
| L3NM：CUST-A（PE1、PE2，ldp）、CUST-B、SHARED、BLUE（LEAF2、LEAF3，vxlan-trans） | 通过；SHARED 与 CUST-A 互为 relation |
| L2NM：RED 跨厂商连接 LEAF1 和 LEAF3；VNI 10020 和 10021 分开 | 通过，三个服务都是 `bgp-ad-enabled` |
| fabric：SPINE1/2 为 spine，LEAF1–3 为 leaf，6 条链路；无叠加缺口 | 通过 |
| BGP 地址族：fabric 是 EVPN，PE 是 VPNv4；6 个 EVPN 会话和 PE1–PE2 VPNv4 会话都已建立 | 通过 |
| LDP：P 与两台 PE 都是 operational；MPLS 接口为 PE1 e0/1、P e0/1–2、PE2 GE2/0 | 通过 |
| libyang 运行态校验：SIMAP 导出、L3NM、L2NM、8 棵路由树 | 通过，全部无错 |
| A1→A2：PE1[CUST-A]〈mpls〉→ P → PE2[CUST-A]；A1→S2 泄漏到 SHARED | 通过，往返都 complete |
| B1→S2：CUST-B 无路由；回程到达 CUST-A 的 10.1.1.10，判为 other_instance | 通过 |
| VXLAN：LEAF1–LEAF3 VNI 10010、**LEAF2–LEAF3 VNI 50020** | **未通过**：只有 VNI 10010 |
| H-B2→H-B3 经 L3VNI 50020 | **未通过**：LEAF2[BLUE] no_route |

**两项未通过的原因在实验网本身，孪生如实反映了设备状态。**

- 现象：实验网重启后（设备运行时间约 7.5 小时），两个 leaf 都不再把对方的 BLUE 路由导入 VRF。
  - LEAF2 收到了 LEAF3 的 type-5 路由 192.168.20.0/24，属性齐全（RT 65100:50020、VXLAN 封装、Router MAC、VNI 50020），但没有导入 VRF BLUE；路由表中只有本地网段，`show nve peers` 为空。
  - LEAF3 也没有 LEAF2 的 192.168.10.0/24。夹具采集时存在的到 LEAF2 的自动 VXLAN 隧道（Tunnel1）现在也没有了。
- 已尝试的恢复，均无效：
  - 对 LEAF2 做 BGP EVPN 的软刷新和硬复位；
  - 原样重新下发 LEAF2 的 stitching 导入 RT（配置净变化为零）；
  - 从控制台重启 LEAF2，不保存配置。LEAF2 的运行配置与仓库中的 `configs/LEAF2.cfg` 一致。
- 下一步排查：在 LEAF2 上开启 `debug bgp l2vpn evpn` 观察导入过程，或重启 LEAF3。
- 孪生的输出：BLUE 中没有路由（no_route），VNI 50020 没有链路。这与设备显示的一致。
- 正向场景的覆盖：L3VNI 对称 IRB 路径由同一实验网正常时采集的夹具覆盖（`test_symmetric_irb_crosses_the_l3_vni_over_both_spines`）。实验网恢复后再复测，复测只需重跑 `m5-live.py submit/poll/verify`。

验收期间没有修改实验网配置（重新下发的那条导入 RT 与原配置完全相同，重启 LEAF2 时没有保存）。主机上的持续 ping 仍在运行，用于保持 ARP/MAC/EVPN 表；需要停止时执行 `lab.py traffic --stop`。
