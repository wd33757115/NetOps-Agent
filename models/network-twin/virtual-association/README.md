# 虚拟 node → NE → 设备树：新候选核验（IETF-1.7）

核验日期：2026-09-28。本项只核验候选定义，不新增或采用任何模块。结论：**本轮新查候选均不能闭合 VSR 的虚拟关联，缺口保留**。

复现（研究 Python，默认离线；缓存缺失时加 `--fetch` 按锁下载）：

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/audit_ietf_virtual_association.py
```

- 来源锁：`sources.lock.json` 共 12 份原文。inventory-topology -02～-10 提取 CODE BEGINS 模块并核对摘要；location -06、passive -01、entitlement -04 只锁全文用于检索。
- 报告：`../reports/virtual-association.json`，内容包括各版本模块与当前 41 文件包的组合编译、pyang 展开后的 augment/when/presence/config/if-feature/require-instance 结构，以及 9 项 Yangson 用例。
- 历史模块编译在 `.runtime/ietf-study/virtual-association/<版本>/` 下分别进行，每次从当前包内移除 -11，不与 -11 共存。

## 1. 候选：inventory-topology 的通用 node→NE 映射（-02～-05）

交接文档所说的“历史草案附录的泛型 topology-inventory 引用讨论”，实为 -00 附录 A 的 `example-topology-inv-ref`。原文写明它是 example、仅供讨论，没有 CODE BEGINS，命名空间也是 example，因此直接排除。继续追踪它的后继定义，结果如下：

| 版本（模块 revision） | node 映射的原生定义 | 是否限定物理 | 反向 NE→node |
|---|---|---|---|
| -02 / -03（均为 `2025-03-03`，但字节不同） | augment `nw:node` 下的 `inventory-mapping-attributes/ne-ref`（`nwi:ne-ref`，config false），受 feature `topology-to-inventory-navigate` 控制；无 when/presence | 否；描述写的是 NE “from which this node is abstracted” | 有：NE 下的 `node-ref`/`network-ref`，受 feature `inventory-to-topology-navigate` 控制，require-instance false |
| -04 / -05（`2025-10-20`，模块字节相同） | 同上；容器也改为 config false | 否 | 有 |
| -06（`2026-02-28`） | 去掉两个 feature，映射改为 config true；描述称 1:1 映射到 “physical NE” | 仅描述限定 | **移除** |
| -07（`2026-05-19`） | 新增 `network-types/inventory-topology`；三个 augment 加 when | 仅适用于 inventory-topology 网络 | 无 |
| -08～-11（-09/-10 模块字节相同） | 映射容器改为 presence：“存在即物理 node；不存在即 abstract node”；§3.2 规定该网络是最底层物理 underlay | 是 | 无 |

结论：

- 路径与语义：-02～-05 的 `D/nwit:inventory-mapping-attributes/ne-ref` 是唯一不带物理限定的原生 node→NE 定义。但当时基础 inventory 只定义了物理 NE，描述也没有声明适用于虚拟 NE；这组定义在 -06/-07 已被工作组收窄。
- 能否采用：-02～-05 与 -11 同名同命名空间。按 RFC 7950 §5.6.5，同一模块只能实现一个 revision，采用即等于降级替换已固定的 -11，违反第 0 节“不降级、不混装”的要求。**排除。**
- 作用域：`nwi:ne-ref` 与 `nw:node-ref` 都是 require-instance false，只是 NE 键的字符串引用，不指向任何设备数据存储。

可执行证据：历史 -05 与当前 -19 混合组合编译通过（这只是历史行为探针，不是被采纳的组合）。下表每一行都与预期一致；“接受”不代表关联已解析。

| 用例 | 结果 | 含义 |
|---|---|---|
| h05-virtual-node-ne-ref | 接受 | 历史定义可以把拓扑 node 指向 ne-virtual |
| h05-target-missing-still-accepted | 接受（缺口） | 目标 NE 不存在也能通过 |
| h05-wrong-object-physical-host-accepted | 接受（缺口） | 把 VSR node 错指到宿主物理 NE，Schema 无法区分 |
| h05-mapping-is-not-config | 拒绝，诊断指向映射容器 | 该版本只能作为只读状态；已剔除 inventory 根本身为 config false 造成的干扰 |
| h05-feature-disabled-rejects-mapping | 拒绝，RawMemberError | 必须声明 feature |
| h05-reverse-node-ref-target-missing-accepted | 接受（缺口） | 反向引用同样允许目标缺失 |

## 2. 当前 -11 的可用表达与局限

| 用例 | 结果 | 含义 |
|---|---|---|
| c11-mapping-outside-inventory-topology-rejected | 拒绝，member-not-allowed | L3 等非 inventory-topology 网络中不能放映射，由 when 强制 |
| c11-node-without-mapping-is-abstract | 接受 | VSR 在物理网络中只能作为 abstract node，与 ne-virtual 清单项之间没有关联 |
| c11-supporting-node-to-physical-host | 接受（缺口） | RFC 8345 原生路径是：L3 node 由 supporting-node 指向宿主物理 node，宿主再经 ne-ref 指向物理 NE。前提是有宿主服务器身份的证据，本实验未采集，不能捏造；即使具备，ne-virtual 清单项仍然没有关联 |

先前已有的反例“物理映射指向 ne-virtual 仍被 Schema 接受”见 `../reports/validation.json`，本项不重复。

## 3. NE → 设备数据树

inventory -19 用的是网络模型与设备模型分离的定义（RFC 8309/8969）。ne-id 是在网络内唯一的字符串。原文 §6 说明，控制器可以读取设备支持的设备模型来收集这些信息，所用机制不在本文范围内；RFC 8348 只是可选项之一。

-19 没有任何节点指向设备的数据存储、管理地址或挂载点。本轮检查的 location -06、passive -01、entitlement -04 都不 import `ietf-network`，只在 inventory 内部使用 ne-ref。它们都不提供 node→NE 或 NE→设备树关联。**标准层面没有 NE→设备数据树的引用**（报告中 `ne_to_device_datastore_standard_reference: null`）。

另外，-19 的 ne-type 默认值是 `ne-physical`。VSR 清单项必须显式写 `ne-virtual`；缺省时会被当成物理 NE。现有实例已经显式填写。

## 4. 验收问题的回答

| 问题 | 结论 |
|---|---|
| 关联是什么标准节点定义 | 当前有效的只有 -11 的物理 node→NE。通用定义只存在于已被取代的 -02～-05 |
| 引用到哪里 | NE 键字符串（require-instance false），不到设备数据存储 |
| 如何区分设备与管理域 | ne-id 在一个 network inventory 内唯一。跨管理域和设备数据存储的区分没有标准节点，由控制器实现 |
| 是否允许目标缺失 | 允许，所以“通过”不证明已解析 |
| 虚拟语义是否成立 | 当前版本不成立（物理 presence、底层 underlay）；历史版本未声明虚拟语义，且已被取代 |
| Schema/工具能否验证 | 能验证 when、feature、config 边界；不能验证目标类别、目标存在，也不能验证与设备树的对应关系 |

缺口保留，不用项目 augment、同名键或工程文件名顶替。是否接受“由控制器实现层在 YANG 数据树之外维护 NE 与设备会话/数据存储的对应”，登记为路线图第 14 节待决项 Q-IETF-1。
