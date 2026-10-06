# M4：三层协议实例、协议邻接与路径推导

按[实施方案 §8](../../../docs/network-digital-twin-implementation.md) 的 M4 执行，topology App 0.6.0。原则同 M1—M3：只输出标准节点；设备没有给出的列表键不构造；协议实例名取设备自己的标识。

## 采集

| 能力 | IOS | Comware 7 | 解析 |
|---|---|---|---|
| OSPF 邻居 | `show ip ospf neighbor` | `display ospf peer` | `generic_ospf_neighbors`：邻居 router ID、地址、优先级、状态、角色、dead time、接口；Comware 另有进程和区域。状态统一为 RFC 9129 `nbr-state-type`；点到点链路上的 `FULL/  -` 记为 full，角色为 `-` |
| BGP 邻居 | `show ip bgp summary` | `display bgp peer ipv4` | `generic_bgp_peers`：本地 router ID 和 AS，加上每个邻居的地址、AS、状态、建立时长、收到的前缀数。State/PfxRcd 列是数字时表示 established；`% BGP not active` 是有效的空表 |
| 按 VRF 的路由表 | `show ip route vrf <名>` | — | 与按 VRF 的 ARP 采集机制相同：先授权基础命令，再针对发现的每个 VRF 授权带作用域的命令。输出必须带 `Routing Table: <名>`，并且只能是该 VRF，否则拒收；路由行和下一跳都记在该 VRF 下 |

真实样本：`tests/fixtures/topology/campus-wan/protocols`（8 台设备的 OSPF/BGP 邻居）和 `vrf-routes`（4 台 IOS 路由器的 MGMT 路由表）。两组样本都经过导入脚本的密文扫描，命中 0。

## 协议实例（RFC 8349 `control-plane-protocol`）

| 协议 | 标准 | 实例名 | 输出内容 |
|---|---|---|---|
| OSPFv2 | RFC 9129 | 设备显示的进程号 | `router-id`；区域；可用接口（`cost`）；各接口上的邻居（`neighbor-router-id`、`address`、`state`） |
| BGP | draft-ietf-idr-bgp-model-21 | 本地 AS（CLI 用 AS 标识唯一的 BGP 进程） | `global/as`、`identifier`；邻居键 `neighbor-key`（取 `remote-address`）、`peer-as`、`peer-type`（对端 AS 等于本地 AS 时为 internal）、`session-state` |

- OSPF 实例只有在该进程的全部可用接口都属于同一个 NI 时才导出，并且写进这个 NI 的路由树。实例引用的接口会复制到同一棵树里；引用不到的接口去掉，记 `ospf_interface_reference_not_proven`。
- 未导出的内容：定时器、LSDB、统计、DR 选举视图（邻居角色）、`dead-timer` 倒计时快照、BGP `last-established`（需要用观测时刻推算）、按 afi-safi 的收到前缀数。字段映射见 [M1 fact-mapping](../m1/fact-mapping.json)：OSPF/BGP 共 28 个字段，其中 15 个已导出，5 个可映射但未导出，2 个没有原生节点，6 个作为作用域。
- RIB 导出（2026-10-06，topology 0.13.0）：RFC 8349 的 `ribs/rib[name]` 需要 RIB 名称，设备不提供。负责人决定由“实例 + 地址族”推出：公网 `ipv4`/`ipv6`，VPN 实例 `<实例>:ipv4`，并在每个实例分段的 metadata `derived_rib_names` 写明（`reason: the device prints no RIB name`）。做法同 ACL 端口集名、`<ACL>@<实例>` 和 PBR 的 sequence-id。设备打印了 RIB 名时用设备的。
  - 路由协议用冻结模块的 identity：直连 `ietf-routing:direct`、静态 `ietf-routing:static`、OSPF `ietf-ospf:ospf`、BGP `ietf-bgp:bgp`（draft-ietf-idr-bgp-model，M1 锁定版本）等。不导出 OSPF metric：RFC 9129 只在 source-protocol 派生自具体 OSPF 版本时允许 metric，CLI 不给版本，不猜。
  - 下一跳：Null 接口为 `special-next-hop blackhole`，InLoopBack 为 `receive`（下一跳在本实例时）。下一跳在别的实例（泄漏路由、VPN 经底层）：树里只写地址，metadata `next_hops_outside_instance` 写明实例和接口。设备打印的出接口在本实例的接口树里证明不了（接口没采到、重复行、归属不明）：树里写地址，metadata `next_hop_interfaces_unproven` 写明接口名，路径仍从这个接口出去，状态记未知。
  - 整条导不出的路由（协议没有映射、没有下一跳、下一跳既无地址又证明不了接口等）：记缺口，查询表按前缀列在 `missing` 下。
  - 查询表 `model/route_table.py` 只从树和缺口取；路径（`model/path.py`）不再读路由事实。查找时若有导不出的路由可能更长匹配，跳上记 `route_not_in_tree`；查不到路由而有这样的路由时，以 `route_not_in_tree` 结束（不当作没有路由）。
  - 校验：BGP 路由的 source-protocol 来自 BGP 草案模块，yangson 加载不了该草案的闭包，单测里 yangson 校验去掉 BGP 路由；含 BGP 路由的树另用 libyang 校验。2026-10-06 在云端会话做了一次：libyang 2.1.30（Ubuntu `libyang2-tools`），模块为 App 设备库的冻结文件加 `ietf-bgp@2026-08-14` 的导入闭包（31 个文件）。闭包中 App 库没有的模块取自 YangModels `0e28ed8`（M1 锁定的提交）；其中 BGP 草案各模块的字节与 M1 锁文件里从草案正文抽取的版本不同（云端访问不了 ietf.org，未能按锁文件取原文），同名同 revision。仅测试用的补充：接口 `statistics/discontinuity-time` 填固定值；一个偏差模块去掉已废弃的 `routing-state`、`interfaces-state`（NMDA 服务器不实现；libyang 2.1 否则按其 min-elements 报错）。结果：campus-wan 6 台路由器的路由树（共 22 条 BGP 路由，含 BGP 协议实例）按完整数据存储（`-t data`）校验全部通过；反例（不存在的 identity、基 identity `routing-protocol`、悬空的出接口）均被拒绝。合并后在本地用 M1 镜像（libyang 5.8.6、锁定字节）复验。
- 校验：BR1/DIST1 的路由树（OSPF + BGP + 引用的接口）在 libyang 5.8.6 的 get/data 模式下通过，strict 模式只缺未采集的接口节点（admin-status、if-index、统计）。OSPF 部分也能用 App 冻结的 RFC 9129 模块通过 yangson 校验；yangson 加载不了 BGP 草案，BGP 只用 libyang 校验。新增 3 个反例，分类都符合预期：OSPF 接口引用了树外的接口（semantic/reference），邻居状态写成 CLI 原文 `FULL/DR`（semantic/type），peer-type 写成 `ibgp`（semantic/type）。M1 正反例增至 48 个（两棵路由树各 3 种模式，加 3 个反例），全部符合预期；App 输出的标准路径从 102 增至 133。

## 协议邻接（单独分析，不并入 L2/L3 图）

`model/protocols.py` 的 `adjacencies()` 结果随快照保存在 `protocols.adjacencies`：

- **OSPF**：按邻居地址找到拥有该地址的已采集设备，再和对端自己看到的邻居对照。结论有 full、one_sided（只有一端看到）、not_full、neighbor_outside_collection、router_id_ambiguous。Router ID 只在 OSPF 域内有意义，不作为设备身份；两台设备声明同一个 RID 时报歧义，不覆盖。
- **BGP**：按对等地址找设备，每个会话只配对一次。结论有 established、one_sided、not_established、peer_outside_collection、peer_ambiguous。

园区样本的结果：10 个 OSPF 邻接两端都是 full；9 个 iBGP 会话（两台核心做路由反射器）全部 established；BR1/BR2 的 ISP 对端标为 peer_outside_collection（external）。

## 路径推导验收

方案列出的场景对应如下（`tests/unit/test_topology_path*.py`、`test_topology_campus_wan.py`）：

| 场景 | 用例与结果 |
|---|---|
| 同前缀隔离 | 真实 MGMT 路由表：DIST1 的 10.10.1.0/24 只在 MGMT 中。全局查找走 BGP 默认路由到 BR1（该前缀在 BR1 上确实属于全局表），BR1 没有 ARP 证据时判为终点未解析；CORE1 的 MGMT 表没有到用户网段的回程路由（no_route）。只有同前缀、没有邻居证据时，结论只是候选 |
| 不同 NI 名互联 | 一端在 blue、另一端在 green：入口用源接口所在的 NI，到对端后用对端接口所在的 NI |
| 重复地址 | 同一地址出现在多台设备或同一设备的两个 NI 时，保留全部候选，不取第一个；可以用 LLDP 对端口从重复的下一跳中选出唯一一个 |
| /32 loopback 递归 | 真实 iBGP：DIST1 的默认路由下一跳是 BR1 的 loopback 10.255.0.1，经 OSPF /32 递归后通过两台核心 |
| 跨 VRF 泄漏 | 新增：red 中的路由下一跳在 blue，跳点记录 `next_hop_vrf`，在 blue 中解析后到达对端；回程由 blue 引入 red 的 Direct /32（InLoop0）交付到 red 中的 loopback。去掉泄漏路由后，回程在 blue 中 no_route |
| ECMP 单支失败 | 一条等价分支的出接口不转发时，只结束这一支并附证据，另一支照常交付 |
| 无 ND/ARP | 直连路由没有终点 ARP 时不能判为交付；另一个 NI 中的 ARP 不算数 |
| 邻居有而链路状态未知 | 有 ARP 邻居但出入接口状态未知或有歧义时，保留候选但不能判为 complete；完全没有观测到的接口不会默认为 up |
| 故障/恢复 | 已有的快照状态用例：当前采集失败时不用旧的 up 状态填补；离线后恢复，16 条设备连线保持 |
| 非对称路径 | 新增：去程 R1→R2→R4，回程 R4→R3→R1，两个方向都完整，`symmetric=false`。候选首跳不同或某个方向不完整时，不下对称性结论 |

## 实机验收（2026-10-03，只读）

部署 topology 0.6.0 后对 campus-wan 的 8 台设备做了一次发现（run `9999883b…`，快照 `e20add00…`），8 台全部成功，10 项核对全部通过（`.runtime/m4-live-proof.json`）：

- 8 台设备都执行了 OSPF/BGP 邻居命令，用平台解析器解析现场原文全部完整：BR 各有 3 个 OSPF 邻居和 3 个 BGP 邻居，CORE 各 5 个，DIST 各 2 个，ACC 为有效空表；
- 6 台 IOS 都执行了 `show ip route vrf MGMT`，输出只含 MGMT（10.10.1.0/24 和本机 /32）；BR 没有按 VRF 的采集；
- 6 台路由器的默认路由树都有 OSPF 进程 1（router ID 10.255.0.1–6）和 BGP AS 65000（identifier 与 router ID 相同）；ACC 没有协议实例；
- 10 个 OSPF 邻接两端都是 full，9 个 iBGP 会话为 established，两个 ISP 对端为 peer_outside_collection，与实验网设计一致；
- 6 棵路由树在 libyang 运行态校验中全部通过；
- 路径：CORE1 → DIST1 的管理地址在 MGMT 中查找（10.10.1.0/24，出口 Ethernet0/0），不使用全局表。现场 MGMT ARP 中没有对端条目，所以结论是终点未解析，没有判为交付。路径核对只用本次快照的观测，源地址和目的地址都在 MGMT 中、同为 IPv4，没有用其他源的 ping 作证明。

## 未完成

- 没有 IPv6/ND、unnumbered、BFD、IS-IS、静态路由配置实例的样本，这些不导出。
- Comware 的按 VRF 路由没有样本（实验网 VSR 不使用 VPN 实例），按 VRF 路由采集只对 IOS 启用。
- RIB 名称由“实例 + 地址族”推出（见上）；LSDB、TE 邻接没有采集。
- 实验室的路径核对只用平台自己的快照观测，不拿其他源地址的 ping 结果作证明。
