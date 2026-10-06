# 云端任务：PBR 按 OpenConfig 模型实现（含文档、单测）

先读 [公共约定](README.md)。分支：`cloud/pbr-openconfig`。

## 1. 负责人的决定

- **PBR 用 OpenConfig 的 `openconfig-policy-forwarding`。** IETF 和 IEEE 都没有 PBR 标准模型，所以用这个公开模型。要固定版本、纳入来源锁和校验；不增加项目自定义 YANG。ACL 用 RFC 8519，NAT 用 RFC 8512。（[实施文档 §13](../network-digital-twin-implementation.md) 第 3 条）
- **走统一流水线**：原始响应 → 标准数据树（RFC 7951 JSON，校验；不完整的标为 partial） → 从标准树抽取查询表 → 路径分析读查询表。（§13 第 1 条，负责人 2026-10-06 确认）PBR 是第一个完整走这条路的领域，要把这几步做成以后 ACL、NAT 和 CLI 采集也能照着改的样子，但**本任务不改造 ACL 和 NAT**。
- **路径上的处理顺序**：入接口 → 入方向 ACL → **PBR** → NAT → 路由 → 出方向 ACL → ARP/ND → 二层转发。
- **只对 H3C 设备做测试**：本任务只覆盖 H3C Comware 7，CLI 和 NETCONF 都要做。Cisco IOS 维持现状，只采集绑定、只打"可能改变走向"的标记，不采集 route-map 的内容。

## 2. 现状

| 部分 | 现状 |
|---|---|
| 采集 | 只有绑定，即检查项 `topology.pbr_bindings`（接口、策略名、类型），见 `src/apps/rule_governance/backend/check_items/assets/system_check_items.yaml`。<br>Comware 的 CLI 是 `display ip policy-based-route setup`；NETCONF 定义是 `h3c-comware7.pbr-bindings`（`src/apps/netconf_api/backend/seeds/h3c_comware7.yaml`）。**策略内容**（节点、匹配、动作）**还没有采集** |
| 路径 | `src/apps/topology/backend/model/path.py`：入接口上有 PBR 绑定时打 `policy_may_redirect` 标记，不计算；设备没采到 `pbr_bindings` 时记 `policy_not_computed`（`COMPUTED_TABLES`） |
| 标准树 | ACL 已能导出 RFC 8519（`model/device_acl.py`，可以参照）；NAT 和 PBR 还没有 |
| 实验室 | BR1 上有一条样例策略，配置见 `scripts/topology/campus_wan/configs/BR1.txt`：<br>`policy-based-route TEST-PBR permit node 10`：`if-match acl 3992`、`apply next-hop 10.0.13.2`；<br>`policy-based-route TEST-PBR deny node 20`：`if-match acl 3993`；<br>接口 `GigabitEthernet5/0` 上 `ip policy-based-route TEST-PBR`，另有 `ip local policy-based-route TEST-PBR`（本机发出的报文） |
| 样本 | `tests/fixtures/netconf/h3c-comware7/br1-policy/`：<br>`pbr.xml`（NETCONF PBR 模块回包）、`cli-display-ip-policy-based-route.txt`（策略内容）、`cli-display-ip-policy-based-route-setup.txt`（绑定）、`acl.xml` 和 `cli-display-acl-all.txt`（其中 ACL 3992：`permit ip source 192.0.2.0 0.0.0.255 destination 203.0.113.0 0.0.0.255`；ACL 3993：`permit ip source 192.0.2.64 0.0.0.63`）。<br>`tests/fixtures/topology/campus-wan/policies/` 里有 BR1/CORE1 的绑定样本 |

**可以参照的提交**（`git show <提交>`）：
- `98db2b8`：ACL 阶段，ACL 逐跳计算；
- `9eea8c0`：RFC 8519 导出；
- `67049c6`：NAT 阶段，演示了新增检查项、TextFSM 模板（钉 sha256）、夹具、采集、路径计算，以及要提升哪些版本号；
- `9c4d7e5`：NETCONF 的 ACL/NAT/PBR 定义；
- `e4b16f9`：`policy_not_computed`。

## 3. 要做的事

1. **模型来源**：从 openconfig/public 固定一个提交，取 `openconfig-policy-forwarding` 及其全部依赖，记入来源锁（URL、SHA-256），格式参照 `models/network-twin/additional-sources.lock.json`。用 pyang 2.7.1 / yangson 1.6.3 编译通过，在 `models/network-twin/m5/README.md` 里登记。不修改源码，不自造模块。
2. **采集策略内容（Comware）**：
   - 在规则治理新增一个系统检查项，例如 `topology.pbr_rules`，表示每个策略节点一行（策略名、节点号、permit/deny、匹配条件、动作），值名和含义自己按样本设计。
   - 配一个 Comware 的 TextFSM 模板，放进模板库，钉住 sha256。
   - 在 netconf-api 种子里加对应的 NETCONF 定义，用现有的 `mapping.py` 能力；确实需要扩展的话，要配测试。
   - 用同一份样本时，CLI 和 NETCONF 产出的值行必须一致。
3. **标准树**：把策略内容和绑定投影成 `openconfig-policy-forwarding` 的 RFC 7951 JSON（在对应 network-instance 下），用钉住的模块校验。表达不了的部分标 partial，并写明原因。
4. **查询表**：从标准树抽出路径分析要用的表（按网络实例和接口：有序的规则、匹配、动作），字段沿用标准节点的名称。
5. **路径计算**：
   - 在入方向 ACL 之后、NAT 和路由之前计算 PBR。
   - 命中 permit 节点并且有下一跳时，按这个下一跳转发，下一跳的解析和路由一致。
   - 命中 deny 节点或没有命中任何节点时，走正常路由。
   - 本机 PBR 只作用于设备自己发出的报文。
   - 能算出来的地方，用计算结果替换 `policy_may_redirect`；算不出来的（例如 IOS 没有采集内容）继续保留标记。
   - 跳上要说明命中了哪个策略的哪个节点，这一点参照 ACL 拒绝时带 entry 的做法。
6. **文档**：
   - 更新 `docs/topology-rule-consumption.md`（策略相关的章节）；
   - 更新 `models/network-twin/m5/README.md`；
   - 在实施文档 §13 后补一段进展说明；
   - 写一份简短的设计说明，讲清这次引入的"标准树 → 查询表"两步怎么组织，以后 ACL、NAT 怎么照着改。

## 4. 必须先查证再定的建模问题

**不能猜。** 结论要附来源：OpenConfig 模块里的 description，以及 H3C 的官方文档。
- **按 ACL 匹配**：H3C 的节点是 `if-match acl 3992`，也就是引用一个 ACL；而 OpenConfig policy-forwarding 的规则是直接匹配报文字段（ipv4、transport 等），怎么表达要查证。可选做法有两种：把 ACL 条目展开成多条规则；或者表达不了就标 partial。选哪种、为什么，要写进文档。
- **deny 节点**：H3C 上 deny 节点的语义是"不做策略转发，走正常路由"，在 OpenConfig 里怎么表达。
- **下一跳不可达**：H3C 的默认行为（是否回退到正常路由），查证后写进实现，并作为缺口或假设标明。
- **本机 PBR**：OpenConfig 有没有对应的位置；没有的话怎么处理。

## 5. 验收（用 BR1 样本）

- **CLI 和 NETCONF 一致**：`cli-display-ip-policy-based-route.txt` 解析出两行：TEST-PBR 节点 10（permit，匹配 ACL 3992，下一跳 10.0.13.2）、节点 20（deny，匹配 ACL 3993）。`pbr.xml` 映射出的值行与之相同。
- **标准树**：通过钉住模块的校验；partial 的部分写明原因。
- **路径计算**（从 BR1 的 `GigabitEthernet5/0` 进入，可以参照现有路径测试构造设备数据）：
  - 源 192.0.2.10、目的 203.0.113.5：命中节点 10，从 10.0.13.2 转发，与路由表无关；跳上写明 TEST-PBR 节点 10。
  - 源 192.0.2.70、目的 203.0.113.5：先遇到节点 10，ACL 3992 也命中（192.0.2.70 在 192.0.2.0/24 内），所以仍然命中节点 10。这个用例检验的是"按节点顺序、第一个命中的生效"。另外构造一个只命中 3993、不命中 3992 的报文，例如目的不在 203.0.113.0/24，验证它命中节点 20（deny），走正常路由。
  - 不命中任何节点的报文走正常路由。
  - 没采到策略内容的设备，继续标 `policy_may_redirect` 或 `policy_not_computed`。
- **回归**：现有拓扑、netconf-api、规则治理的相关测试都通过（和改动前对比，排除原本就失败的）；"采集代码不含厂商名"的守护测试通过。
- **版本号**：提升 topology、rule-governance（以及它的 `bootstrap_version`，如果系统检查项有变化）、netconf-api（种子有变化时还有它的 `bootstrap_version`），具体参照 `67049c6`。

## 6. 不在本任务内

- 把 ACL、NAT 和其他 CLI 采集改造成标准树流水线（以后单独的任务）。
- Cisco IOS 的 route-map 内容。
- 在线采集、部署、在 BR1 上实测（留给本地会话：合并后执行 `start.ps1 -UpgradeApps rule-governance,netconf-api,topology`，在 BR1 上用 CLI 和 NETCONF 各采一次，再用路径工具核对上面几个用例）。
