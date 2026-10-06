# M2/M3：设备实例、资源清单与园区二层

按[实施方案 §8](../../../docs/network-digital-twin-implementation.md) 的 M2、M3 执行，topology App 0.5.0。原则与 M1 相同：只输出标准节点；设备没有给出的列表键、序列号、版本号等不构造，缺口逐项记录。

## M2 设备实例与资源清单

| 内容 | 标准与路径 | 结果 |
|---|---|---|
| 接口关断状态 | RFC 8343 `interface/enabled` | Comware 表头图例 `ADM`、IOS `admin down` 表示关断，其余状态即为启用。`admin-status` 需要 if-mib 特性，开启后 `if-index` 也变成必填，所以不用 |
| 接口描述 | RFC 8343 `interface/description` | 原样输出 |
| VRF | RFC 8529 `network-instances/network-instance[name]`，接口 `bind-ni-name` | 接口的全部地址都在同一个命名 VRF 时绑定。全局表（事实中记为 `default`）不建 NI。`vrf-root` 内的数据（VRF 路由）没有采集；空容器不能满足必选的 root-type，因此不输出 `vrf-root`，记缺口 `network_instance_root_not_observed` |
| 网元 | IVY -20 与软件扩展 -04：`network-element` | 每台设备一个网元。`ne-id` 沿用设备的稳定拓扑身份（方案 §4.1 标识策略）；`name`、`mfg-name`、`product-name`、`software-rev`（运行映像及版本）取自版本输出 |
| 网元类型 | IVY `ne-type`（默认 `ne-physical`） | 只按证据给出：Comware 的 vCPU 与 virtio 网卡判为 `ne-virtual`（运行在虚拟机内）；IOS 的 `Linux Unix (...) processor`（IOL）判为 `ne-software`（直接运行在宿主系统上）。没有证据时不输出网元，记 `ne_type_not_proven`，避免被默认值当成物理设备 |
| 序列号 | IVY `components/component/serial-number` | 不输出。它属于机框组件，CLI 不给 component-id；IOL 自己也打印 "Processor board ID"，序列号不能证明设备是物理的 |

App 的冻结 YANG 新增 RFC 8529、RFC 8528、IVY -20、软件扩展 -04 和 iana-hardware，来源与摘要取自 M1 来源锁。新增 `inventory-library.json`；IVY 网元在 yangson 中完整校验通过，在 libyang 的严格模式下也通过。

**离线验收（实验网没有物理、被动、许可、位置、能耗事实）**：`scripts/topology/m2_examples.py` 从固定草案原文中抽出全部 JSON 示例（去掉分页并按 RFC 8792 展开折行），再用 libyang 按固定模块组合做运行态校验，结果在 `examples.json`：

| 草案 | 示例 | 结果 |
|---|---|---|
| IVY 基础 -20 | 3 | 全部通过（端口、多机框、非模块化网元） |
| IVY 许可 -05 | 8 | 7 个通过；1 个依赖草案中示意用的 `example-*` 模块，无法装载 |
| IVY 软件 -04 | 1 | **草案示例缺陷**：时间叶子写成 JSON `null`（RFC 7951 只允许 empty 类型用 null）；组件层的 patch 没有软件扩展节点 |
| IVY 位置 -06 | 2 | **草案示例缺陷**：模块把 `locations` 增广在 `/nwi:network-inventory` 下，示例却放在顶层 |
| IVY 被动资源 -01、GREEN -04 | 0 | 原文没有 JSON 示例 |

## M3 园区二层

- **采集**：IOS 拓扑模式新增 `show spanning-tree`（`generic_stp`），按实例逐端口记录角色和状态，统一为 IEEE 802.1Q 术语。LAG 记录各成员的原始标志（P/s/D/I/H/w）。
- **为什么不导出为 YANG**：实验网运行 rapid-PVST，每个 VLAN 一棵树。IEEE 802.1Q YANG 只有每网桥（RSTP）或每 MSTI 的端口状态，RFC 8944 没有 STP 节点，因此逐 VLAN 的状态只作为证据。设备级的 PVID 和 VLAN 成员依赖 IEEE `bridges/bridge[name]`，CLI 不给桥名，MAC 表还缺 database-id 和桥端口号，这些都保持 M1 中“键不可得”的结论。网络级 L2 视图仍然是 RFC 8944（trunk 标签、LAG 成员）。
- **逐 VLAN 转发分析**（`model/l2_forwarding.py`，随快照保存为 `l2_forwarding`）：链路 Up 不等于 VLAN 可转发。对每条两端都是受管交换口的 L2 链路，逐个 VLAN 判断：
  - 放行：access VLAN，或 trunk 允许且处于活动状态的 VLAN；
  - 打标一致：一端是 native VLAN 而另一端带标签，判为不一致；
  - 生成树转发状态：取逐 VLAN 状态，没有时取 trunk 表的“转发且未修剪”列表；
  - 聚合成员：按所属聚合口判断，成员本身还必须在束内。

  结论分为转发、阻塞（哪一端、什么角色）、不放行、打标不一致、成员不在束内、未知（缺哪项事实）。链路问题包括 native VLAN 不一致、允许列表没有交集、access 口对 trunk 口。每个 VLAN 再汇总为桥域。

**验收**：`tests/unit/test_topology_l2_forwarding.py`、`test_topology_inventory.py`，加上 device_ip/device_state 的更新用例。方案列出的场景对应如下：

| 场景 | 证据 |
|---|---|
| 双上联、阻塞口 | 真实数据：ACC1/ACC2 两条上联各转发一个 VLAN、阻塞另一个（Alternate），与 DIST1 是 VLAN 10 根、DIST2 是 VLAN 20 根一致 |
| 聚合成员变化 | 真实 Port-channel1 的两条成员链路按聚合口判为转发；把一个成员移出束后，该链路为“成员不在束内”，另一条仍转发 |
| native 不一致、允许列表无交集、access 对 trunk | 对真实数据做单项修改，分别报出对应问题和逐 VLAN 结论 |
| 同 MAC 跨 VID、共享口不同 VLAN 端点 | 已有用例（`test_topology_identity_scope.py` 和园区 UNM-SW 后的 PC4/PC5），不跨 VLAN 合并 |
| 缺生成树事实 | 删除 STP 与转发列表后判为“未知（stp_state）”，不当作转发 |

## 实机验收（2026-10-03，只读）

部署 topology 0.5.0 后，对 campus-wan 的 8 台设备做了一次发现，全部成功，11 项核对通过（`.runtime/m2m3-live-proof.json`）：

- BR1/BR2 为 `ne-virtual`，6 台 IOS 为 `ne-software`，没有任何序列号；
- 6 台 IOS 的 OOB 口绑定 NI `MGMT`，VSR 没有 NI；
- 关断口均为 `enabled=false`（CORE 2、DIST 9、ACC 11），描述保留；
- 4 条接入上联逐 VLAN 的转发/阻塞与生成树根的配置一致；DIST 间两条成员链路两个 VLAN 都转发；VLAN 10 和 20 各形成一个覆盖 4 台交换机的桥域；没有标签问题；
- 8 棵设备树和 8 个网元树在 libyang 运行态校验下全部通过；
- 两台核心路由器返回 "No spanning tree instance exists."，解析为完整的空结果。

## 未完成与边界

- 物理网元、堆叠，以及端口与组件的对应：实验网全是虚拟设备，没有样本，没有加判定规则，也没有实机结论。
- VRF 路由（NI 的 `vrf-root` 内容）、原生 RIB 名称（2026-10-06 起路由用推出的 RIB 名导出，见 M4 README）、LLDP 表键、FDB 键、VRRP 版本：设备没有给出，仍是缺口。
- H3C 生成树：实验网的 H3C 是路由器，没有交换样本，没有写解析器。MSTP/RSTP 的 IEEE 原生导出等到有这类样本时再做。QinQ 没有样本。
- 界面上还没有展示逐 VLAN 转发结果，目前只能从快照数据（API）读取。
