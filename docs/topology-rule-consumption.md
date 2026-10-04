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

**清单**（★ 为必填 Value，其余为可选）

| 检查项 | 一行代表 | Value |
|---|---|---|
| `topology.device_version` | 设备 | ★VENDOR ★SOFTWARE_VERSION, MODEL, HOSTNAME, SOFTWARE_IMAGE, PLATFORM_EVIDENCE |
| `topology.lldp_local` | 本机 LLDP 身份/端口管理地址 | ★CHASSIS_ID, CHASSIS_ID_TYPE, SYSTEM_NAME, LOCAL_INTERFACE, MGMT_ADDRESS |
| `topology.lldp_neighbors` | 一个 LLDP 邻居 | ★LOCAL_INTERFACE ★CHASSIS_ID ★NEIGHBOR_PORT_ID, CHASSIS_ID_TYPE, PORT_ID_TYPE, NEIGHBOR_NAME, SYSTEM_DESCRIPTION, MGMT_ADDRESS(List) |
| `topology.interfaces` | 接口 | ★INTERFACE ★LINK_STATUS, PROTOCOL_STATUS, ADMIN_STATUS, DESCRIPTION |
| `topology.ip_interfaces` | 接口地址 | ★INTERFACE ★IP_ADDRESS ★PREFIX_LENGTH, VRF, STATUS |
| `topology.arp` ✅ | ARP 表项 | ★IP_ADDRESS ★MAC_ADDRESS, INTERFACE, VLAN_ID(List，VLAN 或 VSI 名，折行分段), AGE, TYPE |
| `topology.mac` | MAC 表项 | ★MAC_ADDRESS ★VLAN_ID ★INTERFACE, TYPE |
| `topology.vlans` | VLAN | ★VLAN_ID, NAME, STATUS |
| `topology.trunks` | Trunk 口 | ★INTERFACE, MODE, NATIVE_VLAN, ALLOWED_VLANS, ACTIVE_VLANS, FORWARDING_VLANS |
| `topology.switchports` | 二层口 | ★INTERFACE, MODE, ACCESS_VLAN, NATIVE_VLAN, TRUNKING_VLANS |
| `topology.lag_members` | 聚合成员 | ★BUNDLE ★MEMBER_INTERFACE, PROTOCOL, MEMBER_FLAGS, BUNDLE_STATUS |
| `topology.stp_ports` | 生成树端口 | ★INSTANCE ★INTERFACE ★ROLE ★STATE, COST, PRIORITY |
| `topology.vrrp` | VRRP 组 | ★INTERFACE ★VRID ★STATE, PRIORITY, VIRTUAL_IP(List), MASTER_ADDRESS, ADVERTISE_INTERVAL |
| `topology.ospf_process` | OSPF 进程 | ★PROCESS_ID ★ROUTER_ID, VRF |
| `topology.ospf_interfaces` | OSPF 接口 | ★PROCESS_ID ★INTERFACE ★AREA, IP_ADDRESS, PREFIX_LENGTH, COST, STATE |
| `topology.ospf_neighbors` | OSPF 邻居 | ★NEIGHBOR_ID ★NEIGHBOR_ADDRESS ★STATE ★INTERFACE, PRIORITY, ROLE, DEAD_TIME, PROCESS_ID, AREA |
| `topology.bgp_ipv4_peers` | IPv4 单播 BGP 邻居 | ★LOCAL_AS ★PEER ★PEER_AS ★STATE, ROUTER_ID, UP_DOWN, PREFIXES_RECEIVED |
| `topology.bgp_vpnv4_peers` | VPNv4 BGP 邻居 | 同上 |
| `topology.bgp_evpn_peers` | EVPN BGP 邻居 | 同上 |
| `topology.routes` ✅ | 路由（每个下一跳一行） | ★NETWORK ★PREFIX_LENGTH ★PROTOCOL, TYPE, DISTANCE, METRIC, NEXTHOP_IP, NEXTHOP_IF, NEXTHOP_VRF, VRF, UPTIME, FLAG |
| `topology.vrfs` ✅ | VRF/VPN 实例（汇总为实例列表） | ★NAME, RD, PROTOCOLS, INTERFACES(List), CREATED |
| `topology.vrf_detail` ✅ | VRF × 地址族 | ★VRF ★ADDRESS_FAMILY, RD, INTERFACES(List), IMPORT_RT(List), EXPORT_RT(List) |
| `topology.ldp_peers` ✅ | LDP 会话 | ★PEER_LSR_ID, LABEL_SPACE, STATE, ROLE, LOCAL_LSR_ID, LOCAL_LABEL_SPACE, DISCOVERY_INTERFACE(List), DISCOVERY_ADDRESS(List), ADDRESSES(List) |
| `topology.mpls_lfib` ✅ | 标签转发表项 | ★FEC ★LOCAL_LABEL ★OUT_LABEL, INTERFACE, NEXTHOP, VRF, PROTOCOL, BYTES |
| `topology.evpn_instances` ✅ | EVPN 实例（IOS × VLAN） | ★INSTANCE, SERVICE, RD, IMPORT_RT(List), EXPORT_RT(List), INSTANCE_STATE, ENCAPSULATION, VLAN_ID, STATE, VNI, L3_VNI, VTEP_IP, VRF, GATEWAY_INTERFACE, CORE_INTERFACE, ACCESS_INTERFACE(List), SERVICE_INSTANCE(List), PEER(List) |
| `topology.evpn_peers` ✅ | 每 VNI 的远端 VTEP | ★VNI ★PEER, TYPE, STATE, INSTANCE, RD, INTERFACE, ROUTER_MAC, EVNI, FLAGS, UPTIME, TUNNEL_ADDRESS, ENCAPSULATION |
| `topology.vxlan_vnis` ✅ | VNI 绑定 / VSI | ★VNI, INSTANCE, L3_VNI, INTERFACE, MULTICAST_GROUP, STATE, MODE, SEGMENT_TYPE, SEGMENT, CONFIG, VRF, GATEWAY_INTERFACE, TUNNEL(List), TUNNEL_STATE(List), ACCESS_INTERFACE(List), ACCESS_STATE(List) |
| `topology.tunnel_interfaces` ✅ | 隧道接口（Comware） | ★INTERFACE, STATE, PROTOCOL_STATE, SOURCE, DESTINATION, MODE |
| `topology.evpn_mac` ✅ | EVPN MAC 路由 | ★MAC_ADDRESS ★INSTANCE, VLAN_ID, ESI, ETHER_TAG, NEXTHOP_VTEP, NEXTHOP_INTERFACE, SERVICE_INSTANCE, FLAGS, LOCAL_FLAG, LINK, ENCAPSULATION |

✅ 表示已接入（IOS/IOS-XE 与 Comware 7）。

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

- **切片 1**：`topology.arp`、`topology.routes`、`topology.vrf_detail`、`topology.evpn_peers`。
- **切片 2**：`topology.ldp_peers`、`topology.mpls_lfib`、`topology.evpn_instances`、`topology.vxlan_vnis`、`topology.tunnel_interfaces`、`topology.evpn_mac`、`topology.vrfs`。
- 以上在 IOS/IOS-XE 与 Comware 7 上经规则治理采集和解析：
  - 采集档案里这些能力只写 `parser: rules`，命令由已发布的实现给出；
  - 每项迁移前都用全部实验网夹具比对过新旧结果，事实完全一致后才删除对应的手写解析器和模板层资产；
  - NX-OS 等其余家族暂时仍走原通用解析器（例如 NX-OS 的 VRF 列表与 ARP）。
- **拓扑侧的消费规则**（`collectors/rules.py`）：
  - 按行使用的表剔除坏行；路由表整表使用（`WHOLE_TABLES`）；
  - VRF 列表汇总为一个事实（`SUMMARIES`）；
  - 一张表里同一实体只能出现一次（`UNIQUE`：VRF、LDP 对端、隧道接口），重复即剔除并记 `duplicate_entry`；
  - 输出被截断且一行未读到时为 failed，不把它当成空表。
- **待做**：
  - 其余事实逐项迁移：BGP（三张邻居表共用模板）、OSPF、接口、IP、LLDP、设备版本、MAC、VLAN、Trunk、二层口、聚合、生成树、VRRP；
  - 拓扑去掉对 `src.core.parser.*` 转发的引用（模板层 `tiers/template.py`、`parse.py` 的输出分类）；
  - 系统模板登记到规则治理的模板库（数据库），让页面可见、可评审；目前模板文件由资产清单钉住摘要；
  - 巡检经同一接口消费，另开卡。
