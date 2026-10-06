# M1：完整模块基线与语义验收框架

按[实施方案 §8 M1](../../../docs/network-digital-twin-implementation.md) 执行的结果。输入是 357 项标准目录（`docs/network-digital-twin-ietf-model-catalog.json`）、IANA YANG 注册表（固定摘要 `2253c107…`）和已固定的 YangModels 提交 `0e28ed86`。全程不自建 YANG：模块只来自 IANA、标准/草案原文和 IEEE 已发布目录；原文有错时用带输入/输出摘要的补丁修复，并逐条公开。

## 复现

```powershell
venv/Scripts/python.exe -X utf8 scripts/topology/m1_sources.py --fetch    # M1-1 来源锁
venv/Scripts/python.exe -X utf8 scripts/topology/m1_decisions.py          # M1-2 去留判定
.runtime/yang-model-tools/Scripts/python.exe -X utf8 scripts/topology/m1_sets.py   # M1-3/4 能力集编译、树与节点清单
venv/Scripts/python.exe -X utf8 scripts/topology/m1_mapping.py            # M1-5 映射清单
venv/Scripts/python.exe -X utf8 scripts/topology/m1_cases.py --build      # M1-6/7 正反例与 RFC 9195 文件
venv/Scripts/python.exe -X utf8 scripts/topology/m1_upgrade.py            # M1-8 草案升级回放
```

下载缓存在 `.runtime/m1/cache`（不提交）；首次运行加 `--fetch`，之后离线复现。M1-3 使用研究环境 pyang 2.7.1、yangson 1.6.3，以及挂载验证已建好的 libyang 5.8.6 镜像 `netops-ietf-mount-verify:5.8.6`（断网、只读、非特权容器）。M1-5/6/8 只需应用 venv 和该镜像。

## M1-1 来源锁（`sources.lock.json`）

- 目录中 376 个模块名，锁定 343 个；未锁定的 33 个都是 SMIv2 MIB（不是 YANG，见 M1-2）。连同依赖、IEEE 已发布目录 73 个模块和同一模块的其他现行修订，共锁定 464 个文件（IANA 209、标准/草案原文 178、YangModels 77）。
- 优先级：IANA 注册表文件 → 定义它的目录文档原文（`<CODE BEGINS>` 块，去页眉页脚）→ 固定 YangModels 提交。目录给出 `source_sha256` 的文档逐个核对，全部一致。
- 同一模块的现行文档若给出更新的修订（如 RFC 8776 的 te-types 与 8776-update 草案、RFC 8347 与 8347bis），两个修订都锁定；被废止 RFC 和过期草案只作参考，不锁定。
- 目录外依赖只按导入方引用的文档锁定：ALTO OAM 引用的 `draft-ietf-netconf-http-client-server-33`（RFC 编辑队列）。
- 仍未解决的 8 个依赖都写明原因：BGP-LS YANG -04 导入的 `iana-bgp-types` 在 BGP 模型 -20、-21 中都不存在（导入方缺陷）；过期 EVPN 草案依赖的 L2VPN/伪线草案；过期 flexi-grid/WSON 草案钉住的 ietf-te 2019/2021/2022 等旧修订。

### 原文缺陷与补丁（`patches/`）

| 来源 | 缺陷 | 处理 |
|---|---|---|
| draft-havel-nmop-simap-yang-03 | 模块无 CODE 标记；缺两个 import、引号提前闭合、两处 list 未闭合 | 按审计锁同一规则截取 §9（摘要 `82b7e6c2…` 一致），复用已审查的 `../patches/simap-03-syntax.json` |
| draft-ietf-teas-yang-te-44 | CODE 块缺 `module ietf-te {` 行；描述中的 `"YANG Parameters"` 未转义 | `ietf-te-44-syntax.json`：只补模块头、转义两个引号；输入/输出摘要锁定，差异见 `ietf-te@2026-03-26.patch` |

## M1-2 去留判定（`decisions.json`、`decisions.md`）

按修订版判定再汇总到模块：运行基线 309、候选升级 105、参考 61、不适用 2（RFC 9907 编写模板，修订日期是占位符）。修订版层面：基线 312、候选 109、参考 40。

- 运行基线：最新的已发布 RFC 修订（IANA 文件引用 RFC 即视为已发布，含 IANA 维护模块）、IEEE 已发布模块、方案固定的草案（SIMAP -03、IVY 六项、GREEN、BGP -21）。
- 候选升级：比基线更新的现行草案修订，或尚无发布版本的现行草案。
- 参考：被取代的修订、仅由废止 RFC 定义、过期/被替代草案（EVPN、VXLAN、L3VPN 设备模型、MPLS 静态、RSVP-TE、BIER、STAMP 等真实网络常见能力目前只有过期 IETF 草案，后续里程碑需另行决定）、33 个 MIB（映射依据）。
- 基线模块导入尚无发布版本的模块时，该修订随基线进入并注明原因：BGP -21 导入 `ietf-yang-semver` 草案。

## M1-3 能力集编译（`sets.json`、`sets/*.json`）

11 个能力集，各有基线和候选两种组合（无候选模块的集只编基线）。`*-state` 兼容模块只放在 `nmda-state-compat`，不混入它所镜像的 NMDA 模块集。每个集记录根模块、闭包内每个文件及摘要、缺失依赖、三种工具的结果。

| 能力集 | 组合 | 文件 | pyang | yangson | libyang 5.8.6 | 数据节点（配置/状态） |
|---|---|---|---|---|---|---|
| language-datastore | 基线/候选 | 16/19 | 0 错 | 通过 | 通过 | 160/166 |
| topology | 基线 | 13 | 0 错 | 通过 | 通过 | 292（135/71） |
| inventory | 基线 | 15 | 0 错 | 通过 | 通过 | 369（54/306） |
| device | 基线/候选 | 37/42 | 0 错 | 通过 | 通过 | 1760/1864 |
| ieee-l2 | 基线 | 82 | 0 错 | 工具边界 ① | 工具边界 ② | 3656（1625/1850） |
| routing | 基线/候选 | 88/120 | 0 / 1 错 ③ | 工具边界 ① | 通过 / ③ | 8413/13176 |
| services | 基线/候选 | 30/49 | 0 错 | 通过 | 通过 | 5191/8008 |
| policy-security | 基线/候选 | 28/44 | 0 错 | 通过 | 通过 | 1057/2387 |
| assurance-oam | 基线/候选 | 68/79 | 0 错 | 原文问题 ④ | 标准冲突 ⑤ | 2912/3413 |
| te-transport | 基线/候选 | 40/88 | 0 错 | 通过 / 工具边界 ① | 通过 | 5892/15694 |
| nmda-state-compat | 基线/候选 | 24/29 | 0 错 | 通过 | 通过 | 3960/5716 |

1. yangson 1.6.3 不能解析嵌套通知/RPC 中越出该节点的 leafref（BGP `neighbors/established`、IEEE CFM 告警、TE 路径计算）；pyang 与 libyang 均接受，路径按 RFC 7950 核对正确。
2. libyang 的 PCRE2 对 IEEE 802.1CB `mask-and-match` 的 `{1,1983}` 重复上限报“正则过大”；pyang 接受。
3. 即 M1-1 的 `iana-bgp-types` 缺失；只影响候选的 BGP-LS。
4. RFC 6728 `ietf-ipfix-psamp`（YANG 1.0）在双引号中写 `"\S+"`；pyang 只警告，yangson 拒绝。
5. RFC 8532 `ietf-connectionless-oam` 把 `yangmnt:mount-point` 放在 anydata 上，RFC 8528 §3.1 规定只能用于 container/list；libyang 的拒绝是正确的。未修补。

TE 候选集一开始有 254 个错误（新草案引用 8776-update 才有的 grouping）；按修订锁定 8776-update 后清零。

## M1-4 树与约束清单（`inventory/*.jsonl.gz`）

每个集的完整 RFC 8340 树在 `.runtime/m1/sets/<集>/tree.txt`（可复现，不提交）。节点清单每行一个数据/操作节点：RFC 7951 成员路径（命名空间变化处加模块名，不含 choice/case）、所属模块、类型链（含 typedef 的 range/length/pattern/units/default）、leafref 路径与 require-instance、identityref 基、枚举、key/unique/mandatory/min/max-elements、when/must/if-feature（含 uses、augment、choice/case 带来的条件）和所在 choice/case。gzip 固定 mtime，内容不变则字节不变。

核对：在 yangson 能装载的每个集中，清单的数据树路径与 yangson 自己的 `data_path()` 集合完全一致（差异 0；唯一例外是 `not anon-profile` 这类否定 if-feature 节点，已解释并排除）。限制：`sx:structure`/`rc:yang-data` 结构（如 RFC 9195 头部、SID 文件）不在清单中。

## M1-5 映射清单（`fact-mapping.json`、`mapping.json`、`mapping.md`）

基线去重后 21403 个数据节点。用发现流程同一套解析器和构建器处理 campus-wan 样本，App 实际输出 85 个路径，研究实例输出 133 个，**全部是基线集中的节点**（无项目自定义节点）。

当前解析器采集的 77 个字段：已导出 21、可映射但未导出 31、键不可得 13、无原生节点 8、作用域 4。

- 可映射未导出：接口 admin-status/description、VLAN 与成员、PVID、路由（当时不臆造 RIB 名称，所以不输出路由；2026-10-06 起按负责人决定由“实例 + 地址族”推出 RIB 名并导出，见 M4 README）、VRRP priority、IVY 厂商/型号等，是 M2/M3/M4 的直接工作量。
- 键不可得：FDB 需要 database-id 和桥端口号；LLDP 远端需要 time-mark/remote-index，端口需要 dest-mac-address；本地管理地址按 802.1AB 挂在发送端口下；序列号属于 component-id 未知的机框组件。
- 无原生节点：CLI 简写接口名、ARP 老化时间、VLAN active/suspend、Cisco DTP 模式等，作为证据留在 YANG 树外。
- 部分实例：已输出的接口缺 `statistics/discontinuity-time`，VRRP 实例缺 `version`；`admin-status`、`if-index` 受 `if-feature if-mib` 约束；LAG/微波节点受 when 约束。

## M1-6 正反例与校验器（`cases/`、`reports/cases.json`）

33 例全部符合预期。校验器用 libyang 三种模式区分缺口：get（只解析）、data（运行态，按 RFC 8342 §5.3 容忍）和 strict（完整数据存储）。

| 缺口 | 判定方式 | 实例（真实来源） |
|---|---|---|
| schema | 成员不在模型中；模块装载失败 | 自定义字段；BGP-LS 缺 `iana-bgp-types`；flexi-grid 钉住不存在的 ietf-te 2019-02-15；RFC 8532 mount-point |
| 语义 | 类型/范围、未知 identity、choice 冲突、越 scope 引用、when/must、单位 | VRRP priority 300；未知接口类型；next-hop 同时出现两个 case；出接口指向 DIST1 的接口；SIMAP 链路端点指向不存在的节点；秒值映射到厘秒节点 |
| 采集 | 解析与引用都通过，只缺 key/mandatory | BR1/DIST1 设备树、VSR L2 部分实例 |

App 输出中，BR1 LLDP、园区拓扑和 VSR 路由在 strict 下完整（原生完整实例），设备树和 L2 是部分实例。RFC 8345 的 `tp-ref/node-ref/link-ref` 及 supporting 引用均为 `require-instance false`，SIMAP 链路沿用，YANG 校验不会拒绝悬空端点；校验器增加规则：同一快照内每个引用必须可解析。单位不在 YANG 校验范围内，按映射声明的单位与节点 `units` 比对。

## M1-7 RFC 9195 实例文件

`cases/netops-*@<时间戳>.json` 把快照封装为 RFC 9195 实例数据集：`format-version`、`name`、`content-schema`、`description`、`datastore`（operational）、`timestamp`，文件名按 §2 为“名称@时间戳”（冒号换成下划线）。RFC 9195 §2 允许部分数据集违反 mandatory/min-elements/require-instance/must/when，这正对应“部分实例”的结论；完整性仍按 M1-6 单独给出，不靠文件格式声明。

- 闭包每个模块只有一个修订时使用 simplified-inline（隐含全部 feature、无 deviation）；有同名多修订时必须改用 inline RFC 8525 库。VSR L2 文件示范 inline，内嵌库本身也按 RFC 8525 校验通过。
- 校验时只使用文件自己声明的 content-schema，不使用外部闭包。反例：simplified-inline 列出两个修订会被拒绝（§2.1.2）。
- 实例文件只承载标准数据；应用证据（命令、原文、解析器）仍按现行封装留在树外，两者不混用。

## M1-8 草案升级回放（`reports/upgrade.json`）

有 4 个基线模块存在更新的候选修订：

| 模块 | 修订变化 | 节点 | identity | 旧快照回放 |
|---|---|---|---|---|
| ietf-te-types | RFC 8776 → 8776-update -24 | 无数据节点变化 | +49 | 未受影响 |
| ietf-te-packet-types | RFC 8776 → 8776-update -24 | 无 | +8 | 未受影响 |
| ietf-layer0-types | RFC 9093 → 9093-bis -20 | 无 | +39 | 未受影响 |
| ietf-vrrp | RFC 8347 → 8347bis -23 | 删 9、增 11、改 6 | 删 `master` 等 3 个，增 `active` 等 3 个 | **3 个快照失败** |

8347bis 把 master 改称 active，例如 `master-down-interval`→`active-down-interval`、状态 identity `master`→`active`。保存了 `state: ietf-vrrp:master` 的 BR1/DIST1 快照及其 RFC 9195 文件在新修订下报 unknown identity。采用 8347bis 前必须先做值和路径转换并重新回放；三个加法式升级可直接采用。

## 未完成与边界

- 能力集只说明“标准可编译、可装载、可校验”，不代表平台已支持这些能力。
- 8 个依赖未解决，原因见 M1-1；过期草案覆盖的常见能力（EVPN/VXLAN/MPLS 静态等）留待 M4 决定。
- yangson 工具边界（嵌套通知 leafref、YANG 1.0 宽松转义）和 libyang 正则上限已逐项记录。IEEE L2 集目前没有能整体装载它的实例校验器；L2 实例改用按实例闭包校验（M1-6），与 IETF-1.8 的做法相同。
- 结构扩展（`sx:structure`）不在节点清单中。
