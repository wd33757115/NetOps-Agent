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

**清单**（由 `system_check_items.yaml` 生成；共 29 项，全部已在 IOS/IOS-XE 与 Comware 7 上接入）

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

说明：

- **字段类型**：Value 的类型（text、interface、ipv4、mac、integer、label、route_target）写在契约里。拓扑只按类型做统一规范化，例如接口名补全、MAC 统一格式、`Pop Label` 转为 `implicit-null`，不出现按厂商的分支。
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
- **待做**：
  - 系统模板登记到规则治理的模板库（数据库），让页面可见、可评审；目前模板文件由资产清单按 sha256 钉住；
  - NX-OS 等家族在有实机样本后补实现，届时删掉对应的通用解析器；
  - 巡检经同一接口消费，另开卡（已在独立会话进行）。
