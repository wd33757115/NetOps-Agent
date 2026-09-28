# 网络数字孪生开发交接：Codex → Claude

交接日期：2026-09-28。项目根目录：`C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，PowerShell，分支 `feat/network-twin`。本交接前的最后一个实现提交为 `ec81310`，路线图当前版本 v1.11。

**当前在做 IETF 原生模型研读、组合与离线实例验证，没有恢复采集开发。已完成验证包与若干具体模型验收，不是完整数字孪生已实现。下一步首先处理虚拟拓扑节点→清单 NE→设备数据树的标准关联缺口，再推进尚未审查的协议模型。**

## 1. 先读这些文件

1. [路线图](network-digital-twin-roadmap.md)：先读第 0 节最新规则，再读第 14 节 IETF 台账。第 13 节旧任务顺序已被“模型先行”覆盖，不能直接恢复 P2-3。
2. [IETF 研读记录](network-digital-twin-ietf-study.md)：标准范围、草案状态、撤回的旧假设。
3. [原生模型审计](network-digital-twin-model-audit.md)：SIMAP 36 项需求、原文问题、关联语义、后续验证记录。
4. [验证包入口](../models/network-twin/README.md)、[关联核验](../models/network-twin/associations.md)。
5. [挂载及运行状态验证](../models/network-twin/mount/README.md)、[路由语义验证](../models/network-twin/routing-semantics.md)。

这些文档包含分阶段的历史结论。较早的“挂载未验证”“SIMAP 未编译通过”等文字描述当时的工具/原文状态，应结合后续兼容包、libyang 验证看，不要删除历史失败证据，也不要忽略后续成果而重做一遍。

## 2. 用户已经确定的约束

- **先完整理解 IETF 相关内容，再按原生完整模型建模；没有项目自定义模型。** 不按实验样本裁剪 Schema，不补项目实体、字段、关系、identity、augment、namespace，不利用 anydata 夹带自定义结构。研究用的文件清单、来源摘要、测试报告不是网络模型字段，不能灌入 YANG 实例。
- 用户已明确采纳原称“8345bis”的补充工作。当前固定基线是 RFC 8345 + `draft-havel-nmop-simap-yang-03`，概念配套 `draft-ietf-nmop-simap-concept-13`。保留个人/工作组草案真实状态，不能因为尚非 RFC 而排除，也不随上游 HEAD 自动升级。
- 用户授权过独立、可审查的 **SIMAP 语法兼容补丁**。只修已定位语法问题，保留原文、精确差异与前后摘要；没有授权任意补写草案 TODO、feature、默认值或语义。其他草案不能自动套用这个修补授权。
- **原巡检现在不改，以后也不迁移。** `src/core/patrol`、`src/core/ingestion` 及原 Netmiko 实现不修改。独立公共设备访问能力、拓扑 App、人工终端和 DSH 调用方已经存在，后续在这些模块演进。只读复用边界见路线图第 0 节。
- 拓扑发现由种子出发按 LLDP 扩散，初始不知道对端型号；识别后才选择平台能力。用户名/密码由人工提供，认证或连接失败需要人工确认或更新凭据。已实现的凭据库和受控 SSH 保留，不能为了模型研究改动访问策略。
- 团队共同维护，管理域隔离结论已确定。后续 DSH 会调用公共 SSH 做网络故障根因分析。当前采集路线仍是 SSH，不能自行切换 SNMP。
- **P2-3、P2-10 及后续采集扩展暂停。** 保留未完成代码，不提交成“验收完成”，不发布模型研究成果到运行应用。
- 每完成一项更新路线图第 14 节：文件、实际命令/结果、未完成项。用户曾授权连续推进，不必逐项等确认；发现方案逻辑错误应按标准纠正并记录。未覆盖且影响模型或安全边界的决定，按第 0 节登记待决项，不靠自定义关系绕过去。

本文件不复制账号口令。若后续获准进行实机验收，使用原凭据库/安全输入流程；当前下一步可以离线完成，不需设备登录。

## 3. 已完成到哪里

| 提交 | 内容 | 验证与范围 |
|---|---|---|
| `f9bd738`、`ac9aaab` | 原文审计及作者仓库核对 | 固定来源、36 项 SIMAP 需求；确认原文/作者源同类语法错误，不是文本提取造成 |
| `30f2301` | IETF-1.1/1.2、IETF-2.1 验证包 | 41 个完整模块/子模块；独立 SIMAP 补丁；24 项模型/实例预期匹配；VSR 实例重建 7 文件一致 |
| `90e3997` | IETF-1.3/1.4 配置挂载与关联续查 | libyang 5.8.6 的 8 项配置挂载验证；41 文件中仅 LNE/NI 的 4 个 mount-point；3 个历史候选审计 |
| `c1e77b1` | IETF-1.5 状态挂载、部分观测 | 16 个生成文件；21 项预期匹配；完整合成状态树通过，两台 VSR 部分接口树原样解析但严格完整性不通过 |
| `ec81310` | IETF-1.6 原生路由语义 | 39 项 IPv4/IPv6、配置/状态、下一跳用例；37 项 Yangson 结果，2 项异常改由 libyang 正负对照复核，独立检查共 4 项 |

“预期匹配”包含故意构造的负例和 Schema 接受但证据仍不足的用例，不能说所有实例都有效。所有近期提交均为模型研究、说明、脚本和离线实例，没有部署、数据库迁移或设备配置变更；没有推送这些提交。

### 已采 VSR 实例

`models/network-twin/instances/manifest.json` 记录固定捕获批次及原始/清理后样本 SHA-256。由 `build_ietf_lab_instances.py` 离线生成：

- 2 个拓扑节点、4 个 TP、4 条有向 LLDP 邻接。邻接不证明物理线缆直连，不能据此捏造 Hub/不可管理交换机实体。
- 2 个 `ne-virtual` 清单项，不能误填为物理 NE；序列号的组件归属尚未闭合。
- 两台设备分别 18/19 条路由。RIB 名是投影标识，不是采集到的原生 YANG 名。未填出接口引用，不能宣称完整路径已建立。
- 每台 6 条部分接口记录：类型、状态、IP/掩码。缺 `statistics/discontinuity-time`，文件名保留 `.partial.json`。

不能用采集时间、估算开机时间替代计数器中断时间；不虚构默认 VRF、OSPF 版本/进程/area。`O_INTRA` 仅映射已有 OSPF 基类，cost 不填到 route-preference；0.0.0.0/32 不改为默认路由，特殊地址不自动推导丢弃或本机接收动作。

## 4. 工作区不是干净的：这些改动必须保留

交接时未暂存的 12 个修改及 2 个未跟踪文件均为此前暂停的 P2-3 工作，不属于近期模型提交：

```text
 M src/apps/topology/app.yaml
 M src/apps/topology/backend/collectors/collect.py
 M src/apps/topology/backend/collectors/parse.py
 M src/apps/topology/backend/collectors/profiles.py
 M src/apps/topology/backend/collectors/profiles/h3c-comware7.yaml
 M src/apps/topology/backend/collectors/tiers/README.md
 M src/apps/topology/requirements.lock
 M tests/unit/test_topology_facts.py
 M tests/unit/test_topology_generic_parsers.py
 M tests/unit/test_topology_profiles.py
 M web/react_frontend/src/apps/generated.meta.json
 M web/react_frontend/src/apps/generated.ts
?? src/apps/topology/backend/collectors/tiers/template.py
?? tests/unit/test_topology_templates.py
```

不要 `git reset --hard`、`git clean` 或 `git add .`。新提交仅精确暂存本项文件。如果 Claude 继续使用同一个本机目录，以上代码和研究缓存都还在；仅克隆 Git 或切到另一台机器不会自动带上未提交内容、`.runtime/` 或 Docker 镜像，需要单独保留，不能把 Git 提交当作全部本地状态的备份。

## 5. 运行环境和复现入口

交接时已实际核实下列本机路径存在，Docker 镜像可读取：

| 用途 | 路径/版本 |
|---|---|
| 应用 Python、Ruff | `venv/Scripts/python.exe` |
| 独立模型研究 Python | `.runtime/yang-model-tools/Scripts/python.exe`，pyang 2.7.1、yangson 1.6.3 |
| 已构建模型包 | `.runtime/ietf-study/model-package/`，含 modules、manifest、topology/device-library |
| 默认锁定源缓存 | `.runtime/ietf-study/reproducible-audit/sources/` |
| 历史关联候选缓存 | `.runtime/ietf-study/mount-history/` |
| libyang 源码归档 | `.runtime/ietf-study/mount-tool/libyang.tar.gz` |
| 验证镜像 | `netops-ietf-mount-verify:5.8.6` |
| 镜像 ID | `sha256:5db3d7d18f7ae37a5422516fd38a2bf7df8ace6ffc352867dca496d2b2c91afe` |

研究环境与应用环境分开，不向应用 requirements 安装模型工具。Docker 构建与来源锁在 `models/network-twin/mount/`，重建方法见该目录 README。运行验证容器禁网、只读、无特权，仅挂入模型和测试输入；保留镜像以便继续复现，不是常驻微服务。

下列命令均在项目根目录执行。**这不是要求接手后无条件重跑全部测试**：先读报告，只有缓存缺失、环境变动或修改影响该项时再运行对应验证。

```powershell
# 模型包：默认离线；缓存不存在时用 --fetch 按固定锁下载。
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_model_package.py
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py

# 配置挂载：12 文件一致、8 项预期匹配。
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_mount_cases.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_mount.py --output .runtime/ietf-study/mount-regression.json

# 状态/部分观测：16 文件一致、21 项预期匹配。
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_operational_cases.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_mount.py --cases models/network-twin/mount/operational --output .runtime/ietf-study/operational-regression.json

# 路由语义：39 项；脚本会调用 Docker 进行 4 项独立复核。
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_routing_semantics.py --output .runtime/ietf-study/routing-replay.json

# 关联声明与历史候选审计；缓存缺失时可加 --fetch。
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/audit_ietf_mount_associations.py
```

已提交的摘要、源锁和报告集中在 `docs/ietf-model-audit/`、`models/network-twin/reports/`。模型源码来自固定来源，SIMAP 原文和兼容工作副本分开。不要手改缓存源码或生成实例后绕过摘要检查。

最后一次代码验证：配置挂载 8/8、状态 21/21、路由 39/39 预期匹配，路由报告离线重放字节一致；研究脚本 Ruff/格式检查、范围内 diff 检查和暂存区脚本/报告摘要检查通过。此前同轮已核对模型包 24 项及 VSR 7 文件重建。交接文档整理本身未重复运行这些模型测试，只核对 Git 状态、资料路径和已保存报告。

## 6. 已踩过的工具和标准边界

1. **SIMAP 原文不能编译不是未解决的提取问题。** 作者仓库也确认同类错误。已有精确兼容补丁，沿用锁定补丁；原文审计整体退出 1 是保存的事实，不必改成成功。
2. **Yangson 不自动实现 Schema Mount。** 直接塞进 vrf-root 的子树会被拒绝；实际挂载已用 libyang 验证，不删掉路由子树假装通过。
3. **libyang 3.12.2 会崩溃。** 报告 `mount-3.12.2-validation.json` 的 SIGSEGV 不算正确负例；使用锁定 5.8.6，不把崩溃修成标准语义。
4. **5.8.6 严格模式与显式 `-t data` 不等价。** 严格验证不传 `-t`，使用 `--merge` 等兄弟模块全部解析完。显式 `-t data --merge` 会对部分 operational 语义缺失告警，退出 0 不代表完整性。`-t get` 是 parse-only，不解析完整引用；不能用 `-n` 跳过未知字段。
5. **RFC 8342 §5.3 不是任意缺字段的豁免。** 特定 operational 情况可违反所列语义约束，但层级、标识符和类型不可违反。CLI 未采到不等于设备未使用，也不证明设备应声明 deviation。部分观测可保留，缺项诊断必须留存，不能编造值。
6. **挂载回调文件与数据实例不同。** 5.8.6 的 `-x` 实际读取 XML；父/子库兼容 RFC 7895 与 8525。shared-schema 的 parent-reference 按当前 NI 过滤，只提供必要接口字段；inline 的库在 CLI 回调输入根部是工具约定。不要直接当生产服务协议设计。
7. **字段名必须核对代码。** RFC 8529 用 `bind-ni-name`；路由配置多下一跳有 index/next-hop-address，状态列表无 index、用 address；active 是 `[null]`。双下一跳不自带负载权重，也不证明数据平面 ECMP。
8. **Yangson 1.6.3 空 mandatory choice 有内部 TypeError。** 路由报告 accepted=null 保留异常；libyang 的 2 个负例与 2 个正对照才是此处语义证据，不可把异常算作模型拒绝。
9. **LF 与文件摘要相关。** `.gitattributes` 明确将挂载生成 XML 固定为 LF。新增生成目录时注意对应规则；提交前可以核对暂存区实际 blob 与 manifest，不能只检查工作树。

## 7. 下一步具体怎么接

### 首要：虚拟 node → NE → 设备树关联

先读 `models/network-twin/associations.md` 和 `reports/mount-associations.json`，不要重复已经完成的几轮宽泛搜索。

已知事实：

- inventory-software -04 有 `ne-virtual`；inventory-topology -11 当前物理映射具有物理语义，即使 Schema 接受虚拟 NE 也不能这样用于 VSR。
- 已锁定的 41 文件里只有 RFC 8530 LNE root、RFC 8529 vrf-root/vsi-root/vv-root 共 4 个真实挂载声明。不能利用 LNE 给两台独立设备凭空制造容器。
- inventory-topology 历史 -00 的 system-mount-point、-01 的 policies-target-point 是空锚点，未声明 RFC 8528 mount-point。不能用名称推断具备挂载能力，也不能为了绕过当前问题降级版本。
- Peer-Mount -01 与本地 Schema Mount 机制不同，原文还存在独立语法错误。当前补丁授权不覆盖它。
- 当前 external-relationship 的引用作用域与 request-builder 定义缺口仍在。工程文件名、相同 ID、显示用 interface-name 都不构成跨树已解析引用。

建议接手后的第一个可验收小项：为**明确的新候选**建立“固定来源/版本 → 原生节点路径及文字语义 → 对虚拟 NE 的适用性 → 引用或挂载作用域 → 正例/错对象/目标缺失反例”的记录。若已有文档只给讨论示例、空容器或 TODO，应明确排除而非补代码。可以从历史草案附录的泛型 topology-inventory 引用讨论追踪后继定义，但该线索尚未完成可采用性核验，不是已经授权采用的新模块。

本项验收必须回答：关联是什么标准节点定义、引用到哪里、如何区分设备与管理域、目标缺失是否允许、虚拟语义是否成立，以及 Schema/工具能否实际验证。没有闭合就保留缺口，不用项目 augment 解决。

### 可独立推进：尚未审查的协议/层次模型

关联没有立即可用定义时，继续路线图已经列出的 TE、路由协议、桥/LLDP 等模型范围审查，按目标场景建立“原生模块/依赖/feature/数据作用域/已有标准关联/未定义要求”的表，再逐项构造实例。不要一直为同一关联重复搜索，也不要突然回到 CLI 模板开发。

RFC 8349 的 39 项只覆盖基础路由结构，不代表 OSPF/BGP/TE 完整核验，更不代表所有 IETF 相关内容已经理解。新增模块须固定来源、保留完整模块/子模块/import，不能从当前 H3C CLI 样本反推所需最小字段。

### 之后才重排采集与应用接入

先明确模型字段语义及缺证据清单，再决定采什么、如何绑定设备上下文及保留部分观测；恢复 P2-3/P2-10 前更新方案和台账。不能为了接口缺 statistics/discontinuity-time 修改 mandatory，也不应因此丢弃已有合法观测。

模型研究尚未完成全网关联、完整设备状态、协议服务/NACM、ARP/MAC/VLAN/Hub 映射、转发路径计算和故障根因闭环。不要把近期离线验证包作为已发布网络数字孪生 App。

## 8. 接手后的工作节奏

先核对 `git status` 与本文件，保留 P2-3 变更。阅读规则和缺口后选择一个明确的小项，先核对标准再实现验证；对逻辑错误更新方案。完成时更新第 14 节并运行相关检查，精确暂存本项文件再提交。用户不要求每张卡停下来等待确认；也没有授权通过自定义模型替代未闭合的标准定义。

本次交接后由 Claude 在同一工作区继续，Codex 不再启动新的模型或采集任务。
