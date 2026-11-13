# 网络数字孪生方案（修订版）

- 日期：2026-11-11，负责人当天确认了第 2 节的方向。
- 读者：负责实现的会话（Opus 5.5）和项目负责人。
- 与旧文档的关系：旧的路线图、实施方案、交接文档在 2026-10-24 的清理中移出了仓库，都能从提交 `62e064e` 读到，例如 `git show 62e064e:docs/network-digital-twin-implementation.md`。冲突时以本文为准；本文没写的细节（各标准模型的用法、已有模块的设计）仍以旧文档为准。
- 进度只记在本文第 8 节。

## 1. 目标与衡量

目标：AI 能回答"从 A 到 B 的报文经过哪些设备、哪些接口、为什么走这条路"，并给出证据；走不通时指出断在哪台设备、因为什么。

每个工作包做完都用评测集（第 5 节 D）报两个分数：
1. **孪生层**：路径工具的结论是否与预期一致；
2. **AI 层**：把用户的原话交给助手，它定位的设备、原因、引用的证据是否正确。

## 2. 本轮方向（负责人 2026-11-11）

1. **只建模影响转发路径的东西。** 接口和状态、地址、ARP、二层（VLAN、聚合、MAC、子接口封装）、VPN 实例、路由表、策略路由、**ACL**（丢包）、**NAT**（改地址，决定回程怎么回），以及路由协议邻接（作为路由来源的证据）。
2. **NETCONF 优先。** 先做 NETCONF 采集和 NETCONF 数据到标准树的映射，NAT 模型同期做，然后让路径计算接入标准树。
3. **往后放的：**
   - CLI 的 TextFSM 模板；
   - 防火墙安全策略模型（按区域的 security-policy）。它的影响由人或助手登录设备查看，不进孪生的路径计算。
4. **查询表可以带标准树放不下的事实。** 标准树继续作为命名依据和导出格式，查询表里放不下的部分放在 `non_standard` 下并写明来源，不再因为标准树放不下就把结论降为"判断不了"。

明确不做：控制面仿真（"断一条链路后流量怎么走"）；M6 的 TE、SR、光、多播等领域。

ACL、NAT、策略路由的路径计算已经有了：ACL 和策略路由读标准树，NAT 读事实。本轮要让 NAT 也走标准树，并且三者都接上 NETCONF 来源。

## 3. 不变的原则

- 路径计算是确定性程序，不经过大模型；每一跳带证据；判断不了就说判断不了，不猜。
- 厂商差异写成数据（NETCONF 映射 YAML、家族特征），拓扑代码里不出现厂商或系统家族的名字（有守护测试 `test_topology_vendor_neutral.py`）。
- 能用 IETF/IEEE 模型命名的就用它的节点名。草案可以固定版本采用（已有先例：SIMAP -03、BGP -21、IVY -20）。没有标准时用公开模型（PBR 用 OpenConfig）。不新增项目自定义 YANG。
- 设备返回标准模块时，只做 XML 到 RFC 7951 的转换和校验；返回厂商私有模块时，按厂商写映射。以设备实际宣告的模块为准（hello 能力列表或 `ietf-yang-library`）。
- 每个映射、每种输出格式都要有真实设备的回包作样本；没有样本的一律不接受，不"顺手兼容"。
- 文档和汇报用中文，代码注释和提交信息用英文。只说事实，测试没过就照实写。

## 4. 数据流水线

```
设备 NETCONF 回包（证据，原文保存）
  → 转换层：netconf-api 的映射（mappings/<厂商>.yaml）→ 按标准叶子命名的记录
  → 拓扑：标准树（RFC 7951 JSON；放不下的写进 gaps，但不丢）
  → 查询表 = 标准树内容（字段用标准节点名）+ non_standard（带来源）
  → 路径计算读查询表
```

现有代码（读之前先看这些）：
- `src/apps/netconf_api/backend/standard.py`：转换层引擎，表名就是标准列表名（`interface`、`ipv4-address`、`ipv4-neighbor`、`route`、`ospf-*`、`lldp-neighbor`、`acl-*`、`pbr-*`）。
- `src/apps/netconf_api/backend/mappings/h3c_comware7.yaml`：唯一已有的厂商映射，照它的写法做华为的。
- `src/apps/topology/backend/model/standard_trees.py`：从标准记录建 RFC 8343/8344/8349/9129、IEEE 802.1AB 等树。
- `src/apps/topology/backend/model/standard_tables.py`：从标准记录给拓扑构建器出行。
- `src/apps/topology/backend/collectors/netconf.py`：NETCONF 优先、CLI 兜底的采集逻辑，有 prefer/off/compare 三种模式。
- `src/apps/topology/backend/model/path.py` 第 150 行附近：对有标准记录的设备，路由从 RIB 树取，接口、地址、ARP、LLDP 从标准记录取。

查询表里 `non_standard` 的约定：
- 每个条目写明内容、来自哪条记录、为什么标准树放不下（一句话）。
- 路径结论用到了 `non_standard` 时，在该跳的结果里标出来。
- 已有的"缺口占位"机制保留，但只用于设备回包本身读不懂的情况。

## 5. 工作包

按顺序做。每个做完：更新第 8 节，报第 1 节的两个分数（评测集建好之后）。

### A. 在实验网设备上开 NETCONF，摸清它宣告了什么

**已完成（2026-11-11）。** 详细结论在本机 `.runtime/ensp/netconf/结论.md`，回包、能力列表、YANG 原文都在同一目录。要点：
- 三台都已开 NETCONF（830 端口）。AR、USG 是逐行生效的单阶段配置，CE 要 `commit`。
- **IETF 数据模型一个都没有**，只有 NETCONF 自身的模块。OpenConfig 只有一部分，且三台都没有 network-instance 和路由模块。路由、ARP、VPN 实例只能取华为私有模块，所以工作包 B 全部按华为私有模块写映射。
- 来源已确认：
  - `huawei-ifm` 一处给出接口、子接口的父接口和 dot1q VLAN、VPN 实例、地址、聚合成员及其 LACP 状态、LLDP 邻居、所在安全域；
  - `huawei-network-instance` 下有路由表（含 `active`，未生效的路由也列出）和 OSPF；
  - `huawei-arp` 的 `query-entries` 带 VPN 实例；
  - `huawei-nat-policy` 的规则按匹配顺序，带区域条件；
  - `huawei-security-zone` 给出区域编号和成员接口。
- 华为的 NAT 和安全域模块作为映射的来源，不作为孪生的模型：输出仍按 RFC 8512 命名，区域条件和区域成员放进 `non_standard`。

原来的步骤说明保留如下，供核对。


1. 三台设备开 NETCONF over SSH（830 端口）。
   - 命令以设备的 `?` 帮助为准，大致是：`snetconf server enable`、`netconf` 视图下的 `protocol inbound ssh port 830`，SSH 用户的服务类型加上 snetconf。
   - YunShan 是两阶段配置，CE 要 `commit`。USG 是逐行生效的单阶段配置（2026-11-10 实测，提示符没有 `~`/`*`）。
   - SSH 目前只在 MEth0/0/0（管理口，`_management_vpn_`）上监听，830 端口也要从这个口可达。
   - 下发脚本 `.runtime/ensp/ensp_config.py` 能自动区分一阶段和两阶段。每台设备的命令写成 `.runtime/ensp/<设备>-netconf.cfg`，下发后保存一份回显。
2. 用平台的只读 NETCONF 会话（`src/platform/device_access/netconf.py`）拿 hello 和 `ietf-yang-library`，列出三台设备各自宣告的模块。要看清三件事：
   - 有没有标准模块：`ietf-interfaces`、`ietf-ip`、`ietf-routing`、`ietf-ospf`、`ieee802-dot1q-bridge`、`ieee802-dot1ab-lldp`、`ietf-network-instance`、`openconfig-*`；
   - 如果有，`<get>` 回来的数据是否完整，例如路由表有没有 OSPF 路由、ARP 有没有动态表项；
   - 华为私有模块的名字，如 `huawei-ifm`、`huawei-ip`、`huawei-arp`、`huawei-routing`、`huawei-ethernet`、`huawei-vlan`、`huawei-lacp`、`huawei-lldp`。
3. YANG 原文：先从 GitHub 的 Huawei/yang 找对应版本，找不到再用 `get-schema`（RFC 6022）从设备取，记下设备型号和软件版本。按 `src/apps/topology/resources/yang/sources.json` 的格式登记 URL 和 SHA-256。

完成标准：在 `.runtime/ensp/netconf/` 下，每台设备有能力列表、模块清单、每个转发相关模块的一份 `<get>` 回包，并有一页结论说明每张转发表用哪个模块、标准的还是私有的。

### B. 华为 NETCONF 到标准记录的映射

**已完成（2026-11-11，netconf-api 0.12.0）。**

- **映射文件**：`src/apps/netconf_api/backend/mappings/huawei_yunshan.yaml`，共 20 张表：
  - 接口（带 VPN 实例、父接口、dot1q VLAN、所属聚合、安全域）、地址、ARP、路由（只取生效的）；
  - OSPF 实例、区域、邻居；LLDP 邻居及管理地址；
  - 聚合与成员、二层端口模式、VLAN 与成员、MAC 表；
  - 安全域；NAT 策略规则及其源地址、目的地址。
- **转换引擎**（`backend/standard.py`）新增四项，H3C 映射不受影响：
  - 多命名空间：`namespaces` 加上 `prefix:name` 写法，无前缀的步沿用父节点的命名空间；
  - 上溯：`../` 读祖先节点，生成过滤条件时自动带上祖先的叶子；
  - `list: true`：读 leaf-list 的全部值；
  - `exists: <值>`：空元素存在即取该值；
  - 另有一个转换 `address-hex`。
- **核对**：三台设备实读，没有报告任何问题。路由与同期 CLI 逐条一致（USG 20、AR 14、CE 9）；接口、ARP、LLDP、聚合、VLAN 与 CLI 对照一致。
- **测试**：`tests/unit/test_netconf_huawei.py` 15 个，夹具在 `tests/fixtures/netconf/huawei-yunshan/`（三台的回包，以及同期 CLI 路由表）。
- **NAT**：只映射了实测见过的动作，即源 NAT easy-ip（另含 no-nat、地址池名两个结构字段）。目的 NAT、服务条件没有映射：遇到时记录里没有动作，或在 `unread` 里列出，路径计算按"判断不了"处理。
- **留给工作包 C 的三件事**：
  1. 平台按设备厂商挑映射，厂商取自规则治理已发布的 CLI 实现，华为目前没有，所以华为映射还选不上。要让 NETCONF 采集不依赖 CLI 实现也能确定厂商。
  2. 拓扑的接口树还没有用记录里的 `network-instance` 设置 `bind-ni-name`，管理口会被当作公网。
  3. LLDP 树要求的 `dest-mac-address`、`time-mark` 华为不给。按第 4 节的约定推出并写明（华为默认是 nearest-bridge 代理），或者放进 `non_standard`。

原来的步骤说明保留如下。


- 设备宣告了标准模块且数据完整的：不写映射，只做 XML 到 RFC 7951 的转换和校验。看 `standard.py` 是否已经支持"直通"；不支持就加上，并用设备回包做测试。
- 私有模块：写 `src/apps/netconf_api/backend/mappings/huawei_yunshan.yaml`。按转发路径需要的顺序：
  1. `interface`：名称、启用、运行状态、类型；子接口要带父接口和 dot1q VLAN（USG 的 `Eth-Trunk1.10`）；
  2. `ipv4-address`：注意管理口在 `_management_vpn_`，记录要带 VPN 实例；
  3. `ipv4-neighbor`：ARP，带 VPN 实例；
  4. `route`：公网和各 VPN 实例，每个下一跳一行；协议代码逐个对照 CLI 的 `display ip routing-table` 确认；
  5. `lldp-neighbor`；
  6. 二层：VLAN 与端口成员、端口模式和 PVID、聚合与成员状态、MAC 表。转换层目前没有这些表，表名按对应 IEEE 模型的列表名起（802.1Q bridge 的 VLAN 和 FDB、802.1AX 的聚合）。标准树放不下的（如 M3 记录过的"桥名或键缺证据"）按第 4 节放进 `non_standard`；
  7. `ospf-*`：邻居作为 OSPF 路由的证据；
  8. `acl-*`：ACL 规则和接口绑定（AR 的 `traffic-filter`、CE 的 ACL），表名和字段照 H3C 映射里已有的 `acl-*` 表；
  9. NAT：USG 的 `nat-policy` 规则（源区域、目的区域、源地址、目的地址、服务、动作）、地址池（`nat address-group`）、静态映射（`nat server`）；AR 的接口 NAT（`nat outbound`）。转换层目前没有 NAT 表，表名按 RFC 8512 的列表起。
- 设备给的代码，映射里没列的，原样报告，不猜。CLI 的输出可以拿来对照代码的含义；样本在 `.runtime/ensp/capture/`，每台设备一个目录。
- 测试：回包放进 `tests/fixtures/netconf/huawei-yunshan/`（先确认里面没有口令或密文），每张表一个用例，覆盖代码不认识、查表落空、表为空三种情况。现有 H3C 的测试见 `tests/unit/test_netconf_standard.py`、`test_netconf_api_mapping.py`。

完成标准：三台设备的转发相关表都能从 NETCONF 得到标准记录，没有未解释的问题；同一时刻与 CLI 输出逐项对照一致（差异逐条写明原因）。

### C. 标准树与路径计算接入

1. **建树**：
   - `standard_trees.py` 补二层：VLAN、端口成员、聚合、FDB，以及子接口封装。可选模型有 `ietf-if-extensions` 和 `ietf-if-vlan-encapsulation`（草案，固定版本采用）。
   - NAT 导出 RFC 8512（`ietf-nat`，模块已在 `src/apps/topology/resources/yang/`）。设备不打印的编号键（实例号、策略号、池号、映射条目序号）按设备列出的顺序推出，在 metadata 的 `derived_keys` 写明是推出的，做法同已有的 RIB 名、ACL 端口集名、PBR sequence-id。`nat-policy` 的源区域、目的区域在 RFC 8512 里没有位置，放进 `non_standard`，由 `display zone` 的区域成员换算成接口。现有的 `nat_section` 只覆盖 H3C 的部分情况，要扩展。
   - ACL：已经走 RFC 8519 + RFC 9899 的树。时间段、分片、不连续通配符按第 4 节放进 `non_standard`，恢复成能判定。
   - 放不下的都进 `non_standard`，用钉住的模块校验。
2. **查询表**：二层、子接口、NAT 各出一张查询表（做法同 `route_table.py`、`acl_table.py`：只从树和 `non_standard` 取，字段用标准节点名）。
3. **路径计算**：目前路由、接口、地址、ARP、LLDP、ACL、策略路由已从标准记录或树取，NAT 和二层还读事实。
   - 二层转发（`l2_forwarding.py`）改为读查询表，让路径能回答：网关子接口的 VLAN 在下联聚合上放不放行；聚合成员有几个在工作；终端的 MAC 学在哪个端口。
   - NAT（`model/nat.py`）改为读 NAT 查询表。按区域的 NAT 策略在每一跳上：入接口确定源区域，查路由确定出接口和目的区域，再匹配规则。目的 NAT、源 NAT 与路由的先后，要在实验网用命中次数实测，写成家族数据。回程按去程的转换还原（现有做法）。
   - 原来走 CLI 事实的设备（思科、H3C 的 CLI 采集）行为不能变：用现有夹具测试守住，改前改后同一份样本的路径结论一致。
4. **设备识别与采集**：按数据模型逐表尝试 IETF YANG → OpenConfig YANG → 厂商私有 YANG → SSH/CLI；使用 `<hello>` 模块清单约束读取，并保留失败原因。采集入口先尝试 NETCONF：RFC 8348 硬件信息优先，其次 OpenConfig platform，之后才按已宣告的厂商 YANG 识别。LLDP 在宣告 `openconfig-lldp` 时先读 OpenConfig 0.2.x 映射；查询不可用再读设备厂商映射。查询成功但邻居列表为空，视为有效空观测。AR/USG 未宣告 `openconfig-lldp`，直接读华为私有 LLDP，再投影到 OpenConfig 1.2.0 内部结构；这不代表 AR/USG 原生支持 OpenConfig。对拓扑发现，接口、路由、LLDP 邻居/管理地址是核心表；其他标准表的不完整应保留为缺口，但不能阻断已成功的 LLDP 扩散。在线单种子验证已从 AR `.11` 按邻居管理地址扩散到 USG `.12`（深度 1）和 CE `.13`（深度 2）。CE `.13` 的 OpenConfig LLDP 读取已成功；原始 LLDP 证据在 GE1/0/1、GE1/0/2 均见到 USG6306F，并通告管理地址 `192.168.79.12`，所以 USG 至少在这两条上联发送 LLDP。当前 CE 因非核心标准表问题仍回退 CLI，任务以 `parse_incomplete` 暂停；采集回退范围已收窄为拓扑核心表，待再部署验证。
5. 提升 `topology`、`netconf-api` 的 `app.yaml` 版本号，按记忆里的"App package upgrade procedure"部署，在平台上对实验网做发现。

完成标准：平台对实验网的发现全部经 NETCONF 完成；快照里有 4 条业务链路、聚合、两个 VLAN、子接口、OSPF 邻接和三张路由表；以下路径结论正确：
- 客户端 10.1.10.10 到服务器 10.1.20.10：PC → CE-1 VLAN 10 → Eth-Trunk1 → USG Eth-Trunk1.10 → 查路由 → Eth-Trunk1.20 → Eth-Trunk1 → CE-1 VLAN 20 → 服务器；
- 客户端到 203.0.113.1：经 USG GE0/0/0 的 OSPF 路由到 AR-1，在 USG 出接口做源 NAT，源地址变为 10.0.12.2；回程从 AR-1 回到 10.0.12.2，在 USG 还原；
- 服务器到 203.0.113.1：没有匹配的 NAT 规则，回程在 AR-1 没有到 10.1.20.0/24 的路由；
- 路径经过 USG 时注明：它是按区域做安全策略的防火墙，安全策略不在孪生里计算，需要登录查看。

### D. 诊断评测集

在 `evals/network-twin/` 下按第 6 节的故障集建用例，格式见该目录 README。工作包 C 完成后先跑出基线分数，之后每次改动重跑。

### E. 以后再做（本轮不做）

- **华为 CLI 模板**：12 个模板在 `src/skills/shared/textfsm-templates/Huawei/YunShan/`，三台设备的实采输出都能解析，但**没有登记**到规则治理。
  - 原因：一登记，规则治理就会对所有 VRP 设备发布这些实现，老 VRP 的输出格式会被拒收（2026-11-11 实测，`test_topology_generic_parsers.py` 有 6 个用例因此失败）。
  - 以后登记时，要把实现限定在 YunShan 上（用型号或版本匹配），或者写一个 YunShan 自己的家族。
  - 采样时踩过的坑：USG 每条命令的输出前多一行时间戳；同一系统版本下 AR 的 `display arp` 表头与 USG、CE 不同；`display ip interface brief` 会把长的 VPN 实例名截断。
- 安全策略模型（IETF 对应的是 I2NSF NSF-Facing Interface 草案 -29，2022 年起卡在 RFC 编辑队列；其中没有安全区域条件，也没有入接口、出接口条件）。
- 路径每一跳挂巡检异常和变更；快照对比。

## 6. 实验网（华为 eNSP Pro）

GNS3 暂停使用。三台设备都是 YunShan OS 1.25.1.1，用户名 admin，口令向负责人要。管理口都在 `_management_vpn_`，从本机 192.168.79.1 直接 SSH。

| 设备 | 型号 | 管理地址 | 业务 |
|---|---|---|---|
| AR-1 | AR6700 | 192.168.79.11 | GE0/0/0 10.0.12.1/30；Lo0 10.255.0.1；Lo100 203.0.113.1（模拟互联网主机）；OSPF 1 area 0 |
| USG-1 | USG6306F | 192.168.79.12 | GE0/0/0 10.0.12.2/30（untrust）；Eth-Trunk1.10 10.1.10.1/24（trust）；Eth-Trunk1.20 10.1.20.1/24（dmz）；Lo0 10.255.0.2；OSPF 1 area 0 |
| CE-1 | CE6800 | 192.168.79.13 | 纯二层：Eth-Trunk1（GE1/0/1、1/0/2，LACP）放行 VLAN 10、20；GE1/0/3 接入 VLAN 10；GE1/0/4 接入 VLAN 20 |
| PC-CLIENT-1 | — | — | 10.1.10.10/24，网关 10.1.10.1，接 CE-1 GE1/0/3 |
| PC-SERVER-1 | — | — | 10.1.20.10/24，网关 10.1.20.1，HTTP 80，接 CE-1 GE1/0/4 |

USG 上的安全策略（孪生不计算，只作背景）：`client-to-web`、`client-to-internet`、`ospf`，缺省拒绝。

NAT 策略（孪生计算）：`client-easy-ip`，trust→untrust，源 10.1.10.0/24，easy-ip。USG 不把内网网段发布进 OSPF，所以客户端上网靠 NAT。

ACL：目前三台设备都没有配。评测集要用时再加（见故障集 F8）。

基线（2026-11-10 已核实）：
- LLDP 两端都能看到 4 条业务链路；
- 聚合两端各两个成员都是 Selected；
- OSPF 邻居 Full，USG 学到 203.0.113.1/32；
- CE-1 在正确的 VLAN 上学到了两台 PC 的 MAC。

已核实（2026-11-14）：PC 不回 ICMP echo（USG 和 PC 互 ping 都是请求有会话、无应答），eNSP Pro 节点的已知怪癖；另外这台 USG6306F 模拟器对"业务口进来的、发往设备自身的 ICMP"不交付控制平面——service-manage 和显式 local 域策略都无法让它应答，管理口（MEth）与环回正常。因此 PC ping 网关不可用作健康检查，转发路径（transit）不受影响。USG 安全策略补了 local-icmp 规则（local↔各域 ICMP 放行，已 save）。

故障集（影响转发路径的；每次只改一处，改完恢复）：

| # | 操作 | 孪生应给出的结论 |
|---|---|---|
| F1 | CE-1 的 Eth-Trunk1 去掉 VLAN 20 | 二层：聚合不放行 VLAN 20，服务器所在 VLAN 与网关子接口断开 |
| F2 | CE-1 关闭 GE1/0/1 | 聚合少一个成员，路径仍完整，不报故障 |
| F3 | AR-1 的 OSPF 宣告到错误区域 | 邻居不起，USG 没有到 203.0.113.1 的路由 |
| F4 | USG 删除 Eth-Trunk1.20 的地址 | USG 没有 10.1.20.0/24 的直连路由，客户端到服务器在 USG 处无路由 |
| F5 | CE-1 GE1/0/4 改到 VLAN 30 | 服务器的 MAC 不在 VLAN 20，网关的 ARP 解析不到 |
| F6 | 服务器网关配错 | 去程到达服务器；孪生看不到主机网关，结论要说明停在哪 |
| F7 | 服务器地址改到客户端网段 | 地址与所在 VLAN 的网关网段不符 |
| F8 | AR-1 GE0/0/0 入方向加 ACL，拒绝源 10.0.12.2 的 ICMP | 客户端 ping 203.0.113.1 在 AR-1 入接口被拒（报文已被 USG 转换成 10.0.12.2），指出 ACL 名和规则 |
| F9 | USG 删除 NAT 策略 `client-easy-ip` | 去程到达 AR-1；回程在 AR-1 没有到 10.1.10.0/24 的路由 |
| F10 | USG 的 `client-easy-ip` 源地址改成 10.1.30.0/24 | 客户端的报文不再被转换，结论同 F9，并指出规则存在但不匹配 |

本机 `.runtime/ensp/`（不入库）：三台设备的配置文件、只读采集脚本 `ensp_read.py`、下发脚本 `ensp_config.py`、全部 CLI 实采样本 `capture/`、更早的 `实验设计.md`。

## 7. 测试与评测集的位置（负责人 2026-11-11 已定）

- 拓扑相关的测试和夹具入库，已从 `62e064e` 取回：
  - `tests/unit/` 下 topology、netconf、device_access 的单元测试；
  - `tests/integration/` 两个；
  - `tests/fixtures/topology`、`tests/fixtures/netconf`。
  
  `.gitignore` 只放行这些，其余测试仍留在本机。`.gitattributes` 恢复了"CLI 夹具保持原始字节"的规则，少了它会有 30 个测试失败。
- 没取回的 4 个文件：`test_topology_m1_mapping.py`、`test_topology_lab_fixtures.py`（依赖已删除的 `scripts/topology` 和 M1 脚本），`test_device_access_dsh.py`、`test_device_access_baseline_compare_dsh.py`（依赖未取回的 `test_dsh_runtime`）。需要时连同依赖一起取回。
- 运行：`venv/Scripts/python.exe -m pytest tests/unit -q -p no:cacheprovider`。
- 取回当天的基线（2026-11-11）：2096 通过，44 跳过。
  - 5 个失败在取回时就有：`test_netconf_api_service` 的 trial 用例、`test_topology_ospf_integration`、`test_topology_ospf_parsers` 的 profiles 用例、`test_topology_profiles` 的两个批量用例。
  - 19 个错误在 `test_topology_device_ip`，原因是本机缺 `yangson==1.6.3`（`tests/topology-requirements.txt`）：pip 找不到这个版本，旧做法是 `PYTHONPATH=.runtime/p0-2/yang-deps`，那个目录现在没有了。
  - 判断回归时与这份基线对比。
- 评测集在 `evals/network-twin/`。
- 新增或修改的映射、建树、路径逻辑都要有测试。

## 8. 进度

| 日期 | 工作包 | 状态 | 孪生层 | AI 层 |
|---|---|---|---|---|
| 2026-11-11 | — | 方向修订（NAT、ACL 在范围内，往后放的只有安全策略和 CLI 模板）；测试和夹具取回；华为 CLI 模板写好但不登记 | 未测 | 未测 |
| 2026-11-11 | A | 完成：三台开 NETCONF，模块清单、转发相关回包、16 个模块的 YANG 原文已取；无 IETF 数据模型；OpenConfig 部分可用（platform 三台都有，LLDP 只有 CE），路由/ARP 等仍走华为私有模型 | 未测 | 未测 |
| 2026-11-11 | B | 完成：华为映射 20 张表，引擎支持多命名空间、上溯、leaf-list、空元素；三台实读无问题，路由与 CLI 逐条一致 | 未测 | 未测 |
| 2026-11-12 | C | 进行中：华为 VRP NETCONF 映射和 profile；接口 VPN 绑定、LLDP 推导键、二层查询行、区域 NAT 计算已接入；身份识别优先 RFC 8348/OpenConfig platform，再按厂商模块识别；CE 原生 OpenConfig LLDP 可直读，AR/USG 私有 LLDP 投影到 OpenConfig 内部结构。三条路径离线验证符合预期；拓扑图的管理口地址身份合并已修复，待在线复验 | 离线路径通过；评测集未建立/未跑 | 未测 |
| 2026-11-14 | C | 完成：三个 App 升级部署后，平台对实验网的发现全部经 NETCONF（三台均 netconf_yang，单种子沿 LLDP 管理地址扩散）；三条路径结论在实采数据上全部成立（客户端→服务器经 USG 子接口路由；客户端→203.0.113.1 在 USG 出接口源 NAT 为 10.0.12.2、回程还原；服务器→203.0.113.1 去程无 NAT、回程 AR 无路由）。端点归属 gap 属实：恢复配置后尚无流量，CE 的 MAC 表为空 | 平台发现通过；评测集未建立/未跑 | 未测 |

**实验网运维事故（2026-11-14 复盘）**：AR/USG 的业务配置 11-12 晚随设备重启丢失——当时配置只在 running 上 commit，从未 `save` 到启动文件（CE 系列的 commit 自带落盘，CE-1 因此幸免）。平台此前几次发现采集到的"缺地址、无 OSPF/NAT"不是采集缺陷，是设备的真实状态。两台设备配置已重放并 save。教训：任何设备配置变更后必须确认落盘；孪生如实报告的"异常"要先核对设备真实状态，再怀疑采集。按表子树过滤器在配置齐全的设备上验证完整（接口表含 6 个子接口与聚合，地址、OSPF 邻居过滤器均取到数据）。

**拓扑身份合并修复（2026-11-14，`8dda5e2`）**：同一机箱以 `fa:c7:..` 与 `fac7-..` 两种 MAC 拼法出现（华为 NETCONF LLDP 子类型拼写不定），builder 只在子类型为 `mac-address` 时归一化，USG 被拆成 3 个节点；同时邻居端口被归一化为方言规范形（GigabitEthernet1/0/1）而设备自身清单是短形（GE1/0/1），每条链路画两遍。修复：builder 消费 chassis 的四处入口统一按 MAC 归一化（能解析成 MAC 即合并），邻居端口优先用邻居自己接口清单的拼写；新增回归测试。主机密钥修复（`428a304`）：设备重启换密钥后，自动采集现在重固钉并留审计（`host_key_auto_accepted`），终端仍先问再信。
