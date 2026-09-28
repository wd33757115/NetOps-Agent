# 网络孪生原生模型验证包

这是可离线构建的模型与实例验证包，尚未接入 App、数据库或设备。模块结构来自固定标准/草案；实例名称、文件组织、构建摘要和测试报告是验证工具资料，不是新增网络模型实体或字段。

## 构建与验证

使用独立研究环境（pyang 2.7.1、yangson 1.6.3）：

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_model_package.py
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py
```

构建默认离线读取 `.runtime/ietf-study/reproducible-audit/sources`，缓存缺失时加 `--fetch` 下载并校验固定来源。可用 `--cache` 和 `--output` 指定空目录复现。基础来源锁在 `docs/ietf-model-audit/sources.lock.json`，OSPF 及其依赖在本目录 `additional-sources.lock.json`。原文审计因为 SIMAP 原文错误仍退出 1，兼容包的结果单独记录。研究依赖固定为 pyang 2.7.1、yangson 1.6.3；不安装到应用环境。

输出 `.runtime/ietf-study/model-package/` 包含 45 个完整模块/子模块（IETF-1.8 起；此前为 41 个）、独立保留的 SIMAP 原文、校验清单、两个验证用 YANG Library、展开树和实例结果。不裁剪模块源码。profile 只选择本次实例验证的模块和 feature，不是宣告所有设备或应用支持这些能力；未来模块仍需按来源、版本及约束纳入。固定的文件清单及本轮结果在 `reports/`，不把下载缓存提交进仓库。

## 实验场景的模型覆盖（IETF-1.8）

按路线图 11.0 的迷你北极星实验及故障集 v0 列出所需数据的原生落点。新增的 IEEE 802.1AB LLDP（2022-03-15）、LLDP 类型、IEEE 802.1Q Bridge（2023-10-26）、ieee802-types（2023-10-22）均取自已固定的 YangModels 提交 `0e28ed86`，锁在 `additional-sources.lock.json`。device profile 同时加入包内已有的 RFC 8519 ACL。“已建模”只表示模块完整编译、可装载；数据映射单独列出。

| 场景数据 | 原生模块与路径 | 状态 |
|---|---|---|
| 拓扑节点/TP/链路 | RFC 8345 + SIMAP -03 + RFC 8944/8346：`N/D/T/L` | 已建模；LLDP 邻接已映射（IETF-2.1） |
| 设备清单（虚拟 NE、软件版本） | inventory -19 + software -04：`network-element[ne-id]` | 已建模并映射；序列号的组件归属未闭合 |
| 接口、IP 地址 | RFC 8343/8344：`/if:interfaces/interface`、`ip:ipv4/address` | 已建模；部分映射（缺计数器中断时间） |
| ARP | RFC 8344：`ip:ipv4/neighbor[ip]/link-layer-address`（operational） | **已映射（IETF-2.2）**：每台 4 条；VLAN 终端按唯一包含其地址的三层接口子网归到 Vlan 接口，物理口留在溯源清单 |
| LLDP 本地/远端 | IEEE 802.1AB：`/lldp/local-system-data`、`/lldp/port[name dest-mac-address]/remote-systems-data[time-mark remote-index]` | 本地系统与 3 个端口的 nearest-bridge 代理**已映射**；远端表主键 time-mark 是变化时的 sysUpTime。CLI 的 “Update time” 超过 121 秒 TTL，不是收包间隔；但它是“变化时的开机时长”还是“距变化的时长”尚未核实，**不映射**（邻接仍在拓扑实例中） |
| access 口 VLAN、MAC 表 | IEEE 802.1Q：接口 `dot1q:bridge-port/{bridge-name,component-name,pvid}`；`component/bridge-vlan/vlan[vid]`；`filtering-database/filtering-entry[database-id vids address]/port-map[port-ref]` | PVID、VLAN 及成员**已映射**（pvid 的 when 要求引用非 D-bridge 组件）；MAC 表需要 database-id 和桥端口号，CLI 只有接口名，**不映射** |
| RIB、OSPF、静态/黑洞路由 | RFC 8349 + RFC 9129：`ribs/rib/routes/route`、special-next-hop | 已建模；RIB 子集已映射 |
| VRF/NI 与挂载 | RFC 8529 + RFC 8528 | 已建模；libyang 验证；实验无 VPN 实例 |
| ACL 与接口挂接 | RFC 8519：`/acls/acl`、`/acls/attachment-points/interface[interface-id]` | **本轮加入 profile**；未映射 |
| VLAN 接口（SVI）与 VLAN 的绑定 | 固定模块中未找到 Vlan-interface↔VID 的标准节点；iana-if-type 只给出接口类型 | 缺口，不自定义 |
| Hub、不可管理交换机、PC | 无采集证据；拓扑只能表示可观测对象 | 不建模推断实体（见 6.4 撤回的假设） |
| 拓扑 node ↔ 清单 ↔ 设备树 | 无当前标准引用 | 缺口，见 [virtual-association](virtual-association/README.md)，负责人授权自定：模型内不建立关联 |

**模型要求而当前采集缺少的证据**（供之后重排采集任务使用）：接口 `statistics/discontinuity-time`；bridge `address`；LLDP “Update time” 的语义（间隔采两次即可区分）；FDB 的 database-id 与桥端口号（接口名到桥端口号的对应，样本中 LLDP 端口号 17/33/49 与 ifIndex 一致，但不等于已证明是 802.1Q 桥端口号）；LLDP 其他代理和管理地址 TLV。路由出接口在样本中已有，是映射时因 NULL0/InLoop0 等接口类型未核定而暂缓，不属于采集缺口。SVI↔VID 的绑定属于模型缺口，不是采集缺口。

TE（RFC 8795）、BGP 等不在当前实验场景内，不纳入本轮，避免过度建模；出现对应场景时再按同一方式加入。

## SIMAP 兼容补丁

`patches/simap-03-syntax.patch` 是可审查差异；同名 JSON 用精确上下文及输入/输出 SHA-256 约束自动应用。构建同时检查二者一致。

改动仅限：补声明已使用的 yang/inet import；修复 hybrid description 提前闭合的引号；闭合 linked-termination-point 和 linked-node 的 list。新增 import 使用包内固定 RFC 9911 类型。保留 namespace 与原文 revision，并通过包清单和补丁摘要明确这是本地工作副本，不能与未经修改的同 revision 原文混同。

没有补入 §7 的四项 feature，没有修改 network-category-none 的继承，没有增加 default/mandatory/when/must，没有补写虚拟设备映射或外部请求格式。无效原文无法进行完整 AST 等价证明；这里只能核验精确改动范围、原文描述依据和修复后约束表现，不能声称形式化证明了语义等价。

## 数据作用域

- topology profile：全网拓扑、SIMAP、L2/L3、inventory 及外部关联定义。
- device profile：单个设备的接口、IP、网络实例和路由；两台 VSR 的同名接口分别放在各自数据树，不能合并成一个全网 interfaces 列表。
- 目前使用 RFC 7895 形式的验证库供 Yangson 装载，不代表实现了服务器的 RFC 8525 接口。
- RFC 8528 挂载需要运行时装载及 parent-reference 处理。当前 Yangson 会拒绝直接塞进 vrf-root 的路由树；这个反例明确记录工具边界，不删除子树假装 mount 已通过。

续轮已用 libyang 5.8.6 完成设备内配置级挂载的 8 个正反例，见 [挂载验证](mount/README.md)。它不会改变上述 Yangson 的工具边界。

续轮又完成 21 项运行状态/部分观测检查：完整合成状态树挂载通过，VSR 部分接口树可无改写地解析，但严格完整性检查仍失败。详见同一挂载说明及 `reports/operational-validation.json`。运行状态允许的条件性语义例外与 CLI 缺证据必须分开，parse-only 不证明引用已解析。

完整模型仍有未闭合项：虚拟 topology node→inventory/device 的标准关联、SIMAP 原文语义缺口、生产挂载服务及设备全量 operational 验收、全部 TE/协议扩展。缺口不以项目新字段填补。

基础路由模型续查见 [RFC 8349 路由语义核验](routing-semantics.md)：39 项 IPv4/IPv6、配置/状态和下一跳用例。Yangson 的 2 项内部异常保留，并由 libyang 正负对照复核；多下一跳及 Schema 接受不等于转发路径已证明。

## 实验实例和验证边界

`instances/` 包含 8 个原生 RFC 7951 实例文件及独立工程溯源清单。清单不送入 YANG 数据树；其中没有认证信息。`build_ietf_lab_instances.py` 只针对已审查的固定批次进行离线映射，先检查所有原始/清理后文件的摘要，再生成实例。它不是通用 CLI 解析器，也不接入生产采集流程。`--check` 比较已提交实例与重建结果，省略时重新生成。

| 实例 | 映射范围 | 验证结论 |
|---|---|---|
| `topology-observed.json` | 2 节点、4 个节点内 TP、4 条有向 LLDP 邻接；使用原生 L2 属性和 SIMAP live 类别 | Schema 通过；邻接不证明直连物理线缆，不据此制造 Hub/交换机实体 |
| `inventory.json` | 2 个 ne-virtual、软件版本及名称 | Schema 通过；虚拟属性依据 GNS3 实验部署。同名 H3C 不合并，未虚构硬件组件承载序列号 |
| `vsr-1-routes.json` / `vsr-2-routes.json` | 18 / 19 条路由：目的前缀、协议类别、优先级、下一跳地址 | 原生 IPv4 unicast RIB 子集通过；保留 0.0.0.0/32 和特殊地址行，不擅改成默认路由或丢弃动作 |
| `vsr-1-interfaces.partial.json` / `vsr-2-interfaces.partial.json` | 各 6 条 IP 接口行的类型、状态、地址/掩码 | **严格完整性检查失败**：缺 statistics/discontinuity-time；部分解析通过并逐字段保留，不能称完整 operational datastore |
| `vsr-1-l2.partial.json` / `vsr-2-l2.partial.json` | 同一设备树的另一子集：GE1/0～GE3/0 与 Vlan 接口、4 条 ARP、GE3/0 access PVID、VLAN 1 与业务 VLAN 成员、LLDP 本地系统及 3 个端口 | Yangson 首个诊断为缺 statistics；libyang 15 项检查（`l2-cases/`、`reports/l2-validation.json`）：严格模式的缺项正好是 4 个计数器中断时间和 bridge address（另有工具带出的 yang-library 必填项）；operational 模式解析 LLDP/VLAN 成员接口引用并通过；parse-only 往返一致；7 个反例（未知接口引用 ×2、PVID 越界、VLAN 接口挂 bridge-port、MAC 格式 ×2、自定义字段）按预期拒绝 |

二层子集的 libyang 复现（需验证镜像）：

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_l2_cases.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_mount.py --cases models/network-twin/l2-cases --output models/network-twin/reports/l2-validation.json
```

接口必填要求见 [RFC 8343 §5](https://www.rfc-editor.org/rfc/rfc8343.html#section-5)。不能用采集时间或估算开机时间代替计数器中断时间，也不因缺值修改 mandatory。路由字段及 address-family 约束依据 [RFC 8349 §7](https://www.rfc-editor.org/rfc/rfc8349.html#section-7)，OSPF 身份依据 [RFC 9129 §3](https://www.rfc-editor.org/rfc/rfc9129.html#section-3)。O_INTRA 只能在当前证据下映射到 ospf 基类；不推定版本、进程或 area-id。成本和出口原值保留在独立溯源清单，未借标准字段塞入自定义含义。

路由实例的 RIB 名是验证投影标识，不是设备原生 YANG 名。为分离路由子集与不完整接口树，未填 outgoing-interface；不能据此进行完整转发路径计算。L2 TP 的 interface-name 是显示名称，不会自动解析到另一个文件中的接口；同名 node-id/ne-id 也不构成标准关联。无 VPN 实例时不人为增加默认 NI。

验证脚本现含 26 个正反例/实例结果。负例必须命中预期诊断；“预期匹配”不等于“所有实例有效”。报告明确保留接口缺失、挂载拒绝及 Schema 未强制的语义要求。关联边界见 `associations.md`。
