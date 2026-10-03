# 网络数字孪生开发交接

本文最前面是最新一次交接（2026-10-03，Claude → GPT）；其后保留 2026-09-30（GPT → Claude）的历史交接，第 1～7 节的工作方式、硬约束与工具边界**仍然有效**，先读完本节再读第 2 节。

## 最新交接：2026-10-03，Claude → GPT

项目根目录 `C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，分支 `feat/network-twin`；`origin`（`https://github.com/wd33757115/net-ops.git`）已推送到 `6855949`，本交接文档的提交在其后，是否推送以 `git status -sb` 为准。Windows + PowerShell；Git Bash 也可用（本轮偶发 Bash 工具退出码 107，换 PowerShell 即可）。

**一句话现状：拓扑建模已在一套 8 台纳管设备的园区 + 双出口实验网（H3C VSR + Cisco IOL 混合，ISP 不纳管）上端到端跑通，平台模型与实际接线对应，并按 IETF 标准补了 VRF、VRRP、递归下一跳、聚合与节点管理属性；导出模型通过冻结 YANG 模块校验。负责人倾向继续完善建模，参考 IETF 与成熟产品；下一步建议见“接下来的计划”。**

### GPT 接手更新：2026-10-03

负责人已授权按计划开始，并确认 ACC1/ACC2 两条新 SSH 指纹；使用平台已保存的默认登录完成只读采样，未记录口令、未修改设备配置。第一项 VLAN/trunk 已部署为 **topology 0.4.13**：真实发现 8 台全成功，快照 `894ad28d1fdc415cb942fafa9247786c`，11 个 trunk TP 导出 `outer-tag="10,20"`，access/PVID 留在设备事实中，导出通过冻结 YANG 完整校验。16 条设备间接线按 LLDP 验收；额外管理网 ARP 逻辑邻接不当物理网线。最终相关回归 636 通过、2 个可选模板源用例跳过；详情、限制与证据路径见路线图 §14 新台账。

本轮后续已部署 **topology 0.4.14**，按计划接入状态、OSPF 和独立设备树：冻结 SIMAP 无通用状态 leaf，沿用 Q-IETF-1 应用层快照身份访问 RFC 8343/8344/8347 部分接口树及独立路由/LLDP 树，不补自定义 YANG 外键。只消费本次最新 attempt；失败且没有新证据时不借旧 UP。链路历史仅保留同网络、身份明确的 L2 两设备关系，原始来源与最后确认时间不随新快照刷新。路径遇不可用端口终止，缺状态保留候选并标 partial。最终后端 **1149 通过、2 个可选模板源跳过**，前端 **18 通过**；真实 8 台再次全成功，快照 `660c20b7ae0b42f6b3e48f1b0a3c5b57`，16 条 LLDP 接线、11 个 trunk 与冻结拓扑校验通过，6 个默认 VRF RID 不传播到 MGMT。明细、度量与原生部分实例的缺项见路线图 §14 最新台账；临时关闭接口的故障实测仍待负责人确认，当前未修改实验配置。代码本地提交 `28defe6`，未推送。

**标准核查修正**：下文 Claude 原计划把 RFC 8346 `metric1` 标成 IGP cost，这是该标准没有规定的具体语义；也不能把 `metric1/metric2` 当两个方向。真实 BR=1/Cisco=10 分别保留在设备事实，仅当全部参与 TP 均唯一绑定当前 OSPF 接口、明确 scope 且成本相同，按本项目声明的投影政策填共同 `metric1`，其余省略并给 gap；PID 是设备本地键，不要求两端相同。L3 TP 的 `ip-address`、`unnumbered-id`、`interface-name` 在同一 choice，现有 IP TP 不加互斥接口名，接口关联使用既有 IP 事实。[RFC 8346](https://www.rfc-editor.org/rfc/rfc8346.html)

设备树为部分实例：统计中断时间、VRRP version、原生 RIB name、LLDP agent/time-mark/remote-index 等缺项如实给 gap；不造默认值、表项键、计数器或时间，未宣称完整设备 datastore 已通过校验。完整冻结模块与独立 library 位于 `src/apps/topology/resources/yang/`，校验控制与反例在新增 native 投影测试中。下文 Claude 原始统计及计划保留为接手时的历史基线。

### 网页 GPT 报告核实：2026-10-03

对照负责人提供的 `Downloads/deep-research-report (1).md`、本地源码与 RFC 原文后，报告可用作领域清单，**不能作为本仓库的静态审计结论**：它的背景明确承认没有仓库链接，大部分判断使用假设，但执行摘要和表格却写成确定的实现现状。

| 报告结论 | 核实结果 |
| --- | --- |
| 单层拓扑、没有支持网络映射、TP/端口未实现 | 与接手时的代码不符。`model/builder.py` 已构建 SIMAP L2 和按 VRF 分开的 L3，包含 TP 与 supporting-network/node/TP/link；有冻结模块校验和真实园区回放。 |
| 尚未引入标准 YANG、标准导出和测试 | 与代码不符。接手前已支持 RFC 7951 标准树导出及 Yangson 冻结模块验证；本轮补充独立部分设备树。 |
| 缺少完整设备树、服务模型、策略、清单与协议层 | 方向基本成立，需逐项用采集事实与实例验证衡量，不能因某个 RFC 存在就把所有模型列为当前必做项。 |
| 没有 NETCONF/RESTCONF/gNMI 即拓扑模型不合规 | 混淆数据模型与协议能力。RFC 8345 定义拓扑结构；协议服务和采集渠道需另外设计，当前 JSON 导出不冒称 NETCONF/RESTCONF 服务。[RFC 8345](https://www.rfc-editor.org/rfc/rfc8345.html) |
| NMDA 就是配置表和状态表分开；RFC 6241/8040 直接公开 operational | 表述不准确。NMDA 是概念 datastore 与实例语义，并未要求所有协议公开所有 datastore；访问 NMDA 的 NETCONF/RESTCONF 扩展分别由 RFC 8526/8527 定义。[RFC 8342](https://www.rfc-editor.org/rfc/rfc8342.html)、[RFC 8526](https://www.rfc-editor.org/rfc/rfc8526.html)、[RFC 8527](https://datatracker.ietf.org/doc/html/rfc8527) |

本轮执行顺序继续以本地路线图和有证据的标准映射为准，报告中的工期与优先级没有仓库依据，未直接采纳。

### 负责人本轮新定的要求（与第 2 节同等优先）

- **“建模时优先采纳 IETF 相关标准”“我们的第一任务就是参考 IETF 完善拓扑建模”**。遇到建模选择先查 IETF/IEEE 原文（RFC、SIMAP 草案、YANG），能从标准得出答案的不要再请负责人二选一；本轮负责人就因为被问 VRF 表示法而要求“再搜搜，IETF 一定有相关标准”。只用标准已有字段，仍然**不加项目自定义 YANG 字段**。
- 可以参考成熟产品（Batfish、IP Fabric、SuzieQ、NetBox 等）的做法，但落到模型上以 IETF 定义为准。
- **改实验设备配置要先征得负责人同意**（本轮在 BR1/BR2 加 VRRP 就是先问再做）。只读采样已获授权。
- **在设备上建账号、往任何地方输入设备密码，只能由负责人自己做**：实验账号用负责人运行的 `lab.py account` 建立；平台“拓扑默认登录”的密码也由负责人在页面上改。负责人在对话里给过密码，也不能记录或代填。
- 推送 GitHub 要负责人明确说；提交可以照常做（明确路径）。

### 09-30 之后完成的内容（按时间，均已提交并推送）

| 提交 | 内容 |
|---|---|
| `c245d4e` … `79eeb72` | 终端按键直通、巡检原文基线与整段文本对比、诊断提示改为能力描述、DeepSeek 对照实验记录（详见路线图 §14 对应条目） |
| `70f9320` `7a9a652` `9337087` `b75b0ab` | 回放时间线、工具失败原因告知模型、执行视图显示推理、推理与笔记用中文 |
| `727ed5b` | 一次对比多台设备与基线；诊断提示按 ROLE/DIAGNOSIS/DOCS/EVIDENCE 重写；路径工具注明是“之前的路径” |
| `9fe5284` | 本地按原样记录每轮发给模型的请求（`runs/requests/`，`trace_timeline.py --requests`） |
| `bb440f3` | 引用校验接受本会话更早的证据并点名未知引用；全部时间转北京时间 |
| `5427b3d` | 平台启动与 App 启动解耦（排空、`--force`）；采集数据不再关键词脱敏，保持原文 |
| `a10c346` | 表格按条目对比、摘要与按需查行（`compare_rows`）；大表改用统计命令 |
| `422fc89` | 知识问答会话级模型与思考强度（只对当前会话生效，默认关闭思考） |
| `2cec677` | **园区 + 双出口实验网**与 Cisco 真机建模（见下） |
| `cd12948` | **每个 VRF 一个三层网络**（RFC 8345 §3、RFC 9375 §4.2、SIMAP REQ-CONTROL-PLANE） |
| `0bf421b` | **VRRP 按 RFC 8347**：采集、虚拟地址定位为 `virtual_router`、路径第一跳取主设备 |
| `8e62f84` | 旧格式（SIMAP 前）快照导出改为 URI 标识（另一会话完成） |
| `ed1dbcf` | **递归下一跳按 RFC 8349** 在本机 RIB 解析，证据 `resolved_via`；新缺口 `unresolved_next_hop` |
| `6855949` | **聚合按 RFC 8944** `lag`/`member-link-tp`；节点 `management-address`/`management-mac` |

各项的设计理由、实测与测试数字在路线图 §14 台账 2026-10-03 的几条里，以台账为准。

### 实验环境（新）

- **园区 + 双出口实验网 campus-wan**：工具包 `scripts/topology/campus_wan/`（`README.md` 有拓扑图和地址表）。路由参照 Batfish 示例网络，二层参照园区三层组网。BR1/BR2 为 H3C VSR（eBGP 上联、iBGP、OSPF area 0；BR1 本地优先级 200）；CORE1/2 为 Cisco IOL L3（路由反射器、/31 互联）；DIST1/2 为 IOL L2（ABR、VRRP、LACP Po1、rapid-PVST）；ACC1/2 为 IOL L2（双上联 trunk）；ACC2 后接傻瓜 VLAN 交换机（PC4、PC5）；ISP 不纳管、不发 LLDP；Cisco 管理口在 VRF MGMT。管理地址 10.10.1.11–18，主机 10.10.1.254。BR1/BR2 的 GE1/0 另有 VRRP（VRID 1，虚拟地址 10.10.1.10，负责人批准）。
- GNS3 工程 `~/GNS3/projects/netops-campus-wan/`，跑在 GNS3 VM（Hyper-V，10.10.10.2，10GB）。与旧的两台 VSR 工程（`untitled`）内存不够同时运行，旧工程已关闭，VSR-1/2 配置已保存。
- `lab.py` 子命令：`project`（生成工程，已生成过就不要再跑）、`push NODE|all`（经控制台推配置并保存，报被拒行）、`console NODE "命令" ...`（VSR 自动走串口 aux）、`type`/`screen`（VSR 的 VNC）、`traffic [--stop]`（PC 持续 ping，保持 ARP/MAC 表有内容；采样前先开）、`account`（只能负责人运行）。
- 账号：8 台纳管设备的 admin 账号已由负责人建好，密码与 `.env` 的 `NETOPS_LAB_PASSWORD` 一致；脚本从环境变量读取，不要打印、不要写入任何文件。平台的拓扑默认登录已由负责人更新为同一密码。
- 平台里园区网对应的网络 ID 是 `d537b0de1fab427683413b81290db718`，以 8 个管理地址为种子、`allowed_cidrs` 为 `10.10.1.0/24` 提交发现即可（API `POST /api/apps/topology/runs`，本机开发账号见登录页；请求体含中文时用 UTF-8 发送，Git Bash 下的 curl 会报解析错误）。
- 采样：`scripts/topology/collect_lab_samples.py <地址...>`（按 SSH 横幅区分 Comware/IOS，只发只读命令）。回归素材：`tests/fixtures/topology/campus-wan/`（批次 `20261003-010038`，`expected.json` 是实际接线、终端位置、三层网段、VRRP 主备），导入用 `scripts/topology/import_lab_fixtures.py --lab campus-wan`（秘密扫描；导入器拒绝覆盖已有且不同的素材，换批次要先删目录）。

### 部署与测试

- Claude 交接时平台版本：topology **0.4.12**、assistant **0.8.9**；GPT 本轮部署版本见文首更新。App 源码改后要升 `app.yaml` 版本并执行 `scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps topology -NoBrowser`；部署会先停用并排空，**进行中的发现任务会被取消**。
- **代码摘要准入**：部署后工作区里该 App 的源码只要再变（包括其他会话的改动），App 就返回 503“应用不可用”，直到重新部署。本轮就因另一会话在同一工作区改 `normalize.py` 而中断过一次。
- 测试：`venv/Scripts/python.exe -m pytest -q tests/unit -k "topology or twin" -p no:cacheprovider`（交接时 478 通过）。YANG 校验依赖 yangson，不在 venv 里，放在 `.runtime/p0-2/yang-deps`：加 `PYTHONPATH=.runtime/p0-2/yang-deps` 后 YANG 用例才真正运行，否则跳过。园区网回归 `tests/unit/test_topology_campus_wan.py`（25 项，含导出 YANG 校验）。

### 本轮踩过的坑（照做可避开）

- GNS3 工程文件里**不要写 `startup_config_content` / `startup_script`**：控制器会一直留着它，之后改节点设置都报 400，且每次打开工程都会用它覆盖设备已保存的配置。`lab.py` 已改为全部经控制台推送。
- IOL 第一次 `write memory` 会问“Overwrite the previous NVRAM configuration?[confirm]”，不回车确认就什么都没保存（重启后配置和 SSH 密钥都丢）；`lab.py` 的 `run()` 已处理，其他 `[confirm]` 一律答否。IOS-XE 17.15 用 `crypto key generate rsa general-keys modulus 2048`（不带 general-keys 是隐藏命令）。
- VSR 首次启动卡在 DHCP 自动配置，在 VNC 上按 Ctrl+C；串口（aux）默认只读角色且要求认证，已在两台 VSR 上设 `user-role network-admin` 与 `authentication-mode none` 并保存。VSR 不支持 STP，只能当路由器；VSR 默认不回 TTL 超时，traceroute 在它那一跳显示 `*` 是正常的。
- VPCS 的 MAC 在工程重开后会重新分配，IP 不变；跨重启对比时 MAC 变化不是故障。
- IOS 在 ARP 表、VRRP 为空时**一个字都不打印**，按 ios 族认作“已确认为空”；`show lldp neighbors detail` 不打印端口 ID 子类型；`show ip route` 的递归 BGP 路由没有出接口。
- 本机 `python` 是应用商店占位（退出码 49），一律用 `venv/Scripts/python.exe`。连接 GitHub 偶发 TLS 握手失败，重试即可。
- 工作区与其他会话共享：只按明确路径提交；`web/react_frontend/src/pages/{ChatPage,ChatPage.test,SkillsPage,StatusPage,StoragePage}.tsx` 暂存改动、`generated.ts`/`generated.meta.json` 与未跟踪的 `src/workflows/custom/` 都不是本线的，不要提交。

### 与完整网络模型的差距（2026-10-03 实测统计）

**1. 已导出的拓扑模型：骨架齐，属性约一半。** 用 YANG 模式树枚举冻结模块的全部数据节点，与园区网导出逐项对照（不含通知与 SIMAP 历史树；模型中没有任何字段落在标准之外）：

| 模块 | 已用/定义 | 未用的主要节点 |
|---|---|---|
| RFC 8345 `ietf-network` | 11/11 | — |
| RFC 8345 `ietf-network-topology` | 11/17 | 单向链路的 source/destination（按 SIMAP 用双向链路，有意不用） |
| RFC 8944 二层 | 11/26 | 链路 rate/duplex/auto-nego/delay；节点 bridge-id、management-vlan；TP port-number、unnumbered-id。**VLAN/QinQ/VXLAN 三个 feature 在 `src/apps/topology/resources/yang/library.json` 未启用**，`outer-tag` 等 VLAN 节点目前不可用 |
| RFC 8346 三层 | 8/21 | 链路 `metric1/metric2`、节点 `router-id`/`name`、前缀 metric、TP `interface-name`/`unnumbered-id` |
| SIMAP -03 | 16/36 | name/description/label；`timestamp`、`live-network-ref`（历史/推演）；`subnetwork`（分层分域）；`tp-role` |

**2. 设备级模型：有采集，未按 YANG 导出。** 生产采集的事实（接口、IP、ARP、LLDP、MAC、聚合、VRRP、路由、版本）都对应 RFC 8343/8344、IEEE 802.1AB/802.1Q/802.1AX、RFC 8347/8349 与网络清单草案，但存的是字段名对齐 RFC 的项目 JSON，不是 YANG 实例。`models/network-twin/` 研究包只对旧两台 VSR 做过离线映射，未接入 App。拓扑节点 ↔ 清单 ↔ 设备树的关联**标准本身没有定义**（研究包 `virtual-association` 已记录）。

**3. 尚未涉及的整块：** VLAN/trunk 成员（RFC 8944 VLAN feature + IEEE 802.1Q）；链路/节点状态进模型（SIMAP REQ-STATUS + RFC 8343 oper-status）；路由协议层（RFC 9129 OSPF、BGP 模型草案，SIMAP 把 IGP/EGP 列为图层）；策略（RFC 8519 ACL、RFC 8512 NAT）；SIMAP 历史与推演；服务层（RFC 9182/9291/9408）；清单（序列号、板卡）。STP 端口状态在 RFC 8944/IEEE 802.1Q 拓扑模型中无对应，只能作设备事实。

### 接下来的计划（负责人倾向继续完善建模，可参考成熟产品）

每项先查标准原文、给负责人一段简短设计（落在哪些标准节点、采哪些命令），确认后实现；先用园区网真实回显写解析与测试，再部署、重新发现、实测验收，最后写台账。成熟产品的做法只作对照，模型以 IETF 为准。

1. **VLAN 与 trunk（建议先做）**
   - 标准：RFC 8944 的 `VLAN` feature（TP 的 `outer-tag`，类型为 VID 范围）；设备侧 IEEE 802.1Q bridge（`bridge-port` 的 `pvid`、`bridge-vlan` 成员）。先读 RFC 8944 原文确认 access 口与 trunk 口在 `outer-tag` 上的语义，再决定只启用 feature 还是同时引入 802.1Q 设备事实；启用 feature 要同步 `library.json` 并跑 YANG 校验。
   - 成熟产品：Batfish `interfaceProperties` 有 `Switchport_Mode`、`Access_VLAN`、`Allowed_VLANs`、`Native_VLAN`；NetBox 接口有 802.1Q Mode（Access/Tagged/Tagged (all)/Q-in-Q）、Untagged VLAN、Tagged VLANs；IP Fabric 的路径查找在二三层边界用 VLAN+ARP+MAC 关联。
   - 素材：园区网 dist/acc 的 `vlan.txt`、`trunk.txt` 已入库；Comware 的 access/trunk 回显（`display port trunk`、`display interface brief` 桥模式表）需另采。
   - 验收：园区网 trunk 口带出 VLAN 10/20，access 口的 VLAN 正确；能对应故障集 v1 的“trunk 未放通 VLAN”“端口 VLAN 配错”。
2. **状态进模型**
   - 标准：SIMAP REQ-STATUS 要求宕掉的链路/节点仍出现，状态“在 SIMAP 中实现或可从 SIMAP 访问”；SIMAP -03 冻结版没有状态节点，接口运行状态的标准落点是 RFC 8343 `oper-status`。表达方式需要设计并请负责人确认（不要自定义字段）。
   - 成熟产品：Batfish `Active`/`Admin_Up`，NetBox `Enabled`，SuzieQ interfaces 表的状态字段。
   - 验收：注入“接口 shutdown”后，模型与路径中能看出该链路不可用，链路仍保留。
3. **三层链路度量与 router-id**
   - 标准：RFC 8346 `l3-link-attributes/metric1`（IGP cost）与节点 `router-id`。需新增 OSPF 接口事实（Comware `display ospf interface`、IOS `show ip ospf interface brief`），先采样。
   - 成熟产品：Batfish `ospfInterfaceConfiguration` 的 `OSPF_Cost`；SuzieQ 的 ospf 表。
   - 验收：园区网各三层链路带 cost，能解释等价路径来自 cost 相同。
4. **设备树按 YANG 导出（底座，量大）**：把已采事实输出为 RFC 8343/8344/8349/8347 与 IEEE 802.1AB 的实例，复用研究包映射经验。拓扑节点与设备树如何关联标准未定义，**动手前须请负责人定方向**。
5. **其后**：OSPF/BGP 邻接作为协议图层；ACL（RFC 8519）进入路径（对应路线图 M2，Batfish/IP Fabric/Forward 都做策略感知路径）；SIMAP 历史/时间戳对接已有快照；清单。

**待负责人决定的事项**
- **H3C 聚合真实样本**：两台 BR 之间只有一根线，Comware 非空聚合输出还没有真机回显（解析器遇到会如实失败）。需要负责人在 GNS3 里把 BR1/BR2 网卡从 5 改为 6、加一根 GE6/0↔GE6/0，再由我们配 Route-Aggregation 并采样。
- STP 端口状态作为设备事实采集与否。

### 先读这些

1. 本节，然后第 2 节的硬约束与 Git 纪律。
2. 路线图第 0 节原则、§14 台账 2026-10-03 的各条（每条有文件、命令、结果、未完成项）。
3. `scripts/topology/campus_wan/README.md`、`tests/fixtures/topology/campus-wan/README.md`。
4. IETF 资料：[IETF 研读记录](network-digital-twin-ietf-study.md)、[原生模型审计](network-digital-twin-model-audit.md)、[验证包](../models/network-twin/README.md)；成熟产品资料：[开源参考](network-digital-twin-open-source-reference.md)（偏采集与解析）。

参考链接：[RFC 8345](https://www.rfc-editor.org/rfc/rfc8345.html)、[RFC 8346](https://www.rfc-editor.org/rfc/rfc8346.html)、[RFC 8944](https://www.rfc-editor.org/rfc/rfc8944.html)、[RFC 8347](https://www.rfc-editor.org/rfc/rfc8347.html)、[RFC 8349](https://www.rfc-editor.org/rfc/rfc8349.html)、[RFC 9375](https://www.rfc-editor.org/rfc/rfc9375.html)、[RFC 8529](https://www.rfc-editor.org/rfc/rfc8529.html)；[Batfish 配置属性](https://pybatfish.readthedocs.io/en/latest/notebooks/configProperties.html)、[NetBox 接口模型](https://netboxlabs.com/docs/netbox/models/dcim/interface/)、[SuzieQ 表](https://suzieq.readthedocs.io/en/latest/tables/)、[IP Fabric 发行说明](https://docs.ipfabric.io/latest/releases/release_notes/previous_releases/nimpee_v2/)、[Batfish 示例网络](https://github.com/batfish/batfish/tree/master/networks/example)。

---

## 历史交接：2026-09-30，GPT → Claude

交接日期：2026-09-30，本文已追加 GPT 接手后的验收与支线记录。项目根目录：`C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，Windows + PowerShell（也可用 Git Bash），分支 `feat/network-twin`。Claude 原始交接时已推送到 `origin`，止于 `de6a28f`、路线图 v1.19；后续本地提交不代表已经推送。上一份交接（Codex → Claude，2026-09-28，止于 `ec81310`）的内容已并入本文；其第 6 节的工具与标准边界仍然有效，保留在本文第 7 节。

**一句话现状：拓扑发现（SIMAP L2/L3）→ 终端定位 → 双向路径计算 → 知识问答自动登录路径设备取证诊断已打通。D-8 八个场景的首轮盲测已结束，根因命中 6/8，AI 未执行网络配置命令；场景 3 漏判，场景 4 用满预算后没有答案。达到原定数值及安全门槛，但结论质量问题尚未闭合，不能称每句话均有充分证据。负责人已追加授权 GPT 自行注入、测试和恢复实验故障。**

### 2026-09-30 交接内容：原始基线配置对比（历史记录；其后已完成并实测，见路线图 §14 与上文提交表）

负责人因 token 将尽要求本次停止扩展、写好交接。**Claude 下一步先验收下面这项，不要继续旧的巡检变化列表方案，也不要先重跑 D-8 全故障集。** 下文既有记录保留，若描述“生产提示未改”，指此前 D-8 收尾修复阶段；本次配置比较已按确认范围修改 `DIAGNOSIS` 提示。

### 负责人最新确认的方向

负责人先要求把巡检基线用于故障分析，随后明确纠正：“巡检这个应用……有大量的无效的变化数据，应该诊断时找基线原始数据，再做一个配置对比工具，然后直接对比需要的部分”。对当前配置来源，负责人选择了 **“诊断时读取指定配置块”**；已告知这是现有 D-8 配置导出限制的受控只读例外，保持脱敏、白名单和审计。

因此最终链路是：按管理地址和故障前时间找**原始配置快照** → 选择明确配置块 → 现场读取同范围 → 确定性文本差异 → AI 结合运行状态解释影响。不依赖 `NetworkChangeRecord` 或预生成差异，不启动新巡检，不新增 UI/迁移/YANG 字段。旧 `inspection__read_changes` 试作没有部署，已从源代码/工具注册撤回，只有本次自建文件移入忽略目录 `.runtime/abandoned-baseline-changes/`；不要恢复或提交它们。

### 已完成的实现

- `src/platform/device_access/config_sections.py`：纯函数范围验证、固定命令、同范围原始行提取、统一脱敏和有界对比，复用现有 `src/core/patrol/config_diff_bridge.py`（未修改旧巡检核心）。首版仅 Comware：`section=interface,target=GigabitEthernet3/0` 等完整接口名；`section=ospf,target=1` 等进程号。接口查询 `display current-configuration interface <完整接口名>`；OSPF 查询 `display current-configuration configuration ospf`，再严格提取指定进程。语法有 H3C 官方文档依据，**尚未在本实验设备上实测**。缺块、范围不唯一、未闭合、CLI 错误、截断不会报 unchanged；脱敏行不参加差异，不同占位符不会制造假变更；只有隐藏字段而可见部分相同时返回 unavailable。
- `src/apps/inspection/backend/config_baseline.py` 与 `tools.py`：内部工具 `inspection__read_config_section`，`evidence=False`、只读，经 registry 正常 App 鉴权。核验 snapshot 原始文本 SHA-256、完整性、命令和设备身份；返回基线 ID/运行/时间/摘要/行段及质量。已有资产关联必须严格一致；无关联的离线快照可精确匹配其持久 `management_ip`，并明确说明只能证明历史地址对应，不能证明地址未被复用。不能从设备名称猜 IP。
- `inspection/backend/diagnostics.py`：`list_snapshots` 新增可选 `host` 精确过滤并返回 `management_ip`；原设备/运行/时间分页仍保留。`read_snapshot` 补读也沿用统一脱敏。AI 找到快照后按 `run_id` 查询来源、用途和质量；历史基线不自动等于健康基线。
- `src/apps/device_access/backend/config_compare.py` 与 `tools.py`：AI 工具 `device_access__compare_config(snapshot_id, host, section, target, purpose)`。先经 registry 授权读取有效基线，再用公共 SessionHandle 发送 **1 条服务器构造的固定命令**；凭据沿用默认/已保存会话，失败不自动重试，finally 关闭。结果 ≤24KB，保留两侧脱敏片段、时间、差异和会话/命令引用。没有硬性 inspection App 依赖，普通 SSH 终端可单独用。
- `assistant/backend/diagnostic_workspace.py`：仅在有权限的基线提供方存在时开放比较工具；`platform`、`turn_id` 服务端填入，设备仍只能来自本轮定位/路径结果。每次比较计 1 条，与普通诊断共用 8 台/20 条预算。`dsh.py` 提示 AI 用原文和指定块，不用巡检变化列表，比较后还需运行状态证据。
- `assistant/backend/evidence.py`、`workspace.py`、`api.py`、`turns.py`：复合比较证据固定要求 `device-access` 和 `inspection` 两个 App 当前权限；最终提交引用、引用回读、步骤展开均重新鉴权。沿用原有展开结果，没有新增界面。
- `platform/device_access/governance.py`：接口/OSPF 可能携带的 authentication-mode、cipher、keychain 等敏感行在**平台审计和服务器会话日志持久化之前**脱敏。`device_access/backend/diagnosis.py` 同时窄修普通 run 的大小写/缩写配置导出绕过；普通 run 仍不能用 `display cur`、`show RUN` 等导出配置，比较是独立受控例外。

### 验证及当前部署

- 配置范围/基线与原诊断查询组：`venv/Scripts/python.exe -m pytest tests/unit/test_config_sections.py tests/unit/test_inspection_config_baseline.py tests/unit/test_assistant_diagnosis.py -q`，**56 passed**，只有 2 个既有 websockets 弃用提示。随后仅对新增文件做格式与等价函数写法整理，Ruff 通过。
- 新服务、真实 SQLite 基线、真实 registry/SessionHandle、假 SSH、平台白名单/审计/日志及官方 DSH 闭环组：77 项通过，随后新增 2 项（含密文一致不造假差异、超大块）后对应新工具组 27 项通过；共覆盖 79 个不同用例，分组间重复不累加为测试总数。DSH 测试用 stub 模型和假 SSH，**不是实机/真实模型验收**。
- 根会话最终相关查询/引用/展开/设备范围组：`venv/Scripts/python.exe -m pytest tests/unit/test_assistant_evidence_access.py tests/unit/test_assistant_tool_outputs.py tests/unit/test_device_access_app.py tests/unit/test_assistant_diagnosis.py tests/unit/test_assistant_progress.py -q -p no:cacheprovider`，**44 passed**。与前述组存在重叠，不相加。此前 30 项是中间检查，不另算。Ruff 对本项 21 个 Python 路径通过，其他线程 5 个暂存文件的 index/worktree 哈希核对未变。独立审查未发现未解决阻断项。
- 升级命令已执行：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps inspection,assistant,device-access,topology -NoBrowser`。目标版本 inspection **1.3.2**、assistant **0.8.2**、device-access **0.9.3**；topology 仍 **0.4.3**。命令退出 **0**，`verify.ps1` **9/9 App** 及 FastAPI/BFF/React/Worker 检查全部通过；项目已运行于 `http://localhost:3000/chat`。未修改设备配置。

- 对应新服务组合的精确命令：`venv/Scripts/python.exe -m pytest tests/unit/test_device_access_config_compare.py tests/unit/test_device_access_config_compare_dsh.py tests/unit/test_ssh_governance.py tests/unit/test_device_access_sessions.py -q`（77 passed，34.46 秒）；新增 2 项后仅重跑 `venv/Scripts/python.exe -m pytest tests/unit/test_device_access_config_compare.py -q`（27 passed，21.13 秒）。79 个不同用例由服务 27 + DSH 2 + governance 20 + sessions 30 组成，非同一轮同时运行。
- **代码已本地提交 `33ec796`**（`feat(diagnosis): compare scoped live config with raw baselines`），25 个本项代码/测试文件；此前 D-8 收尾修复是 `089c270`。本次交接与路线图另作文档提交，**均未推送**。没有执行 `.runtime/verify-config-scope.py`，也没有本次实机证明文件。其他线程暂存的 5 个页面以及环境生成注册文件保留。

### Claude 从这里继续

1. 先检查 `git status --short` 与上面的部署/提交状态，保留第 5 节的无关暂存页面、`generated.ts`/`generated.meta.json`；不恢复旧 read_changes。只有新问题需要改代码时才重跑对应测试；App 代码变更后必须重新部署。
2. **尚未实机执行任何本次配置查询。** 已准备 `.runtime/verify-config-scope.py`，但未运行；`.runtime/config-scope-live-proof.json` 在交接准备时不存在。脚本对两台 VSR 各用公共 SessionHandle 读取 GE3/0 与 OSPF 1（共 4 次独立只读会话，正常初始化另计），关闭后直接下载已有 MinIO `.log` 验证，不调用可能补写日志的 read_log/sync_log。最后通过真实 registry 读现有离线快照 `192.168.2.30` 的 GE1/0/1，仅核验原文块，不连接该离线设备。脚本不含口令，只保存脱敏后的元数据/摘要。
   ```powershell
   . scripts/windows/_port_utils.ps1
   Initialize-WindowsRuntimeEnvironment -ProjectRoot (Get-Location).Path
   $env:PYTHONPATH = (Get-Location).Path
   venv/Scripts/python.exe .runtime/verify-config-scope.py
   ```
   脚本用 `open('x')` 创建证明并逐次保存，若已有证明会拒绝再次启动；若中途失败或结果不明，先看已保存记录、stdout 和会话日志，只补核未完成部分，不删除证明后盲目重跑全部 SSH。脚本尚未执行，因此也可能存在验收脚本自身的小问题，应先审阅。
3. 本机只读数据库核查：`NetworkDevice` **0 条**，`display current-configuration` 原始快照 **127 份**，全是无 inventory 关联的离线数据，但有明确 `management_ip`。两台实验 VSR 的 `10.10.1.1/2` 没有对应巡检基线。**不能造一份历史基线或用其他设备快照冒充。** 实机固定查询通过只能说明命令/日志可用，不等于“真实历史基线 → 当前 VSR → AI 根因”的整体验收。需要后续取得合法对应基线后再做这一端到端验证，当前完整对比仅隔离 fixture 已验证。
4. 本次验收并未改变 D-8 首轮 **6/8**，不宣称推理缺陷解决。既有实验故障已于上一轮全部恢复；本轮未注入/改动设备配置。不要为了补齐证明擅自重跑全故障集或扩大厂商/配置范围。

## 1. 先读这些

1. 本文。
2. [路线图](network-digital-twin-roadmap.md)：
   - 第 0 节开头 2026-09-29 的负责人原则（最高优先级）。
   - 第 14 节台账中 D-1～D-8、P2-3 各行（每行有文件、命令、结果、未完成项）。
   - 文末 D-5、D-6、D-7、D-8 四张设计卡：这是负责人逐条确认过的范围，**只做卡上列出的内容**。
   - 第 11.0 节实验环境和迷你故障集 v0。
3. [首版方案](device-access-topology-plan.md) 第 1、3、7 节：SSH 公共能力的分层（平台核心 + SSH 终端 App，拓扑和诊断是调用方）。
4. 需要回看 IETF 模型研究时，再读 [IETF 研读记录](network-digital-twin-ietf-study.md)、[原生模型审计](network-digital-twin-model-audit.md)、[验证包入口](../models/network-twin/README.md)。

## 2. 负责人的工作方式与硬约束（务必遵守）

负责人对以下几点非常在意，违反过就会被严厉纠正：

- **简单即强大，简洁、可靠。这是开源产品。只做负责人要求的，不要自己加层级、字段、选项、权限机制或界面。** 曾因加了管理域、访问范围、凭据集、连接授权、按页面加侧栏等被要求全部删掉。
- **先想清楚再动手**：较大的改动先给出简短设计（要素、分层、标准怎么建模），等负责人确认后再实现；确认后可以自主推进，不必逐步请示。真正需要负责人决定的（尤其安全边界）要停下来问，并给出推荐。
- 顺序：**先建模 → 再用实验数据填充验证 → 最后补采集**；能力是从基础上“长出来”的，基础没做好不要跳到上层。
- 标准基线：**RFC 8345 + SIMAP YANG -03**（负责人称 8345bis），用 SIMAP 双向链路、多点链路；**不增加项目自定义 YANG 字段**。分析结果（定位、路径）以接口 JSON 返回，不新造 YANG 模型。
- **App 是可插拔包**：功能按归属放在各自 App 内，**不要把 App 的页面放进平台“系统管理”**（做过一次已回滚）。**SSH 是平台公共能力**：`src/platform/device_access` 是核心，`device-access` App（显示名“SSH 终端”）是终端入口，拓扑发现和 AI 诊断只是调用方，不要把 SSH 代码并入拓扑，也不要删掉终端。
- 界面风格与防火墙策略 App 一致（`src/apps/firewall_policy/frontend/pages/FirewallPolicyPage.tsx/.css`：面包屑顶栏、圆角窗口、#1671e8、圆角 9、控件高 36）。**负责人当前说“先不做 UI”**，除非明确要求，不要改界面。
- 原巡检 `src/core/patrol`、`src/core/ingestion` 及原 Netmiko 实现不修改。
- **实验设备口令不得写入代码、文档、测试、提交或报告。** 负责人已给实验 VSR（10.10.1.1、10.10.1.2）永久的只读登录授权；口令已加密保存在本机平台的“SSH 终端默认登录”和“拓扑默认登录”里，程序化调用走平台，不要把口令写进脚本。本机开发平台账号见登录页提示（仅 localhost）。
- 回答使用中文。

### Git 纪律

- 不要 `git reset --hard`、`git clean`、`git add .`；**只提交本项文件，用 `git commit -F <消息文件> -- <路径...>`**（工作区可能同时有其他会话暂存的改动，见第 5 节）。
- 提交前 `git diff --cached --check`（或对路径 `git diff --check`）必须通过；失败就停下修正，不要带病提交。
- 修改前后对比时，可 `git worktree add ../netops-agent-verify HEAD` 在干净检出上跑同样的测试，用完删除。
- 提交消息末尾保留 `Co-Authored-By` 之类署名按你自己的工具约定；推送前确认负责人同意（本分支此前已按负责人要求推送）。

## 3. 已完成到哪里（2026-09-28 之后）

| 提交 | 内容 |
|---|---|
| `acb9893`～`192eaf1` | IETF 研究收尾：inventory-topology 虚拟 NE 追踪（Q-IETF-1 定为模型层不关联）、IEEE LLDP/Bridge 模型、ARP/VLAN/LLDP 映射、P2-11 实机补采核查、采集计划按模型缺口重排 |
| `47957d7`～`62749ea` | D-1～D-4 拓扑发现：SIMAP L2/L3 构建器入 App（`model/builder.py`）、发现任务采集全部拓扑事实、快照/导出/画布、按域主机密钥（后被 D-5 简化）、`start.ps1 -ApplyDatabase/-UpgradeApps`、实机界面验收 |
| `f346301`、`f551399` | 画布显示双向链路两端、按角色布局 |
| `2796851`～`b892e8e` | 访问范围相关的来回修改（最终被 D-5 整体删除，不必再看） |
| `c248cb8`、`5287e29` | **D-5**：SSH 终端改为 SecureCRT 式（已保存会话、默认登录、批量改密、标签页、登录失败弹窗）；拓扑只用默认/临时登录，节点登录失败弹窗输入；删除管理域、访问范围、凭据集、连接授权（云端 Claude 会话完成） |
| `476918a`、`f34bfae` | **D-6** 终端定位（`model/locate.py`，`GET /snapshots/{id}/locate`，工具 `topology__locate`） |
| `154c968` | D-5/D-6 实机验收中修的 3 个问题（平台终端未对 SSH 异常分类、窄屏会话列表、节点登录失败自动弹窗） |
| `0c305dd` | 拓扑页统一为防火墙策略外观（布局保持原样，负责人否决了侧栏方案） |
| `3ac3f0c`、`2b86259` | **P2-3 恢复并完成**（固定版本 NTC 模板层；修 VLAN 前言）；**D-7** 双向路径计算（`model/path.py`，`GET /snapshots/{id}/path`，工具 `topology__trace_path`），发现任务开始采集路由表；实机验收 |
| `393b145` | 研究脚本的路由实例改用 App 路由解析器，RFC 8349 实例逐字节不变，26/26 |
| `d652dc4`、`de6a28f` | **D-8** 诊断闭环（见下节）；场景 0 实机通过 |
| `5810e4f`、`46a6c1d` | GPT 接手：场景 1 实机记录；知识问答执行过程展开查看参数与结果 |
| `6899ded`、`a64428f`、`2701275` | 部署排空修复；全平台 SSH 白名单、批量执行与审计；CRT 风格服务器会话日志 |
| `0e82ad3`、`3e95be3` | SSH 终端内编辑命令；恢复受控 Comware 远端 Tab 补全 |
| `089c270` | D-8 预算收尾只允许提交校验后的答案；场景 4-R2 独立记录 |
| `33ec796` | 原始基线与指定现场配置块对比，已部署；实机验收待 Claude 继续 |

## 4. 当前进行中：D-8 诊断闭环验收

设计见路线图文末 D-8 设计卡。负责人选定方案 A：AI 可用保存的默认登录登录路径上的设备，只执行白名单只读命令，每步审计。

实现要点（便于接手排查）：

- `src/apps/device_access/backend/diagnosis.py`：按地址取该地址的已保存会话或默认登录，经平台受控会话（`src/platform/device_access/sessions.py`）执行；L1/L2 执行，L3/L4 不执行；配置导出命令不发送；一次调用一台设备 ≤5 条命令；输出每条截 3000 字节；工具 `device_access__run`。
- `src/apps/assistant/backend/diagnostic_workspace.py`：每轮的登录范围来自本轮 `topology__trace_path`/`topology__locate` 结果中的已采集且平台已识别设备（≤8 台、≤20 条命令）；`platform`、`turn_id` 由服务端填，模型不可见。
- `src/apps/assistant/backend/turns.py`：每轮启用诊断工作区；单轮 300 秒、30 次工具调用、15 轮（平台上限），租约 320 秒。
- `src/platform/agent_runtime/dsh.py`：`DIAGNOSIS` 提示词（排查方法、结论四小节：已证实/候选/已排除/待验证）。
- 审计：`app_device_access_session_audit`，`target_ref` 形如 `diagnosis:<轮次ID>:<设备地址>`。

**2026-09-30 GPT 接手补验**：按 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 启动，全部部署检查通过，无需迁移或升级应用。负责人注入场景 1 后，AI 在不重新发现、不获知故障位置的情况下，命中 VSR-1（10.10.1.1）GE2/0 管理性关闭。15 条命令全部 L1，4 次会话/42 条审计，5 个答案引用核验通过；输出和完整证据分别在 `.runtime/d8-case1.txt`、`.runtime/d8-case1-evidence.json`。两台设备拒绝 `display ospf peer brief` 的参数，本轮未直接取得邻居状态；答案关于邻居断开及对端不可达的部分表述属于推断。详细记录见路线图第 14 节，不能把根因命中等同于每句结论均已证实。

**2026-09-30 自主验收续测**（各轮 ID、统计及限制见路线图第 14 节）：

- 负责人授权覆盖本轮 D-8 故障注入和恢复，不等于给诊断 AI 放开配置权限。平台人工实验操作记 `d8-lab:*`，AI 审计按 `diagnosis:<轮次>` 单独统计；全局 SSH 白名单仍是版本 1、`[".*"]`。实验设备口令不落文件。
- 首轮 0/1/2/5/6/7 命中；3 未提出 PC1 默认网关错误，4 达到 15 轮上限，只返回通用失败提示。场景 4 等待 ARP/MAC 自然老化后才提问，盲测前核实 VSR-2 GE3/0 仍 UP；使用 GNS3 PC1–Switch1 链路暂停传包，没有清表或修改老化参数。各首轮及后续补测的注入均已恢复；最后的场景 4-R2 于 14:22:16 验证恢复，此后最终检查 16 项全部通过，双向 PC ping 各 3/3、OSPF Full、业务路由、VLAN 与临时 ACL 均恢复。最终证明 `.runtime/d8-final-state-proof.json`；13:52 的旧证明单独保存在 `.runtime/d8-final-state-before-case4-r2-proof.json`。
- 场景 3 已按旧方案补测“重新发现后再诊断”：两台 VSR 发现成功，新快照 `a07321f029a04c20b0bc600cf91c39ab` 成为当前快照，旧快照保留。新 MAC 已正确采到，仍未命中网关错误，且答案把实际存在的互联 ARP 读成缺失，不能归因于旧快照一项。记录 `.runtime/d8-case3-fresh-{discovery,evidence,review}.json`；网关已恢复。
- 首轮场景 2–7 合计 101 条 AI 命令（96 L1、5 L2）、26 个会话，诊断审计 280 条、平台审计 332 条。服务器已有的 26 份日志共 86,355 bytes，与审计投影逐字节一致；含初始化共 127 条命令结果。另行核对本轮拓扑发现的 4 个连接、18 条命令（含 2 条初始化），每条均记录策略版本 1；4 份已有日志共 29,995 bytes，同样匹配。以上仅验证默认放行路径及已脱敏留存，未收紧规则做实机拒绝测试；补测和人工实验操作另计。
- 证据留存完整不等于推理正确。重点未闭合项：设备/接口归属混读、旧 MAC 差异误判 IP 冲突、路由器 ping 被称作 PC 间单向连通、未取得 ACL 绑定却确认丢弃、错误引用和单次计数推断趋势。不能用注入脚本知道的真值替 AI 补证。
- 两版 `DIAGNOSIS` 提示词在同一批脱敏固定证据上试验均出现明显错误，全部保留在 `.runtime/d8-prompt-replay*`，没有应用到生产。生产提示词、模型和预算均未改。仅针对场景 4 的预算收尾缺口，平台收尾阶段只提供答案提交工具，在执行前拒绝其他工具，未通过原引用检查的内容不能发布。相关回归 29 项通过、1 项因 Windows 缺少 Linux `/proc` 跳过；13:59 平台重启及 9/9 应用部署检查通过，无需升级 App。补测同时使用新快照，结果须独立记录，不能把变化完全归因于收尾修复。
- 场景 4-R2 轮次 `1e4fee9f18814eefa4f13d92319b3415` 在 8 轮／12 次工具／52.063 秒内完成答案提交，首候选指向 PC1 接入／主机失联；20 个命令条目中 1 条多管道命令被判 L4、发送前拒绝，实际发送 19 条（17 L1、2 L2），4 个会话、52／59 条诊断／平台审计。未达到收尾阈值，不能宣称本次实机触发了强制收尾。证据 `.runtime/d8-case4-r2-evidence.json`，逐句限制见独立复核；补测不改写首轮 6/8。
- R2 仍存在过度排除：未指定源地址的路由器 ping 不能证明 PC 间对称转发；GE3/0 的 UP 和零错误不能排除下游链路／主机问题；未读取 PC1 配置却在开头排除网关问题。此次 `inspection__list_events` 只传 `limit:20`，返回其他设备的历史事件且还有下一页，不能据此排除实验设备告警。命令、引用和日志留存核验通过不消除这些结论缺陷。
- 本轮场景 2–7 及两次补测合计 37 个 AI 会话日志均核验通过，共 122,870 bytes、175 条命令结果（含 37 条分页初始化），不含先前场景 0、1。人工注入／观察／恢复另计：113 个关闭的 SSH 会话、1,111 条审计、166,028 bytes 的已有服务器日志均匹配，证明 `.runtime/d8-lab-server-logs-proof.json`；命令返回完整不代表 CLI 语义成功，原错误和纠正记录保留。
- 复测脚本：`.runtime/d8-case-run.py <2|3|5|6|7> --suffix r2`；场景 4 用独立脚本及持久状态，异常恢复支持 `--restore-only`。单次诊断 `.runtime/d8-eval-run.py <唯一标签>`，若提问结果不明，只用 `--collect <证据文件>` 只读补收，不能重复提交。脚本和原始证据留在忽略目录，不提交。

**下一步**：先解决 D-8 的预算内收尾和结论可靠性，保留首轮与补测各自成绩；不能把固定证据回放或补测成功覆盖原失败。这里只做现有卡的必要修复，尚不扩展快照自动刷新、VRF 或服务端结构化诊断校验。

**负责人后续追加已实施（最新状态见文首交接）**：巡检接入方向最终确定为“基线原文 → 指定块现场读取 → 配置比较”，代码提交 `33ec796`，本机部署通过，实机比较尚待验收。原先设想直接读取变化列表已撤回。`inspection__launch` 仍不开放给诊断，不发起新巡检；`read_batch` 仍不是普通诊断证据工具。本次只改已确认的配置比较提示与工具，`thinking` 仍为 `disabled`，模型和既有轮次/时间上限没有改动。

注意：Comware 默认不回 TTL 超时，`tracert` 中间跳显示 `* * *`，这是设备默认行为，不是故障；开启需要改配置，不要做。

验收完成后，下一张卡由负责人决定。路线图里的候选：快照增量刷新（P4-2）、P2-10 LLM 兜底与模板沉淀、多厂商、VRF。**先给设计、等确认。**

### 4.1 执行结果展示（2026-09-30 负责人追加要求）

知识问答已升至 0.8.1：每条执行记录默认一行收起，点击后展示真实参数与结果；SSH 输出按设备、命令逐条展示，保留表格列对齐。业务结果展开时才读取，并核对本轮归属和源 App 当前权限，公共进度/SSE 不直接携带业务正文。历史记录只恢复可唯一匹配的已留存证据：场景 1 的 4 次 SSH 与 1 次路径结果可看，2 次定位结果未留存，页面明确提示。未为展示重跑诊断或设备命令。测试、部署与浏览器验收详见路线图第 14 节“负责人追加：执行过程展开查看结果”；本次界面授权仅覆盖此项。

### 4.2 全平台 SSH 白名单、批量执行与操作记录（2026-09-30 负责人确认，已完成部署与本地验收）

负责人另行确认本支线，设计与台账见路线图 **S-1**。本次明确授权 SSH 终端相关界面及既有在线 SSH 调用接线；只在此范围内调整前文“暂不做 UI／原巡检不修改”的限制，不扩展 D-5～D-8，不改采集模型或解析语义。

- 平台统一维护一份管理员正则白名单，完整命令匹配任一规则才发送；默认 `[".*"]`，空列表拒绝全部。规则覆盖终端、批量、诊断及既有在线平台 SSH 调用，含初始化/识别命令；**AI 仍叠加 D-8 的 L1/L2、设备范围和预算限制**，不因默认放行而获得配置权限。
- 终端内本地编辑完整单行命令，按回车发送；本地按键不直接转发设备，不提供任意控制字符透传。受控远端 Tab 的后续追加见 4.5。设备要求密码、分页或交互确认时停止；命令失败不自动重试。批量按顺序执行，最多 20 个会话、每台 50 条命令，整批 15 分钟；支持停止，取消不能回滚已经执行的命令。
- 白名单存平台 `netops_ssh_policy`（迁移 `0107_ssh_governance`）；所有在线 SSH 操作写入平台 `netops_audit_logs`，普通用户查看本人、管理员查看全部，命令和输出按已知 secret 及敏感内容脱敏，现有 D-8 会话审计保留。源码中的独立实验采样脚本不属于在线平台调用范围；实验设备口令仍不得写入任何文件。
- 已部署：`device-access 0.9.0`、`device-management 1.0.4`、`inspection 1.3.1`、`rule-governance 1.4.6`；拓扑仍为 0.4.3，知识问答仍为 0.8.1。部署前备份：`backups/20260930-023847-before-ssh-governance/postgres.dump`，109079394 bytes。升级命令包含依赖拓扑：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology,device-management,inspection,rule-governance -NoBrowser`，退出 0；最终 `verify.ps1` 退出 0，9/9 已启用 App 以及 FastAPI/BFF/React/Worker 全部通过。排空旧/新包摘要比较修复已单独提交 `6899ded`，生命周期回归 10 项通过。
- 验证：公共 SSH 回归 663 项通过，修补后相关 72 项重跑通过（与前批部分重复，不相加）；App/主机密钥/人工命令/终端分组最终 56 项通过（包含此前 18 + 5）；治理分组 20 项通过（含审计时间修复）；前端 22 项通过，改动文件 ESLint 与 diff 检查通过。旧在线调用链最终隔离回归为 119 通过、1 跳过、7 排除；跳过项需要独立 PostgreSQL 验证 advisory lock，7 项为原始 HEAD 已复现的既有失败，详情见 S-1 台账。
- 实机：两台 VSR 各执行 `display clock`、`display ip interface brief`，4 条均成功；批次 `30fe653e4efd44c08a06b536c05183a7` 留存 16 条审计。另在 VSR-1 单终端执行 `display clock` 成功后停止，审计完整。默认 `[".*"]` 未改，未改实验设备配置、未重跑 D-8。截图 `.runtime/ssh-batch-preview.png`、`.runtime/ssh-audit-preview.png` 不提交。
- 审计页面曾多显示 8 小时，现于查询侧按既有数据库 `TimeZone` 解释无时区时间，不改写入方式、不迁移历史。网关重启后健康检查正常，浏览器确认时间从错误的 18:54 恢复 10:54，展开命令仍保留设备返回的 02:53:44 UTC。

### 4.3 按连接自动保存 SSH 会话日志（2026-09-30 负责人追加确认，已完成部署与本地验收）

- S-1 追加 CRT 风格的会话日志：平台复用 MinIO，在 `platform/ssh-session-logs` 前缀下按连接 ID 隔离目录，自动保存“设备名_北京时间（精确到微秒）.log”。新连接的日志逐事件持久化，写入失败即停止；重试使用新连接 ID，首条日志写入失败也正确收尾。终端、批量、诊断和既有在线调用都传递连接时的设备名快照；Netmiko 初始化先记录日志，再检查命令白名单。
- 操作记录默认显示“日志文件／状态／下载”三列，一次连接一行；文件名下显示地址、连接时间、来源，管理员另见用户。点击预览连续纯文本命令与回显，下载经过现有认证；地址筛选、详情失败重试及切换文件的竞态保护已实现。管理事件保留在独立“管理记录”入口，普通用户仍只读本人、管理员可读全部。
- 原平台审计不删，白名单不改，本次追加不新增数据表或迁移。历史只按连接恢复已留存内容，没有设备名快照时用 IP；不补执行 SSH，也不把恢复内容称作完整原始 PTY。日志沿用脱敏边界，保留实际缺失、失败或截断提示。
- 验证：前端 18 项、App 日志 API 7 项通过；平台日志相关 86 项与拓扑识别 92 项是不同用例，合计 178 项通过；名称接线 73 项、drivers/legacy 61 项（含 4 项新增）通过。后两组与其他分组有重叠，不累加为独立总数。相关 Ruff、前端 ESLint、diff 检查通过，真实 MinIO 健康检查返回 200。
- 已部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9 个应用及全套启动检查全部通过；`device-access 0.9.1` 已启用，拓扑仍为 0.4.3。
- 本轮实机通过页面批量仅在 VSR-1、VSR-2 各执行一次 `display clock`，均成功。打开日志列表或预览前，只读脚本直接读取 MinIO 两个新对象，证实服务器已自动保存 `VSR-1_2026-09-30_11-31-16.065586.log`、`VSR-2_2026-09-30_11-31-18.535872.log`，各 483 bytes；该核验未调用 `read_log`／`sync_log`。随后页面列表与完整预览验证通过；浏览器实际点击下载成功，VSR-1 文件保存到 `C:/Users/wangd/Downloads/VSR-1_2026-09-30_11-31-16.065586.log`，483 bytes，SHA-256 为 `476c7e1f7b3b0590d37605d857d977d85b56dd19001078e0193c4cf27ce346ff`，与预览前 MinIO 原文件完全一致。未改设备配置或白名单。
- 自动保存证明 `.runtime/ssh-session-files-proof.json`，页面截图 `.runtime/ssh-session-files-preview.png`、`.runtime/ssh-session-log-preview.png`，均留本地、不提交。

> **2026-09-30 已被取代**：4.4 本地编辑与 4.5 远端 Tab 已删除，改为按键直通 + 回车拦截白名单（参照 JumpServer），见路线图第 14 节“终端按键直通 + 回车拦截白名单”。以下两节仅作历史记录。

### 4.4 终端内本地编辑命令（2026-09-30；**已被 `c245d4e` 按键直通取代，勿恢复浏览器侧行编辑**）

- 共享前端 `DeviceTerminal.tsx` 改为直接在 xterm 内输入，去掉底部独立输入框、执行按钮和旧说明。新增 `LocalCommandLine` 仅处理本地编辑；Enter 仍发送完整 `command` 帧，沿用后端白名单和会话日志，不改变后端协议、App 版本或数据库。
- 支持插入、左右、Home/End、退格/Delete、↑↓当前会话历史、Unicode 及长命令单行水平滚动。单行粘贴不会自动提交，多行或控制串粘贴拒绝；执行中禁止新提交，被拒绝后恢复草稿。远端回显前擦除本地草稿，避免命令重复显示。
- 验证：组件 12 项、编辑辅助 13 项联合通过，共 25 项，包含真实 xterm 的缩宽和回显，以及 UTF-8 字节帧超长时不发送、可修正后再提交；4 个前端文件 ESLint 全部通过。生产构建通过（15.95 秒），刷新页面已生效，无需升级 App，SSH 终端仍为 0.9.1；最后仅追加测试，生产代码与实机验收版本一致。
- 实机在 VSR-1 浏览器终端直输 `display clocx`，退格修正为 `display clock`，仅按一次 Enter，命令成功且回显仅出现一次；↑↓切换草稿及拒绝多行粘贴均已验证，随后停止连接。直接只读 MinIO 的 `VSR-1_2026-09-30_11-47-33.663235.log`（716 bytes），确认仅有 1 条 `display clock`、成功结果及连接结束，多行粘贴未产生额外执行记录。核验脚本 `.runtime/verify-inline-input.py`、截图 `.runtime/ssh-inline-input-preview.png` 留本地、不提交。
- 本轮本地编辑未包含远端 Tab；负责人后续确认其独立设计后另行实施，进展见 4.5。

### 4.5 Comware 受控远端 Tab（2026-09-30；**已被 `c245d4e` 取代：按键直通，设备自己做 Tab/?/历史，回车时服务器按白名单检查**）

- 负责人已确认方案，平台、App 协议与共享前端接线已完成。首批仅对 Comware 7 开放，须同时匹配 SSH banner 与 CLI 提示符；首次补全通过原白名单和审计执行 `display hotkey | include (hotkeys|CTRL_[EXR])`，只接受系统保留的 Ctrl+E/X/R 清行与重绘定义，不使用可重绑快捷键或禁分页配置命令。
- 光标在行末时，Tab 发送独立 `complete` 帧，包含请求 ID、草稿版本和 `tab_count`（默认 1，上限 32，仅作前端轮次与审计）。每个请求只向设备发送一次初始草稿加 Tab，连续请求沿用初始草稿，候选随设备自身状态推进；任何本地编辑重置前端轮次，不假定设备候选索引归零。补全期间冻结本地编辑，过期响应不应用。输入限受支持的单行 ASCII 前缀、最多 256 字符，返回最多 511 字符；不发送换行。完成后清行并核对空提示符，无法确认、超时或日志失败即停止连接。Enter 仍发送完整 `command`，重新检查当前白名单，补全结果不授予执行权限。
- 请求、补全结果及前置校验拒绝沿用平台审计和服务器会话日志；协议去重区分命令与补全，同一请求 ID 改内容会终止连接。补全纳入既有会话时限、输入/输出预算，并另设草稿长度、响应大小与等待时限边界；单次草稿补全及清理共限 3 秒，首次快捷键查询单独限 15 秒。
- 验证：最终 4 组后端联合 95 项通过（29.38 秒），前端 37 项通过，相关 ESLint、Ruff 和 14 个任务路径的 diff 检查通过；最新生产构建通过（8.40 秒）。这些是本轮分组结果，不与前文各轮回归累加。
- 已部署 `device-access 0.9.2`，拓扑仍为 0.4.3：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9/9 应用检查通过。最终平台代码以 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 重新加载，数据库检查通过、无需再次升级 App；该命令退出 0，最终 9/9 应用及 React/BFF/FastAPI/Celery 全部通过。
- 实机公共 `HumanCLI` 连续 3 个请求各发送一次 Tab，`display cl` 依次得到 `client-verify`、`clock`、`cloud-management`，执行 `display clock` 成功后仍可再补全。浏览器最终实测第一次 Tab 得到 `client-verify`，第二次得到 `clock`，按 Enter 成功返回 `04:22:25 UTC`，命令回显仅一次，随后停止连接。
- 页面预览前直接只读 MinIO 的 `VSR-1_2026-09-30_12-21-42.776582.log`，1534 bytes，会话 `aa1aa0ddfbd146258a0c13648b288a27`；仅执行一次初始化只读快捷键查询和一次 `display clock`，两次补全请求/结果、远端已清空和连接结束均留存。`.runtime/verify-tab-completion.py` 断言通过，证明 `.runtime/ssh-tab-log-proof.json`，截图 `.runtime/ssh-tab-completion-preview.png`、`.runtime/ssh-tab-result-preview.png`，均留本地、不提交。本轮没有修改设备配置或白名单，实验设备口令未写入文件。

## 5. 本机状态与注意事项

- 已部署（本机）：知识问答 0.8.2、SSH 终端 0.9.3、拓扑 0.4.3、设备管理 1.0.4、巡检 1.3.2、规则治理 1.4.6。数据库在 D-5/D-6 前备份于 `backups/20260930-001850-before-d5-d6/`；S-1 前备份于 `backups/20260930-023847-before-ssh-governance/postgres.dump`（109079394 bytes）。
- 本机平台里已配置：SSH 终端默认登录与两条会话 VSR-1/VSR-2；拓扑默认登录；两台 VSR 的主机密钥。“实验网络 lab”的当前快照为 `a07321f029a04c20b0bc600cf91c39ab`（本轮拓扑 0.4.3 发现，含路由表与当前终端 MAC），原快照保留。
- 部署命令：`scripts\windows\start.ps1 -SkipLangfuse -UpgradeApps <app,...> -NoBrowser`。
  - **修改任何 App 的代码后，该 App 的摘要与已部署版本不一致，平台会把它判为不可用**（依赖它的 App 也会不可用），直到用 `-UpgradeApps` 重新部署。
  - 只改平台代码（`src/platform`、`src/gateway`）时不用升级 App，但要重启服务（不带参数运行 `start.ps1`）。
  - 如果服务全停（机器重启后常见），`-UpgradeApps` 会在“排空”步骤报 `ConnectError: drain incomplete`：先临时起一个网关让排空检查通过，再重跑。做法见台账 D-5 行；临时网关会被脚本在第 3 步自动停掉。
  - Docker Desktop 需在运行；刚启动时 PostgreSQL 可能短暂连不上。
- 工作区里有**不属于本线工作的改动**，不要提交也不要丢弃：
  - `web/react_frontend/src/pages/{ChatPage,ChatPage.test,SkillsPage,StatusPage,StoragePage}.tsx` 已暂存：另一个会话在修 `components/Layout` 的大小写引用（Linux 上构建失败的既有问题），由它自己提交。
  - `web/react_frontend/src/apps/generated.ts`、`generated.meta.json`：按本机环境生成的 App 注册摘要，历来不提交。
- `git stash` 里的 “P2-3 paused work…” 和 `backups/p2-3-paused-20260929/` 已经过时（P2-3 已在 `3ac3f0c` 提交），只作备份，删除前征得负责人同意。
- 已知与本线无关的失败测试（在干净检出上同样失败）：`test_workflow_plugins.py` 3 项、`test_rule_asset_bundles.py`、`test_command_autopilot_integration.py`、`test_check_item_assessment_runtime.py`、`test_chat_intent_match.py` 各 1 项。`dsh.py`、`src/gateway/schemas.py` 有既有的 Ruff 行长/导入顺序问题，不要顺手整文件格式化。
- S-1 旧调用链另有 7 项原始 HEAD 既有失败（设备管理 2 项、巡检 runner 5 项，名单见路线图 S-1 台账），与上一条旧交接的 7 项不是同一组。最终隔离回归为 119 通过、1 个 PostgreSQL advisory lock 用例跳过、7 项排除，不能称全套无失败。
- 早期旧测试隔离不完整，在本机留下 3 条 `operator-1`／`127.0.0.1`／`patrol-1` 的回环测试审计（opening/open_failed/closed）。未连接实验设备，记录保留，不是实验设备故障；后续治理及旧调用边界单测已使用临时 SQLite 并阻断连接本机 PostgreSQL。本项以实机批次及用户 `1` 的记录为验收依据。
- 本机 pip 配置的镜像是 http，会被拒绝；需要安装时用 `--index-url https://mirrors.aliyun.com/pypi/simple/`，不要改全局配置。

## 6. 常用验证

```bash
# 拓扑 / SSH 终端 / DSH / 知识问答相关后端（约 3 分钟）
venv/Scripts/python.exe -m pytest tests/unit -k "topology or device_access or dsh or assistant" -q -p no:cacheprovider
# 前端（在 web/react_frontend 下）
npx vitest run ../../src/apps/topology/frontend ../../src/apps/device_access/frontend src/platform/terminal
npx eslint --no-eslintrc -c .eslintrc.cjs --resolve-plugins-relative-to . <文件>
npx playwright test e2e/topology.spec.ts
# 模型研究（研究环境，见下表）
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py
```

| 用途 | 路径/版本 |
|---|---|
| 应用 Python、Ruff | `venv/Scripts/python.exe` |
| 独立模型研究 Python | `.runtime/yang-model-tools/Scripts/python.exe`，pyang 2.7.1、yangson 1.6.3；2026-09-30 加装 textfsm 2.1.0、ntc-templates 9.1.0（与拓扑 App 锁定一致，供实例构建调用 App 的路由模板层；本机 pip 镜像为 http，安装时显式用 `--index-url https://mirrors.aliyun.com/pypi/simple/`） |
| 已构建模型包 | `.runtime/ietf-study/model-package/` |
| 锁定源缓存 | `.runtime/ietf-study/reproducible-audit/sources/` |
| libyang 验证镜像 | `netops-ietf-mount-verify:5.8.6`（构建与来源锁在 `models/network-twin/mount/`） |

## 7. 已踩过的工具和标准边界（沿用上一份交接）

1. SIMAP 原文不能编译是原文问题（作者仓库同类错误），沿用锁定的兼容补丁，不要改写原文或草案语义。
2. Yangson 不自动实现 Schema Mount；挂载验证用 libyang 5.8.6（3.12.2 会崩溃，不算负例）。严格验证不传 `-t`，用 `--merge`；`-t get` 只是解析。
3. RFC 8342 §5.3 不是缺字段的豁免；部分观测保留缺项诊断，不编造值（如计数器中断时间）。
4. 核对真实字段名：RFC 8529 用 `bind-ni-name`；路由状态下一跳用 address、无 index；双下一跳不证明数据平面 ECMP。
5. 生成文件的行尾会影响摘要：`.gitattributes` 固定了 LF 的目录，新增生成目录注意同步；Windows 下 Python `write_text` 会写 CRLF，脚本里用 `write_bytes` 或 `newline="\n"`。
6. 路由表按设计走模板层（7.3），**不写通用正则解析路由**。模板固定 ntc-templates 9.1.0 与逐文件 SHA-256。
7. 定位/路径只用快照观测，确定性计算，不让 LLM 猜；数据不足就以明确原因结束分支。
