# 拓扑发现作为规则治理的消费方：系统基础检查项与接口方案

负责人决定（2026-10-04）：规则治理生产 TextFSM、检查项、变化规则和事件，巡检和拓扑只消费。拓扑不再手写设备解析器。规则治理增加一组拓扑所需、不可删除的系统基础检查项，并定义它们的同名 Value。拓扑先接入，巡检的迁移另开一张卡。

## 1. 现状

- 规则治理已有正式的消费契约 `src/apps/rule_governance/runtime.py`（版本 1，只读，不含审批、发布、生成等操作）。巡检执行、证据导入和设备身份识别都经过它。
- 检查项、采集实现、模板绑定和运行包都存放在平台共享的数据库表里，例如 `netops_check_items`、`netops_collection_implementations` 和 `netops_check_item_runtime_bundle_versions`。
- 选择逻辑：
  - 采集实现由 `CheckItemImplementationResolver` 按设备匹配打分，型号 40 > 系统家族 30 > 平台 25 > 厂商 20，最具体者胜出。
  - 模板由 `ParserRuntimeRepository` 按"厂商 + [型号, 系统家族] + 规范命令"查找，型号优先。
- 拓扑现在绕过了这个契约：它经由 `src.core.parser.*` 的转发直接 import 规则治理的内部实现，且多数事实是手写 Python 解析器产出的。本方案就是要纠正这两点。

## 2. 系统基础检查项

**统一约定**

- **命名**：编号为 `topology.<事实>`。
- **契约类型**：`value_contract: same-name/v1`，即 TextFSM 的 Value 名就是标准字段名，不允许改名或转换。
- **模式**：`result_mode: structured`，`assessment_mode: evidence`。拓扑把它们当作证据使用，不在这里做健康判定。
- **Value 名**：大写；有对应的 ntc-templates 名称时直接沿用（`IP_ADDRESS`、`MAC_ADDRESS`、`INTERFACE`、`NETWORK`、`NEXTHOP_IP` 等），使社区模板可以直接挂接。
- **列表值**：一个字段有多个值时用 TextFSM 的 `List`，例如成员端口、RT、管理地址。

**保护规则**

- **不可删除**：清单写在规则治理的资产文件 `check_items/assets/system_check_items.yaml` 中，由代码持有，随目录引导写入数据库。删除这些检查项或它们的默认采集实现一律拒绝（`CatalogGovernanceConflictError`，返回 409）。
- **字段不可删改**：新版本只能增加可选 Value，不能删除或改名必填 Value（新建草稿时由 `ensure_compatible` 校验）。
- **语义自带**：每个 Value 的语义由检查项自己定义（`semantic_definition`），冻结在 `topology-system` 目录下，不依赖共享的标准观测目录。
- **实现可自由增改**：各厂商、各型号的采集实现（命令 + 模板）可以随时新增或替换。

**清单**（由 `system_check_items.yaml` 生成；共 34 项，除安全域只有 Comware 7 的实现外，全部已在 IOS/IOS-XE 与 Comware 7 上接入）

| 检查项 | 名称 | Value（★ 为键/必填） | 已发布的采集实现 |
|---|---|---|---|
| `topology.arp` | ARP 表项 | ★IP_ADDRESS, ★MAC_ADDRESS, INTERFACE, VLAN_ID, AGE, TYPE | ios: `show ip arp`<br>comware: `display arp` |
| `topology.routes` | 路由表（每个下一跳一行） | ★NETWORK, ★PREFIX_LENGTH, ★PROTOCOL, TYPE, DISTANCE, METRIC, NEXTHOP_IP, NEXTHOP_IF, NEXTHOP_VRF, VRF, UPTIME, FLAG | ios: `show ip route`<br>comware: `display ip routing-table` |
| `topology.vrf_detail` | VRF/VPN 实例详情（每地址族一行） | ★VRF, ★ADDRESS_FAMILY, RD, INTERFACES, IMPORT_RT, EXPORT_RT | ios: `show vrf detail`<br>comware: `display ip vpn-instance instance-name {vrf}` |
| `topology.evpn_peers` | EVPN/VXLAN 每 VNI 的远端 VTEP | ★VNI, ★PEER, TYPE, STATE, INSTANCE, RD, INTERFACE, ROUTER_MAC, EVNI, FLAGS, UPTIME, TUNNEL_ADDRESS, ENCAPSULATION | ios: `show nve peers`<br>comware: `display evpn auto-discovery imet` |
| `topology.ldp_peers` | LDP 会话 | ★PEER_LSR_ID, LABEL_SPACE, STATE, ROLE, LOCAL_LSR_ID, LOCAL_LABEL_SPACE, DISCOVERY_INTERFACE, DISCOVERY_ADDRESS, ADDRESSES | ios: `show mpls ldp neighbor`<br>comware: `display mpls ldp peer` |
| `topology.mpls_lfib` | MPLS 标签转发表 | ★FEC, ★LOCAL_LABEL, ★OUT_LABEL, INTERFACE, NEXTHOP, VRF, PROTOCOL, BYTES | ios: `show mpls forwarding-table`<br>comware: `display mpls lsp` |
| `topology.evpn_instances` | EVPN 实例（IOS 每实例每 VLAN 一行） | ★INSTANCE, SERVICE, RD, IMPORT_RT, EXPORT_RT, INSTANCE_STATE, ENCAPSULATION, VLAN_ID, STATE, VNI, L3_VNI, VTEP_IP, VRF, GATEWAY_INTERFACE, CORE_INTERFACE, ACCESS_INTERFACE, SERVICE_INSTANCE, PEER | ios: `show l2vpn evpn evi detail`<br>comware: `display evpn instance verbose` |
| `topology.vxlan_vnis` | VXLAN VNI 与 VSI | ★VNI, INSTANCE, L3_VNI, INTERFACE, MULTICAST_GROUP, STATE, MODE, SEGMENT_TYPE, SEGMENT, CONFIG, VRF, GATEWAY_INTERFACE, TUNNEL, TUNNEL_STATE, ACCESS_INTERFACE, ACCESS_STATE | ios: `show nve vni`<br>comware: `display l2vpn vsi verbose` |
| `topology.tunnel_interfaces` | 隧道接口 | ★INTERFACE, STATE, PROTOCOL_STATE, SOURCE, DESTINATION, MODE | comware: `display interface tunnel` |
| `topology.evpn_mac` | EVPN MAC 路由 | ★MAC_ADDRESS, ★INSTANCE, VLAN_ID, ESI, ETHER_TAG, NEXTHOP_VTEP, NEXTHOP_INTERFACE, SERVICE_INSTANCE, FLAGS, LOCAL_FLAG, LINK, ENCAPSULATION | ios: `show l2vpn evpn mac`<br>comware: `display evpn route mac` |
| `topology.vrfs` | VRF/VPN 实例列表 | ★NAME, RD, PROTOCOLS, INTERFACES, CREATED | ios: `show vrf`<br>comware: `display ip vpn-instance` |
| `topology.bgp_ipv4_peers` | BGP IPv4 单播邻居 | ★PEER, ★PEER_AS, STATE, PREFIXES_RECEIVED, UP_DOWN, ROUTER_ID, LOCAL_AS | ios: `show ip bgp summary`<br>comware: `display bgp peer ipv4` |
| `topology.bgp_vpnv4_peers` | BGP VPNv4 邻居 | ★PEER, ★PEER_AS, STATE, PREFIXES_RECEIVED, UP_DOWN, ROUTER_ID, LOCAL_AS | ios: `show bgp vpnv4 unicast all summary`<br>comware: `display bgp peer vpnv4` |
| `topology.bgp_evpn_peers` | BGP L2VPN EVPN 邻居 | ★PEER, ★PEER_AS, STATE, PREFIXES_RECEIVED, UP_DOWN, ROUTER_ID, LOCAL_AS | ios: `show bgp l2vpn evpn summary`<br>comware: `display bgp peer l2vpn evpn` |
| `topology.ospf_neighbors` | OSPF 邻居 | ★NEIGHBOR_ID, ★INTERFACE, ADDRESS, PRIORITY, STATE, ROLE, DEAD_TIME, PROCESS, ROUTER_ID, AREA | ios: `show ip ospf neighbor`<br>comware: `display ospf peer` |
| `topology.vrrp` | VRRP 组 | ★INTERFACE, ★VRID, STATE, PRIORITY, VIRTUAL_IP, MASTER_IP | ios: `show vrrp`<br>comware: `display vrrp verbose` |
| `topology.lag` | 链路聚合组与成员 | ★INTERFACE, GROUP, BUNDLE_FLAGS, PROTOCOL, MEMBERS, MEMBER_FLAGS, BUNDLED | ios: `show etherchannel summary`<br>comware: `display link-aggregation verbose` |
| `topology.stp_ports` | 生成树端口角色与状态 | ★INSTANCE, ★INTERFACE, VLAN_ID, MSTI, PROTOCOL, ROLE, STATE, COST, PORT_ID, TYPE, ROOT_PRIORITY, ROOT_ADDRESS, ROOT_PORT, IS_ROOT, BRIDGE_PRIORITY, BRIDGE_ADDRESS | ios: `show spanning-tree` |
| `topology.interfaces` | 接口状态 | ★INTERFACE, ★LINK, PROTOCOL, DESCRIPTION, MODE, PRIMARY_IP, SPEED, DUPLEX, TYPE, PVID | ios: `show interfaces description`<br>comware: `display interface brief` |
| `topology.ip_interfaces` | 接口地址 | ★INTERFACE, ADDRESS, ADDRESSES, VRF, STATUS, PHYSICAL, PROTOCOL, DESCRIPTION | ios: `show ip interface`<br>comware: `display ip interface brief` |
| `topology.mac` | MAC 地址表项 | ★MAC_ADDRESS, ★VLAN_ID, PORT, TYPE, STATE, AGING | ios: `show mac address-table`<br>comware: `display mac-address` |
| `topology.vlans` | VLAN 与端口列 | ★VLAN_ID, NAME, STATUS, PORTS | ios: `show vlan brief`<br>comware: `display vlan brief` |
| `topology.lldp_neighbors` | LLDP 邻居 | ★LOCAL_INTERFACE, ★CHASSIS_ID, ★PORT_ID, CHASSIS_ID_TYPE, PORT_ID_TYPE, NEIGHBOR_NAME, SYSTEM_DESCRIPTION, MGMT_ADDRESS | ios: `show lldp neighbors detail`<br>comware: `display lldp neighbor-information verbose` |
| `topology.device_version` | 设备版本与身份 | ★OS_VERSION, MODEL, SERIAL, HOSTNAME, IMAGE_NAME, IMAGE_VERSION, UNIX_PROCESSOR, BOOT_IMAGE, BOOT_IMAGE_VERSION, SYSTEM_IMAGE, SYSTEM_IMAGE_VERSION, VIRTUAL_CPUS, VIRTIO_NIC | ios: `show version`<br>comware: `display version` |
| `topology.lldp_local` | 本机 LLDP 身份与端口管理地址 | ★CHASSIS_ID, SYSTEM_NAME, SYSTEM_DESCRIPTION, SERIAL, MANUFACTURER, MODEL, LOCAL_INTERFACE, MGMT_ADDRESS | comware: `display lldp local-information` |
| `topology.trunks` | Trunk 端口的四张 VLAN 表 | ★PORT, MODE, ENCAPSULATION, STATUS, NATIVE_VLAN, ALLOWED_VLANS, ACTIVE_VLANS, FORWARDING_VLANS, BAD_ROW | ios: `show interfaces trunk`<br>comware: `display port trunk` |
| `topology.switchports` | 二层端口模式与 VLAN | ★NAME, SWITCHPORT, ADMIN_MODE, OPER_MODE, ADMIN_ENCAPSULATION, OPER_ENCAPSULATION, ACCESS_VLAN, NATIVE_VLAN, ADMIN_NATIVE_TAGGING, OPER_NATIVE_TAGGING, TRUNK_VLANS, BAD_FIELD | ios: `show interfaces switchport` |
| `topology.ospf_interfaces` | OSPF 接口开销（简表，按行） | ★ADDRESS, TABLE, INTERFACE, PROCESS_ID, ROUTER_ID, LABEL, AREA, TYPE, STATE, COST, PRIORITY, DR, BDR, NEIGHBORS, BAD_ROW | ios: `show ip ospf interface brief`<br>comware: `display ospf interface` |
| `topology.ospf_process` | OSPF 进程与接口映射（按行） | ★PROCESS_ID, ROUTER_ID, AREA, VRF_EVIDENCE, CONFIRMED_ROUTER_ID, ADDRESS, INTERFACE, COST, STATE | ios: `show ip ospf`<br>comware: `display ospf` |
| `topology.pbr_bindings` | 策略路由的接口绑定 | ★INTERFACE, ★POLICY, TYPE | ios: `show ip policy`<br>comware: `display ip policy-based-route setup` |
| `topology.nat_bindings` | NAT 的接口绑定 | ★INTERFACE, ★KIND, RULE | ios: `show ip nat statistics`<br>comware: `display nat all` |
| `topology.acl_rules` | IPv4 ACL 规则（按设备的匹配顺序） | ★ACL_NAME, ★LINE_NUM, ACL_TYPE, ADDRESS_FAMILY, MATCH_ORDER, ACTION, PROTOCOL, SRC_/DST_ HOST、ANY、NETWORK、WILDCARD、NETWORK_OBJECT_GROUP_NAME、PORT_MATCH、PORT、PORT_RANGE_START/END, ICMP_TYPE, ICMP_CODE, TCP_FLAG, FRAGMENTS, TIME, STATE, VRF, OTHER, MATCHES | ios: `show ip access-lists`<br>comware: `display acl all` |
| `topology.acl_bindings` | ACL 的接口绑定 | ★INTERFACE, ★DIRECTION, ADDRESS_FAMILY, ACL_NAME, SCOPE, STATUS, DEFAULT_ACTION | ios: `show ip interface`<br>comware: `display packet-filter interface` |
| `topology.security_zones` | 安全域成员 | ★ZONE, ★INTERFACE | comware: `display security-zone` |

说明：

- **字段类型**：Value 的类型（text、interface、ipv4、mac、integer、label、route_target）写在契约里。拓扑只按类型做统一规范化，例如接口名补全、MAC 统一格式、`Pop Label` 转为 `implicit-null`，不出现按厂商的分支。
- **平台固定、设备不打印的值写在实现上**（`defaults`）：IOS 的 ACL 末尾隐含拒绝（`DEFAULT_ACTION: Deny`）；Comware 不带 vpn-instance 的规则只匹配公网报文（`VRF: '--'`）。设备打印了的以设备为准。
- **"未配置"的含义写在实现上**：例如设备不跑 BGP 时输出空白或提示语，属于有效的空表。这由采集实现的输出质量策略声明，即规则治理已有的 `output_quality_policy`。

## 3. 采集实现与模板

- **每个系统家族一份默认实现**：例如 Cisco IOS/IOS-XE、H3C Comware 7，匹配条件写厂商或系统家族，`template_scope: family`。
- **型号例外**：只在某型号输出不同时才加实现，匹配条件写型号，自动获得更高的优先级。
- **模板位置**：放在共享模板库 `src/skills/shared/textfsm-templates/<厂商>/<系统家族>/`，经规则治理登记、校验后发布。
  - 先用三套实验网的夹具（vpn-fabric、campus-wan、VSR）做校验样本。
  - ntc-templates 中 Value 名能对上的直接复用；对不上的，以本清单的名字为准另写。
- **按 VRF 展开的命令**：例如 IOS 的 `show ip route vrf {vrf}`、Comware 的 `display ip routing-table vpn-instance {vrf}`。采集实现声明带实例占位的命令形式（`scoped_command`），拓扑用已发现的实例名填入；解析时使用同一实现的模板。命令本身就带实例名的（Comware 的 `display ip vpn-instance instance-name {vrf}`）只按实例执行。按实例的命令形式取系统家族级的默认实现，使任务授权与采集用同一组命令。
- **模板钉住摘要**：每个实现记录模板文本的 sha256。模板被改动而摘要未经评审更新时，选择被拒绝（`SelectionError`），拓扑记 `implementation_invalid` 并让该项失败，而不是悄悄换用新解析。
- **输出质量策略**（`output_quality_policy`，规则治理已有字段的扩展）：
  - `valid_empty_patterns`：表示"未配置"的输出，例如空白；
  - `required_patterns`：每份有数据的输出都必须出现的行，例如表头；缺失时不算完整；
  - `count_patterns`：设备自己打印的条目数 `{pattern, of}`，`of: rows` 与读到的行数比对，`of: keys` 与按检查项键去重后的条目数比对。例如 Comware 路由表的 `Routes : N` 是下一跳行数，`Destinations : N` 是前缀数。
- **模板写法**：表格以外的行必须由规则显式接受；任何没认出的行走 `^. -> Error`。行尾用 `\s*$$` 锚定，避免"多出来的字"被吞掉。

## 4. 拓扑消费接口

拓扑只 import `src.apps.rule_governance.runtime`。契约版本 1 只做加法、保持兼容，新增 `CheckItemSelectionReader`（只读）：

```text
select(check_item_ids, device) -> {check_item_id: Selection}
  device: vendor / platform（系统家族）/ model
  Selection: check_item_version_id, implementation_id/version_id,
             command, scoped_command（"{vrf}" 占位）,
             fields（同名 Value 契约：类型、是否必填、是否为键）,
             template_ref, template_text, template_sha256, output_quality_policy
  没有可用实现时不出现在结果中 → 拓扑记 no_implementation，不回落到手写解析器
  模板 Value 超出契约或缺少必填 Value、模板摘要与钉住值不符 → SelectionError

parse(selection, raw_text) -> ParsedRows
  status: complete / partial / unsupported / failed（先按实现的质量策略分类输出）
  rows: 同名 Value 字典；lines[i]: 给第 i 行赋过值的源文本行号（证据）
  rejected: 模板拒绝的行号（遇到 Error 记下该行，继续读下一行）
  problems: 缺少必需行、条目数不符
  incomplete_output: 输出被分页或截断
  有拒绝行、问题或被截断即为 partial；一行都没读到则为 failed

commands(check_item_ids) -> [{implementation_id, platforms, command, scoped_command}]
  供消费方生成精确的命令授权
```

**拓扑侧采集流程**（`collectors/collect.py`、`collectors/rules.py`）：
1. 识别设备，沿用现有探测。
2. 采集档案（profile）里标 `parser: rules` 的能力不写命令；采集时用 `select` 选出的实现命令。
3. 用平台的设备访问能力执行。命令仍须是拓扑命令策略下的 L1 只读；不是的记 `command_not_read_only`，也不会进入任务授权（`command_allowlist`）。
4. 调用 `parse` 得到同名 Value 行。
5. 每个检查项用一段投影把行转成拓扑事实，只按 Value 含义规范化（接口名、地址、地址族），不读厂商 CLI 文本。
6. 缺陷的使用由拓扑决定：ARP、VRF 详情、EVPN 对端按行使用，坏行剔除、结果为 partial；路由表只整表使用，任何拒绝行、条目数不符或无效行都使整表失败。输出被截断记 `collection_incomplete`，与内容缺陷分开。

建模层（IETF 树、叠加层解读、路径）不变。

## 5. 迁移顺序

1. **规则治理**：
   - 实现系统基础检查项的资产和保护（拒绝删除、契约只能做加法）；
   - 运行时契约增加 `select` 和 `parse`。
2. **先打通一条完整链路**：用 `topology.vrf_detail`、`topology.evpn_peers` 等这次新增的 VPN/EVPN 事实，加上已有模板的 `topology.arp` 和 `topology.routes`。
   - 打通的意思是：IOS 与 Comware 的模板发布后，拓扑经接口采集和解析，结果与现有夹具的期望一致。
3. **逐项迁移其余事实**：每迁一项，就删掉对应的手写解析器。
4. **拓扑去掉对 `src.core.parser.*` 转发的引用。**
5. **巡检改为经同一接口消费，另开卡处理。**

## 6. 进度（2026-10-04）

- **已完成：IOS/IOS-XE 与 Comware 7 的全部拓扑事实都经规则治理采集和解析**（上表 29 项；`inventory`、`irf` 两项本就没有解析器）。
  - 采集档案里这些能力只写 `parser: rules`；命令、按 VRF 的命令形式由已发布的实现给出，并仍须通过拓扑的 L1 只读校验，也只有这样的命令才进入任务授权。`version` 例外：它的命令就是身份识别探测命令，保留在档案中。
  - 每项迁移前都用全部实验网夹具（必要时加上原测试中的全部变异输入）比对新旧结果，事实一致后才删除对应的手写解析器。唯一的有意差异：LLDP 邻居的系统描述现在完整读出（原解析器在 IOS 上为空、在 "Technical Support: ..." 这类行处截断）。
  - 拓扑只经 `src.apps.rule_governance.runtime` 使用规则治理；对 `src.core.parser.*` 的引用和固定 NTC 模板层已移除。
- **拓扑侧的消费规则**（`collectors/rules.py`、`collectors/rules_tables.py`）：
  - 按行使用的表剔除坏行；路由表整表使用（`WHOLE_TABLES`）；
  - VRF 列表、版本、本机 LLDP 汇总为设备事实（`SUMMARIES`），BGP/OSPF 邻居表头、生成树实例头另成汇总（`HEADERS`），VLAN 的端口列拆成 `summary.members`（`SPLITS`）；
  - 一张表里同一实体只能出现一次（`UNIQUE`），重复的各条都剔除并记 `duplicate_entry`；
  - Trunk、二层口、OSPF 接口与进程是带否决语义的整表：模板把每个有意义的行变成带类型的行，`rules_tables.py` 按输出顺序重放并施加原有的局部/全局否决；
  - 模板的 `Error "..."` 消息作为原因码出现在告警里（如 `conflicting_local_identity` 触发本机身份否决）；
  - 输出被截断且一行未读到时为 failed（Trunk/二层口为带全局否决的 partial），不把它当成空表；输出末尾仅由 Filldown 带出的行不算一行。
- **已部署（2026-10-04）**：
  - 规则治理 1.5.0 的 `bootstrap_version` 升到 3，部署时把系统基础检查项写入目录数据库；topology 0.7.0 声明依赖规则治理 ≥1.5.0，以 `published` 读取。
  - vpn-fabric 实机发现中，8 台设备的 M5 命令全部按已发布实现执行；输出用同一组钉住的模板复读，结果完整（见 `models/network-twin/m5/README.md`）。
- **厂商无关化（2026-10-05，topology 0.7.2、规则治理 1.5.1）**：按负责人认可的分支清单逐项改为数据或通用规则，见第 7 节。
  - #1/#2 Comware ARP 改用 `display arp verbose`：每个条目自带实例、VSI、VSI 接口和带单位的老化时间。ARP 检查项和 Comware 实现都升到 v2，`overlay.py` 不再推断实例。
  - #5 按 VRF 输出的实例名：由实现声明 `scope_header`，未声明的输出按接口绑定校验。
  - #6 字段单位：由实现声明 `units`。
  - #7 是否先列举 VRF：按能力是否有按 VRF 的命令形式推出。
  - #8–#11 家族、厂商、CLI 方言、验证过的主版本、接口名：都改为数据。
  - 三套实验网的夹具（vpn-fabric、VSR、campus-wan）已用新命令重采，同时采回 Comware 的 `display ospf interface verbose` 原文。
  - 未完成：#3/#4 OSPF 整表（改用 verbose 原文）、#12 NX-OS。
- **路径上标注策略路由/NAT（2026-10-05，topology 0.7.3、规则治理 1.5.2）**：
  - 新增 `topology.pbr_bindings`、`topology.nat_bindings` 两项，只读"哪个接口绑定了哪个策略、哪种 NAT"，不解析策略内容。
  - 路径仍按路由表推导。入接口绑定了策略路由的跳带 `policy_may_redirect` 缺口，入或出接口绑定了 NAT 的跳带 `nat_may_translate` 缺口，该方向为 partial；缺口给出设备、接口和策略名，由 LLM 到该设备查看策略。
  - 绑定表完整才保存。未完整采集的设备在跳上标 `policy_bindings: not_observed`，不改变状态。
  - 模板只认已知格式：Comware 策略类型只认 Forward/Local；`display nat all` 中未知段落里出现接口行即判失败。宁可"未观察"，不误报"没有策略"。
  - 未覆盖、无样本：IOS 的 NAT 虚拟接口（`ip nat enable`），防火墙按安全域或安全策略配置的 NAT。
  - 实机验收（campus-wan，2026-10-05）：在 CORE1、BR1 上临时配置只匹配 192.0.2.0/24 的测试策略，部署后经平台发现。8 台设备的两条命令都按已发布实现执行，观测完整；PC1 → 198.51.100.10 经 CORE1 的路径标出 CORE1 E1/0（策略路由、NAT inside）、E0/1（NAT outside）和 BR1 GE3/0（策略路由）、GE2/0（三种 NAT），经 CORE2 的路径只在 BR1 GE2/0 标 NAT。随后删除配置并重新发现，路径上已无标注。
  - 配置过又删除后，两家都不再输出"未配置"，而是输出空的表头或全局段落；这些原文也作为夹具保留（`campus-wan/policies/removed`）。
- **路径按流检查 ACL（第一期，2026-10-05，topology 0.8.0、规则治理 1.6.0）**：负责人决定把 ACL、策略路由、NAT 纳入路径计算（"不做这些你无法确定流量怎么走"），分三期，本期为 ACL。
  - 新增 `topology.acl_rules`、`topology.acl_bindings`、`topology.security_zones`。规则按设备列出的顺序（即匹配顺序，Comware 深度优先时设备已排好）读成条件；端口、ICMP、协议的名称按方言查表（`collectors/aclnames.yaml`），表里没有的不猜。模板不认识的条件（dscp、对象组等）原样放进 OTHER，该规则判断不了，不当作没有这个条件。
  - 实现可声明 `defaults`（见第 2 节）。IOS 的 ACL 绑定与接口地址共用 `show ip interface`，采集时同一命令只执行一次，两个能力各读一遍。
  - 路径按一条流判断（`trace_path` 新增 protocol、src_port、dst_port；ICMP 按 echo，反方向按应答：TCP 带 ACK、ICMP echo-reply）。每跳查入接口、出接口上的 IPv4 ACL（`model/acl.py`）：拒绝时以 `denied_in`/`denied_out` 结束，给出 ACL 名和规则序号；所有路径都被拒绝时该方向为 `blocked`。规则判断不了时，若所有可能结果一致仍给出结论（`undecided_before`），否则记 `filter_undetermined` 并写明缺什么（端口、协议或该规则的条件）。
  - 接口在安全域里记 `security_policy_not_evaluated`（域间默认拒绝，安全策略本期不计算）；MPLS/VXLAN 隧道内不查核心 ACL（看到的是外层头）；设备自己发出的报文不查其出接口 ACL（IOS 与 Comware 都不过滤本机报文）。跳上的 `not_observed` 列出没完整采到的表。
  - 实机验收（campus-wan）：DIST1 Vlan10 入向禁止 PC1 ping PC4 和到 172.16.20.0/24 的 telnet，BR1 GE2/0 出向禁止到 198.51.100.0/24 的 UDP 53。VPCS 实际打流的结果与推导一致，设备命中计数对得上（ping 3 次、telnet 1 次、DNS 2 次；web、echo-reply、NTP 通过）；原文作为夹具保留（`campus-wan/acl`，语法覆盖样本在 `campus-wan/acl-syntax`）。
  - 部署后经平台发现与路径接口复验 12 项全部通过：两台设备的 ACL 命令按已发布实现执行，IOS 的 `show ip interface` 每台只执行一次；六条流的结论与上面一致。随后删除配置并重新发现，路径上已无过滤，PC1 ping PC4 恢复。
  - 未覆盖：二层设备端口上的 ACL、安全策略内容、对象组展开、IPv6、IOS ZBF（IOL 不支持，无样本）。
  - 按 RFC 8519 导出（topology 0.8.1）：设备级导出新增 `acl` 一节（`model/device_acl.py`），`ietf-access-control-list` 树用冻结模块校验（模块取自 IANA，与 M1 policy-security 集同一字节；只启用设备上看到的特性：IPv4/TCP/UDP/ICMP 匹配、接口绑定、规则命中计数）。只导出能精确表达的规则：连续通配符转前缀，lt/gt 等价换成 lte/gte；一条规则多个端口、established、fragments、时间段、不连续通配符、模板不认识的条件都不导出，写进缺口。默认动作不是 RFC 8519 的字段，也不造一条规则，放在 metadata；Comware 只匹配公网的规则绑在 VPN 接口上时，该绑定不导出、记缺口。路径计算仍用内部结构，和路由表一样。
  - 第二期策略路由：节点内容（`show route-map`、`display ip policy-based-route`）、下一跳可达性、动作优先级、deny 节点按路由表转发。第三期 NAT：规则内容、两个方向的改写、回程按去程的翻译推导。Comware 的出方向 NAT 与包过滤、入方向 nat server 与策略路由谁先，文档未写明，届时在实验网用流量和命中计数实测，写成家族数据。
- **待做**：
  - 系统模板登记到规则治理的模板库（数据库），让页面可见、可评审；目前模板文件由资产清单按 sha256 钉住；
  - NX-OS 等家族在有实机样本后补实现，届时删掉对应的通用解析器；
  - 巡检经同一接口消费，另开卡（已在独立会话进行）。


## 7. 新增一个设备家族（2026-10-04 负责人决定：只改数据，不改代码）

拓扑代码不认识任何具体厂商。一个家族的全部差异都写在下面几处数据里。

| 步骤 | 位置 | 内容 |
|---|---|---|
| 1. 识别 | `collectors/signatures.yaml` 加一行 | 必填：`id`、厂商、版本输出特征 `pattern`、命令风格（show/display）、CLI 方言 `cli`。按需填写：`vendor_pattern`（同一家族以多个品牌出售时，从版本输出读品牌）、`profile_requires`（采集档案验证过的主版本）、`supersedes`（版本输出同时带另一家族的特征时，以本家族为准）、`global_instance`（该家族对公网实例的叫法，如 VRP 的 `_public_`）、`discovery_profile` |
| 2. 采集档案 | `collectors/profiles/<档案名>.yaml` | 会话准备、各能力 `parser: rules`；版本命令须为"命令风格 + version" |
| 3. 接口名 | `collectors/ifnames.yaml` 加该 CLI 方言的一组 | 规范前缀、类型、设备会打印的别名 |
| 4. 命令与解析 | 规则治理：为系统基础检查项发布该家族的实现 | 命令、TextFSM 模板、按 VRF 的命令形式 `scoped_command`、输出自报实例名的行 `scope_header`、空输出与条目数校验；型号例外只在样本证明有差异时添加 |
| 5. 平台 | 设备访问层（平台能力，不属于拓扑） | 该 CLI 方言的只读命令规则和终端准备；目前支持 ios、nxos、comware、vrp |

- 规则治理是否为某个家族发布了实现，决定拓扑是否经规则治理采集和解析该家族。拓扑代码里没有家族名单。
- 守卫测试 `tests/unit/test_topology_vendor_neutral.py` 扫描拓扑代码中的字符串常量，出现家族或厂商名即失败。豁免清单只列尚未完成的分支项，只能缩小。
- 现状与后续：第 1–3 步是 App 内的数据文件，修改后要重新部署；第 4 步在规则治理里发布即可生效。下一步可以把第 1–3 步也移入规则治理，作为可发布的资产，这样开局接入新家族就不需要部署。
