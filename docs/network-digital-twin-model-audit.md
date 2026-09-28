# 原生模型核验清单：RFC 8345、SIMAP 与配套模型

核验日期：2026-09-28。本文记录定义、约束和可复现验证，不新增网络数据模型。采纳范围与实施顺序遵守路线图第 0 节；“核验完成”表示已查明该项结果，不表示草案模块一定通过或应用已经实现。

## 1. 核验基线

| 来源 | 本次使用方式 |
|---|---|
| [RFC 8345 §6](https://www.rfc-editor.org/rfc/rfc8345.html#section-6) | `ietf-network`、`ietf-network-topology`，revision 均为 `2018-02-26`；依赖 `ietf-inet-types@2013-07-15`。 |
| [SIMAP YANG -03](https://datatracker.ietf.org/doc/html/draft-havel-nmop-simap-yang-03) | 已采纳；§7 提议、§8 树图、§9 模块分别核对。§9 的模块为 `ietf-simap-topology`，revision `2025-01-01`，不可用草案发布日期替换。 |
| [SIMAP concept -13](https://datatracker.ietf.org/doc/html/draft-ietf-nmop-simap-concept-13#section-4) | §4 共 36 项需求，下面逐项登记；需求文档不等于可直接部署的 Schema。 |
| [SIMAP concept -12](https://datatracker.ietf.org/doc/html/draft-ietf-nmop-simap-concept-12) | YANG -03 实际引用的是 -12。比较 -12/-13 的 §4 后，需求标识集合相同；-13 明确写操作也用于记录无法发现的拓扑，并澄清不能通过写 SIMAP 直接改变真实网络。不得宣称 -03 已验证兼容全部 -13。 |

路径缩写仅用于本文：`N=/nw:networks/nw:network`，`D=N/nw:node`，`T=D/nt:termination-point`，`L=N/nt:link`。`nw`、`nt`、`st` 分别是原文 network、network-topology、simap-topology 前缀；JSON 使用模块名，而不是这些缩写前缀。

## 2. RFC 8345 基础结构与约束

以下包括两个基础模块的全部实例节点类别；typedef、grouping 的重用关系一起列出。两个模块无 feature、choice、when/must 或显式 default；实例唯一性仍由 list key 约束。未声明 config 的节点继承默认 `true`，不能据此推导“采集到的拓扑允许用户随意改写”。

| 原生路径 / grouping | 标识、类型及引用 | 语义和实施要求 |
|---|---|---|
| `/nw:networks`、`N/network-id` | network key `network-id`；`nw:network-id` → `inet:uri` | 容器承载网络列表；同一真实网络跨 datastore 的标识应稳定。 |
| `N/network-types` | 空 augment 目标；技术模块加入 presence container | 不是预先定义 physical/l2/l3 的枚举，也不是只能选择一个类型。 |
| `N/supporting-network[network-ref]` | 引用 network-id；`require-instance false` | 网络的支撑/下层关系，不是父子分区关系。 |
| `D/node-id` | node key `node-id`；`nw:node-id` → `inet:uri` | 在所属 network 内唯一；同名节点跨网络不自动合并。 |
| `D/supporting-node[network-ref node-ref]` | network-ref 指向该网络 supporting-network；node-ref 是 leafref；均允许未解析引用 | 原文描述要求支撑节点属于相应下层网络；Schema 可通过不等于所有关系语义已被校验。 |
| `L/link-id` | link key `link-id`；`nt:link-id` → `inet:uri` | 基础 link 是有向点到点；允许同一对端点存在多个不同 link。 |
| `L/source/{source-node,source-tp}` | source-node 在当前网络；source-tp 通过 source-node 限定；均为允许未解析的 leafref | 两个 leaf 没有 mandatory；缺失时不能补造端点。 |
| `L/destination/{dest-node,dest-tp}` | 与 source 对称，限定当前网络 | 基础端点不能靠拼接 ID 冒充跨网络引用。 |
| `L/supporting-link[network-ref link-ref]` | network-ref 指 supporting-network；link-ref 用 network-ref 限定下层 link；允许未解析 | 原文明确禁止直接或间接自支撑环，但没有对应 must；须另做语义核验。 |
| `T/tp-id` | TP key `tp-id`；`nt:tp-id` → `inet:uri` | 在 node 内唯一；不是每个 TP 都等于物理端口。 |
| `T/supporting-termination-point[network-ref node-ref tp-ref]` | network/node 引用 supporting-node；tp-ref 按 network/node 限定；允许未解析 | 原文描述此信息由系统提供、可由链路依赖推导；代码没有显式 config false，须保留这种代码/描述差别，不能改源码掩盖。 |
| `nw:network-ref`、`nw:node-ref` | node-ref grouping 重用 network-ref，以 network-ref 限定 node | 全网引用由组合构成，不能只存裸 node-id 后任意解析。 |
| `nt:link-ref`、`nt:tp-ref` | link-ref 重用 network-ref；tp-ref 重用 node-ref | 专为扩展模块重用的引用结构；其 leafref 均允许未解析目标。 |

## 3. SIMAP 需求到定义的对应表

“代码有定义”仅表示 §9 文本存在该定义；当前 §9 原文尚不能通过编译，不能解释为可投入生产。完整性核验覆盖 concept -13 的全部 **36** 个需求标识。

| concept -13 需求 | RFC / YANG -03 对应位置 | 原生路径或责任边界 / 本次结论 |
|---|---|---|
| REQ-BASIC-MODEL-SUPPORT | RFC 8345；§4.2 | `N/D/T/L`，基础结构已定义。 |
| REQ-LAYERED-MODEL | RFC 8345；§4.2 | `network-types`、四类 supporting；具体层属性由配套模块定义。 |
| REQ-VIEWPOINTS | §4.2 | 网络实例与视图组织；不是新增 viewpoint 字段的依据。 |
| REQ-PASSIVE-TOPO | §4.2；concept §4.1 | 基础对象可表示被动拓扑；不可发现部分需人工提供或标准外部关联，不能由 LLDP 缺失自动推断。 |
| REQ-PROG-OPEN-MODEL | §6、§7.4.1 | 读写接口与生命周期；模拟和真实状态必须隔离，写拓扑不等于对设备下发配置。 |
| REQ-STD-API-BASED | §7.5.1–7.5.2 | 外部模型连接另引 external-relationship 草案；§9 无相应外部关联结构。 |
| REQ-COMMON-API | §4.2、§6 | 跨域使用共同模型/API；不是每个厂商建立一种核心拓扑 Schema。 |
| REQ-GRAPH-TRAVERSAL | §7.3.1、§9 | `T/st:linked-termination-point`、`D/st:linked-node`，config false；§8 树图缺这两项，§9 对应 augment 还缺闭合。 |
| REQ-TOPOLOGY-ABSTRACTION | §4.4 | 仍待研究；不能把 supporting 自动解释成所有抽象级别关系。 |
| REQ-LIVE | §4.2、§6 | 真实网络当前视图；时间戳提议未完整定案，不能自行增加 created/updated/deleted 字段。 |
| REQ-SNAPSHOT | §7.4.2、§9 | `/st:networks-history/network-history[live-network-ref timestamp]`，只读，`file-id` 为 URI；不表示已有历史查询服务。 |
| REQ-POTENTIAL | §7.4.3、§9 | `N/st:network-category`、`st:live-network-ref`、`st:timestamp`；描述要求与机器约束有差别，见下节。 |
| REQ-INTENDED | §7.4.4、§9 | 同上，category 的 intended identity；不能直接等同 NMDA intended datastore。 |
| REQ-SEMANTIC | §7.2.8 | 角色、关系属性、子层与路径等仍待研究；不可新增 core/backup/hub 等项目 identity。 |
| REQ-LAYER-NAVIGATE | §4.2 | 基础 supporting 和端点引用；具体技术层内实体仍需技术模块。 |
| REQ-EXTENSIBLE | §7.2.2、§9 | 四类对象 `st:extension` 为只读 anydata；保留原生入口，本项目不在其中注入自定义内容。 |
| REQ-PLUGG | §7.5.2 | 指向 `draft-vivek-simap-external-relationship-00`；§9 没有该模型，需单独核验。 |
| REQ-BIDIR | §7.2.3、§9 | `L/st:link-direction` 的 uni/bi identities；保留基础 source/destination。 |
| REQ-MULTI-POINT | §7.2.4、§9 | `L/st:link-type`、`st:tp[network-ref node-ref tp-ref]`；点数约束仍是 open issue，不自行写成标准 must。 |
| REQ-MULTI-DOMAIN | §7.2.5–7.2.6 | link 的 st:tp 可指跨网络 TP；共享 node 的方案仍待研究，不能仅靠统一 node-id 宣称已解决。 |
| REQ-SUBNETWORK | §7.2.7、§9 | `N/st:subnetwork[network-ref]`；§9 采用父指向子，`parent-ref` 仅在另一候选中，不能两种混装。 |
| REQ-SUPPORTING | §7.2.9 | 不同实体类别的 supporting 扩展仍待研究；现有四类同类型 supporting 不得被任意外键替换。 |
| REQ-STATUS | §7.4.5 | down 的对象仍须出现；可使用特定层状态/外部引用。§9 未定义通用 status leaf。 |
| REQ-DATA-PLANE-FLOW | §4.2；concept §4.1 | 流与 underlay/overlay 的关联需求；不能由基础节点 ID 推导 IPFIX/VPN 上下文已经完整建模。 |
| REQ-CONTROL-PLANE | §4.2；concept §4.1 | 路由状态与对应拓扑关联；需要路由、VRF、协议及关联定义，§9 无通用 route 外键。 |
| REQ-TOPO-ONLY | §4.2；concept §4.2 | 只纳入拓扑信息，详细配置/ACL 等通过相应模型关联。 |
| REQ-PROPERTIES | §7.2.1、§9 | `st:simap-common` 应用于四类对象：name/description 为 string，label 为 string leaf-list。 |
| REQ-RELATIONSHIPS | §7.2.10 | 基础包含关系存在；其余提议待研究。 |
| REQ-CONDITIONAL | §4.1 | 查询/协议能力，§9 无条件查询 RPC；不能只加载模块就标为支持。 |
| REQ-TEMPO-HISTO | §7.4.6 | -03 标题写为 REQ-TEMPO-HISTORY；历史候选部分已写入 §9，时间/地理关联仍未完整定义。 |
| REQ-SCALES | §4.1；concept §4.3 | 服务实现与容量验收，不是模型字段。 |
| REQ-PERFORMANCE | §4.1；concept §4.3 | 增量、过滤、分页及合适的订阅机制需实现验证。 |
| REQ-USABILITY | §4.2；concept §4.3 | API 使用要求，Schema 编译不能证明满足。 |
| REQ-DISCOVERY | §4.2；concept §4.3 | 初始/按需发现属于采集与同步流程；标准不要求事先知道 LLDP 对端型号。 |
| REQ-SYNCH | §4.2；concept §4.3 | 同步流程需后续验收；存在 network 列表不代表完成同步。 |
| REQ-SECURITY | §4.1、§6；concept §4.3 | 身份认证与授权；YANG 接口参考 RFC 8341 NACM，不是 RFC 8431（-03 引言引用有误）。 |

## 4. SIMAP 原文的一致性与实现缺口

1. **可复现编译错误**：从官方 txt 的 §9 提取模块，仅去除分页页眉页脚；pyang 2.7.1 报 `link-type-hybrid` 的 description 字符串结束后出现未引用文本，退出码 1。没有修改该模块以制造通过结果。
2. **静态检查的其他问题，尚不是原文编译器验证通过的结果**：使用 `yang:date-and-time`、`inet:uri` 但只 import nw/nt；两个导航 augment 各少一个闭合括号。后续原生修订应重新核验；本轮经授权的独立兼容补丁见第 7 节，不能把这些修补冒充原文。
3. **feature 不一致**：§7.1 给出 core-topology/navigation/lifecycle/external 四项 feature，多个片段包含 if-feature；§9 没有这些 feature 或 if-feature。不能在 YANG Library 宣告 §9 已实现这四项。
4. **生命周期约束不一致**：`network-category-none` 缺少 network-category base；category 描述称默认 live，但没有 default 语句。live-network-ref 和 timestamp 的描述写 required，但没有 mandatory/when。它们不能成为已由 Schema 强制的规则；不自行补默认值。
5. **引用要求不同**：网络扩展 live-network-ref、subnetwork-ref 明示 require-instance false；历史记录的 live-network-ref 没有此声明，按 YANG 默认要求目标存在。不能对所有引用使用一种容错策略。
6. **方向、端点和角色**：link-direction、link-type、tp-direction 有原生派生 identity；tp-role 仅有 base，没有具体派生角色。tp 列表重用 nt:tp-ref，组合键是 network/node/TP；不能缩成 TP 名。§9 没有与 source/destination 的一致性 must，也没有强制多点数量。
7. **树图不能替代源码**：§8 缺导航结构；§7 的 parent-ref 候选和四项 feature 也不能视为已经合入 §9。§5 明确当前 augment 方案用于建模审查，最终承载模块方式尚未定案。

已进一步核验作者 [RFC8345Bis 仓库固定提交](https://github.com/OlgaHuawei/RFC8345Bis/tree/79b88040877b509b630a9186e458892188d6cad7)，而非因文档提取错误就停止调查：原始 `.yang` 同样在 description 处编译失败（第 249 行，退出 1）。与 -03 提取物忽略空行和行首尾空白后，仅有两处注释行不同；此提交没有可替换的已修复模块。源码摘要和诊断保存于 `ietf-model-audit/simap-upstream-report.json`。该次核验没有改变采纳版本或本地修正原生定义。

结论：**继续采纳 SIMAP 补充工作；当前冻结版本的定义核验已产出明确结果，生产可执行模型尚未通过。** 先完成配套关联和可用模块的验证。SIMAP 未定义或原文错误的部分列为待解决项，不退回自定义模型，不把参考代码静默修补后标为原生标准。

## 5. 配套关联核验

### 5.1 设备、接口、清单与路由的原生关系

| 对象/关联 | 来源及原生定义 | 已核对的约束与边界 |
|---|---|---|
| 全网 NE 清单 | [inventory -19 §5](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-yang-19#section-5)：`/nwi:network-inventory/network-elements/network-element[ne-id]` | 全树 config false；NE key 是 string，ne-type 是 identityref。基础定义 ne-physical；不能根据主机名相同归并 NE。 |
| NE 内组件与端口 | 同上：`…/components/component[component-id]`，class 必填 | component-id 在 NE 内定位；class 可以是硬件或非硬件 identity；parent 可多个，parent-rel-pos 有 when。port-ref 描述要求目标为 ianahw:port 或派生类，但没有相应 must。 |
| 物理拓扑节点 → NE | [inventory-topology -11 §5](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-topology-11#section-5)：`D/nwit:inventory-mapping-attributes/ne-ref` | when 要求 `N/network-types/nwit:inventory-topology`。presence 明确表示物理节点，描述为 1:1；未用 must 强制 NE 类别或全局一对一。不能因 Schema 接受某引用就忽略物理语义。 |
| 物理 TP → 端口组件 | 同上：`T/nwit:inventory-mapping-attributes/{ne-ref,port-ref}` | 组合引用定位 NE 内组件，require-instance false；presence 明确表示物理 TP。`T/nwit:port-breakout/breakout-channel[channel-id]` 是只读能力，不是逻辑接口对象。 |
| 物理链路媒介 | 同上：`L/nwit:inventory-mapping-attributes/link-type` | identityref 定义 copper/fiber/coax/microwave/wlan/unknown/leased-fiber。unknown 表示已评估但不能分类，省略表示未评估；不能泛化成所有字段都填 unknown。与 SIMAP link-type 的连接基数含义不同，必须保留命名空间。 |
| 虚拟 NE / 软件 | [software -04 §6](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-software-04#section-6)：`nwis:ne-nonphysical` → ne-software/ne-virtual/ne-container；augment NE/组件的 software-rev、patch | VM 内虚拟路由器可用 ne-virtual；软件扩展表达清单类别及版本状态，不负责完成拓扑 node → 虚拟 NE 的通用映射。VSR 不能借物理 inventory-mapping-attributes 填充。 |
| 设备接口 → 地址/邻居 | [RFC 8343](https://www.rfc-editor.org/rfc/rfc8343.html)、[RFC 8344](https://www.rfc-editor.org/rfc/rfc8344.html)：`/if:interfaces/interface[name]/ip:ipv4`、`ip:ipv6` 的 address/neighbor | 作用域为一个设备数据树；同名接口跨设备不等同。L2 topology interface-name 是 string，不是自动跨设备解析的 interface-ref。 |
| 接口 → 网络实例 | [RFC 8529 §6](https://www.rfc-editor.org/rfc/rfc8529.html#section-6)：interface、ipv4、ipv6 下 `ni:bind-ni-name` → `/ni:network-instances/network-instance[name]` | leafref 默认要求目标存在；NI 名称限定设备范围。没有对应实例时不能用 require-instance false 的拓扑规则宽放。 |
| NI → 挂载路由/桥模型 | 同上的 root-type mandatory choice：vrf-root/vsi-root/vv-root；[RFC 8528](https://www.rfc-editor.org/rfc/rfc8528.html) mount-point | 子树和父树可见性由 schema mount/shared-schema/parent-reference 描述。必须独立验证挂载环境；仅在 JSON 中嵌入 routing 不能证明有效。 |
| 单设备 RIB | [RFC 8349](https://www.rfc-editor.org/rfc/rfc8349.html)：`/rt:routing/ribs/rib[name]/routes/route`，IPv4/IPv6 扩展目的前缀、next-hop | route 是只读无 key 列表；下一跳 simple/special/list 是 choice。RIB、拓扑前缀、最终数据路径是不同对象，不建立自定义 route→node 外键。 |
| 服务接入 | [RFC 9408 §6](https://www.rfc-editor.org/rfc/rfc9408.html#section-6)：`D/sap:service[service-type]/sap[sap-id]` | when 要求 sap-network。parent-termination-point 类型是 nt:tp-id（URI），attachment-interface 是 string，均不是强制验证的设备树 leafref；SAP 不等于终端主机。 |

**版本核对结果**：inventory -19 的 CODE BEGINS 文件名写 `2026-06-24`，模块实际 revision 是 `2026-09-16`，冻结记录必须保留这个差异。inventory -19 / software -04 引用 [RFC 9911](https://www.rfc-editor.org/rfc/rfc9911.html)，其类型模块 revision 为 `2025-12-22`，不能只凭旧 RFC 6991 类型也能编译便宣称依赖版本核验完成。后续组合验证单独使用新版类型；此前 30 模块的旧版研究基线保留。

### 5.2 SIMAP 外部关联的实际能力

YANG -03 引用的旧 external-relationship 草案已被 [draft-vivek-nmop-simap-external-relationship-00](https://datatracker.ietf.org/doc/html/draft-vivek-nmop-simap-external-relationship-00) 接续。该固定版本作为关联候选接受核验，不因到期状态跳过，也不把它自动当成完整解决方案。

- `/draft-template:template/template[template-id]`：URI 标识、template-type、参数、请求及 request-builder。原文模块名就是 draft-template，不改名为虚构 ietf 模块。
- `/draft-relation:relation/relation[relation-id]`：choice network-element-ref 可选择 network/node/link/TP 引用，另有 template-ref、path、parameter-value。引用默认要求目标存在。
- **已查明的定位缺口**：node/link/TP 的引用路径没有携带 RFC 8345 的完整 network/node 作用域；如果不同网络重用 node-id 或不同节点重用 tp-id，leafref 存在性校验不能判定唯一目标。param-ref 也未限定到本 relation 的 template-ref。不能声称这种组合天然解决全网唯一关联。
- **请求定义缺口**：主模块只提供 request-type 基类和 ALL_REQUESTS；request-builder 为空。文中的 NETCONF/foo 片段是增补示例，不是已完整定义的所有协议访问模块。本项目禁止自定义，不能用自行增补 SSH/HTTP 字段来填空，也不执行模板中的任何请求。

本项完成的是上述候选关联的定义与缺口核验：设备本地接口/NI/路由、物理 inventory 映射已有原生依据；虚拟拓扑节点到全网清单、设备树/挂载树、服务数据的端到端关联仍未闭合。下一步验证这些模块能否组合、哪些文字约束无法由 Schema 检查，并继续查找缺口的已有标准定义。TE 和各协议扩展的全面审查仍在 IETF-0 后续范围内。

## 6. 验证证据

- 原文下载及研究提取物：`.runtime/ietf-study/simap-audit/`；未写入生产模型目录。
- 首次命令：`.runtime/yang-model-tools/Scripts/pyang.exe -p .runtime/ietf-study/modules .runtime/ietf-study/simap-audit/ietf-simap-topology@2025-01-01.yang`；退出码 **1**，提取文件第 283 行的 description 语法错误。
- concept -12/-13 §4 比较结果：`.runtime/ietf-study/simap-audit/concept-12-13.diff`。已由脚本验证表内覆盖全部 36 个需求标识，无遗漏、额外或重复项。
- 已加入可复现研究脚本 `scripts/topology/audit_ietf_models.py`、[来源锁定清单与报告](ietf-model-audit/README.md)。38 个来源及提取模块均核对 SHA-256；空缓存下载和离线重放结果一致。提取只处理文档分页/缩进，没有修正 YANG。
- pyang 2.7.1：基础 30 文件通过；inventory/external 组合 10 文件通过；RFC + inventory/external 组合 35 文件通过；SIMAP 原文组合失败。使用新版类型的两组固定 RFC 9911 revision `2025-12-22`，没有静默使用旧类型。
- yangson 1.6.3：16 个原生定义正反例和 inventory-topology -11 附录 A/B 两个原始实例，共 18 项结果符合预期。其中 4 项是“Schema 接受但语义仍有问题”的证据，分别涉及 supporting 环、物理映射到虚拟 NE、port-ref 目标 class、外部 node-ref 作用域歧义。
- 附录 A 原始示例把 port-ref 写为完整路径字符串；实际类型引用组件 ID，而 require-instance false 使其未解析也能通过。保留原始示例和这个差异，不把验证通过说成关联已解析。
- 脚本整体退出 **1**，报告 `all_checks_passed=false`，因 SIMAP 原文不能编译；没有运行其双向/多点/生命周期实例。挂载实例、TE 和协议扩展全面核验仍待完成。脚本 Ruff 检查与文档 diff 检查用于验证研究产物，不替代模型验收。

## 7. 经授权的兼容包与实验实例（2026-09-28 续）

负责人批准继续制作独立语法补丁。第 4～6 节保留原文审计结果；它们不代表兼容包仍不能编译。原始来源、失败诊断和 -03 原文保持不变。补丁仅四处精确编辑，前后 SHA-256 固定，并与人工可审查 diff 交叉校验；没有补定义、feature、default 或语义约束。

- [验证包及复现命令](../models/network-twin/README.md)：完整 41 文件编译通过，topology/device 两个 profile 装载成功，包含新增 RFC 9129 OSPF 和完整依赖。
- [关联核验](../models/network-twin/associations.md)：已验证接口→NI；物理 inventory 引用允许未解析目标。虚拟 NE 不能借用物理映射，Yangson 不自动实现 RFC 8528 mount。这两项尚未完成可运行的端到端闭环。
- [实验溯源清单](../models/network-twin/instances/manifest.json)：既有捕获批次离线生成 2 节点/4 TP/4 LLDP 邻接、2 个虚拟 NE、37 条路由和 12 条部分接口实例。拓扑、清单、路由子集通过；接口因缺统计中断时间按预期失败，未补虚假值或裁剪必填约束。
- [验证结果](../models/network-twin/reports/validation.json)：24 个结果均符合预期，负例检查具体诊断；其中含 Schema 语义缺口和接口数据缺失，不能将结果理解为全量模型或全部实例验收通过。

IETF-1.1 完成兼容包范围；IETF-1.2 完成当前候选关联与工具边界核验，挂载实现和虚拟映射仍未完成；IETF-2.1 完成本批次有限字段映射核验，未完成整台设备状态和路径计算闭环。继续维持模型优先，下一步解决虚拟关联、挂载运行时及标准必填数据的获取依据；本轮不修改应用、设备访问、巡检或暂停的采集代码。

## 8. 原生挂载运行时及关联续查

第 7 节的工具边界已进一步分解：libyang 5.8.6 通过设备内 NI 挂载的 8 项配置级正反例（含父接口引用和跨 VRF 拒绝），无需修改任何 YANG；这不代表完整 operational 状态或应用协议服务验收。复现、来源版本及失败的 3.12.2 报告见 [挂载验证](../models/network-twin/mount/README.md)。

声明扫描确认锁定包内仅 LNE/NI 共 4 个挂载点。历史 inventory-topology -00/-01 的空锚点不等于 RFC 8528 挂载点；Peer-Mount -01 不会自动给 node 添加设备关联，且原文模块有独立语法错误。候选来源和诊断已锁定，结论限于已查范围，不声称全部 IETF 工作都无可用定义。详见 [关联续查](../models/network-twin/associations.md)。

方案将“设备内挂载验证”与“虚拟 node→NE→设备树关联”独立验收。后一项仍未闭合；不能因为前者通过，就将物理 inventory 映射套在 VSR 上，也不能把工程文件名/同名键当作原生引用。

## 9. 运行状态与部分观测边界（IETF-1.5）

新增 21 项离线检查，详见[运行状态验证说明](../models/network-twin/mount/README.md)及[报告](../models/network-twin/reports/operational-validation.json)。完整合成父接口/NI/挂载 RIB/YANG Library 通过严格约束检查，跨 VRF 引用拒绝；合成缺项和两台 VSR 部分接口树的完整性诊断保留。两棵 VSR 树在 parse-only 下往返相等，非法类型、层级及自定义字段被拒绝；parse-only 故意不解析跨 VRF 引用，不能作为完整语义验收。

按 RFC 8342 §5.3 修正“缺必填就不能保留任何观测”的潜在误读。libyang 5.8.6 显式 `-t data` 对部分语义缺失只告警，严格模式与其参数不同；脚本检查诊断和输出，不只看退出码。CLI 缺采集证据不能被认定为设备的合法语义例外。计数器时间仍缺失、未造值，生产挂载服务及全网虚拟关联仍未完成。

## 10. 基础路由选择结构（IETF-1.6）

39 项 IPv4/IPv6、配置/状态、单/多/特殊下一跳及 Schema 边界用例结果匹配，见[路由语义核验](../models/network-twin/routing-semantics.md)。Yangson 的两个 mandatory choice 内部异常未当作负例通过，改用 libyang 的两个负例及两个正对照核实；保留原始诊断与实际工具归属。完整模块与 profile 未改，不等于其他路由协议、TE、ECMP 实际转发或全网关联已经验收。
