# NETCONF 接口 App 交接

2026-10-05。本文交代 NETCONF 采集这项工作的决定、边界、已完成的部分、卡住的部分和接下来的分工。

## 1. 现状

| 部分 | 状态 |
|---|---|
| 平台 NETCONF 传输层 | 已完成，已合入 `feat/network-twin`（c3d684a）。设备访问相关测试 173 个通过（含 NETCONF 新增的 13 个）。还没有调用方，下次 `start.ps1` 重启时随平台一起加载 |
| 实验室 | BR1、BR2 已开启 NETCONF over SSH（830 端口）并保存，配置文件已提交（c638dd6）。vpn-fabric 当前未运行 |
| 实测数据 | BR1 的 NETCONF 回包和 CLI 对照已存为测试夹具，见第 4 节 |
| **映射模块** | **未完成**。Claude 写这部分时被安全分类器拦截，本次对话里 Claude 不能再写，需要人工实现，见第 5 节 |
| App 其余部分 | 未开始。等映射模块的接口约定确定后，由 Claude 继续，见第 6 节 |

## 2. 负责人已定的决定

- **NETCONF 优先，不行再走 CLI**：设备支持 NETCONF 且有已发布的定义时走 NETCONF，否则走现有 CLI 采集。
- **先做 H3C**：实验网的 Cisco IOL 不支持 NETCONF/RESTCONF（实测：命令不存在），这些设备继续走 CLI。以后有 IOS-XE 虚拟机（如 C8000v）再加 Cisco。
- **新建独立 App** 维护 NETCONF API 定义，原因是 NETCONF 涉及大量 API。
- **先后端后前端**：后端测试完成后，再按防火墙策略 App 的外壳和视觉规范设计前端。
- **选 NETCONF 而不是 RESTful**：H3C 的 RESTful 和 NETCONF 是同一套数据模型。NETCONF over SSH 复用 SSH 账号（BR1 的 admin 只开了 SSH 服务类型就能用）和平台的 SSH 审计。华为、Cisco 也都支持 NETCONF。
- **GNS3 设备的账号和密码**由 Claude 管理。但 Claude Code 自身的安全检查不受这个授权影响，见第 7 节。

## 3. 架构与边界

| 层 | 位置 | 职责 |
|---|---|---|
| NETCONF 传输 | 平台 `src/platform/device_access/netconf.py` | NETCONF over SSH，只读。凭据、主机密钥、准入、审计和 SSH 共用 |
| NETCONF 接口 App（待建） | `src/apps/netconf_api`，App id `netconf-api` | 维护 API 定义、设备能力、试运行、发布；把设备表映射成检查项值行 |
| 消费方 | 拓扑（之后可能是巡检） | 按设备选用 NETCONF 或 CLI。两条路产出同样的值行，路径计算和 IETF 导出不用改 |

**契约**：规则治理的检查项（`src/apps/rule_governance/backend/check_items/assets/system_check_items.yaml`，例如 `topology.arp`、`topology.routes`）规定了值的名称和含义，CLI 那边由 TextFSM 产出这些值。NETCONF 这边要产出**同名、同写法（按 CLI 原文）**的值行，拓扑现有的归一化代码就能原样处理。检查项归规则治理所有，NETCONF App 只提供另一种采集实现。

**凭据**：App 从平台设备清单（`NetworkDevice`）取用户名和凭据引用（园区 8 台是 `env:NETOPS_LAB_PASSWORD`），密码由平台在服务端解析，App 和开发者都不经手。园区 8 台已在 2026-10-05 加入巡检清单（`site=campus-wan`，`netops-lab` 组）。

## 4. 已完成

### 4.1 平台传输层

调用方式和 SSH 的人工终端一样：调用方先完成用户和设备的授权，再签发上下文。

```python
from src.platform.device_access.contracts import AccessContext, AccessGrant, ConnectionProfile, Target
from src.platform.device_access.service import DeviceAccessService

context = AccessContext(actor_id, "netconf-api", (AccessGrant(target_ref, "netconf", action="device.netconf"),))
with service.netconf(context, target_ref, "netconf") as (session, guard):
    session.capabilities                 # 设备 hello 通告的能力
    xml = session.get(subtree_filter)    # 返回 <data> 元素的 XML 文本
    xml = session.get_config(subtree_filter, source="running")
```

- **只读**：只发带过滤条件的 `<get>`/`<get-config>`。RPC 由平台根据过滤条件自己拼，调用方传什么都不会变成 `edit-config`、`lock`、action 或 `<CLI>`。不带过滤条件的整库读取一律拒绝。
- **分帧和解析**：支持 RFC 6242 的 base 1.0（结束标记）和 1.1（分块）。BR1 只支持 1.0。回包用 defusedxml 解析。
- **授权**：需要单独的 `device.netconf` 授权，`device.collect` 和 `device.terminal` 都不够。
- **审计**：连接、断开，以及每次 RPC 的过滤条件、耗时、回包字节数和 sha256。
- **出错处理**：设备返回 `<rpc-error>` 时会话继续可用；超时、分帧错乱、`message-id` 不对时会话作废。回包有字节上限，取 `ConnectionProfile.max_output_bytes`。
- **主机密钥**：按端口单独记录，830 和其他端口一样。App 构造 `DeviceAccessService` 时传入设备访问 App 的 `HostKeyStore`。首次见到的密钥是自动信任（和拓扑发现一样）还是等人确认，由 App 决定。

### 4.2 实验室

- BR1、BR2（H3C VSR1000，Comware 7.1.064 R1362P12）：`netconf ssh server enable`，`display netconf service` 显示 `NETCONF over SSH: Enabled (port 830)`。
- vpn-fabric 的 LEAF3、PE2 也是 VSR，实验室启动后执行同一条命令即可。
- **不经 SSH 读数据**：控制台进入 `xml` 视图就是 NETCONF，不需要登录。工具是 `.runtime/console-xml.py <节点> get <目录> <模块>...` 和 `.runtime/console-xml-filter.py <节点> '<过滤条件>' [文件]`（本机 `.runtime/`，不入库）。
  - 控制台如果卡在 XML 视图里，用 `.runtime/console-xml.py <节点> exit` 退出（先发 hello，再发 close-session）。

### 4.3 实测数据（`tests/fixtures/netconf/h3c-comware7/br1/`）

2026-10-05 在实验网正常状态下从 BR1 采集。

| 文件 | 内容 |
|---|---|
| `arp.xml`、`route.xml`、`ospf.xml`、`ipv4address.xml`、`lldp.xml`、`mac.xml`、`vlan.xml`、`nat.xml`、`acl.xml` | 各模块整模块 `<get>` 的回包（`rpc-reply`）。BR1 当前没有配置 ACL 和 NAT，这两个文件里只有能力和开关类的表 |
| `ifmgr-interfaces.xml` | 接口表按字段过滤后的回包（IfIndex、Name、AbbreviatedName、AdminStatus、OperStatus、Description）。整模块是 213 KB，过滤后 2 KB |
| `hello-capabilities.txt` | 设备 hello 通告的 384 项能力：164 个 H3C 数据模块，IETF 只有 `ietf-netconf-monitoring` |
| `cli-display-arp.txt`、`cli-display-ip-routing-table.txt` | 同一时刻的 CLI 输出，作为对照依据 |

**几个结论**
- 数据是结构化的。但接口用编号表示（`IfIndex`），VRF 也是编号（`VrfIndex`），类型和协议是数字代码。要转成 CLI 的写法，需要查另一张表做关联，再做代码翻译。
- 数据是 H3C 私有模型（命名空间 `http://www.h3c.com/netconf/data:1.0`，结构是 `top/模块/表/行`），不是 IETF。
- 按字段选择的过滤条件可用：`<Ifmgr><Interfaces><Interface><IfIndex/><AbbreviatedName/>…</Interface></Interfaces></Ifmgr>` 只返回这几个字段。

**ARP 和 CLI 的对应（`ARP/ArpTable/ArpEntry`）**

| 检查项值（`topology.arp`） | NETCONF | CLI 写法 | 说明 |
|---|---|---|---|
| IP_ADDRESS | `Ipv4Address` | `10.0.13.2` | |
| MAC_ADDRESS | `MacAddress` = `AA-BB-CC-00-02-10` | `aabb-cc00-0210` | 小写，每 4 位一组 |
| INTERFACE | `IfIndex` = 49 | `GE3/0` | 用接口表 `IfIndex` → `AbbreviatedName` |
| TYPE | `ArpType` = 0 | `D` | 只见过 0；静态等其他代码未实测 |
| VRF | `VrfIndex` = 0 | `--`（公网） | 非 0 的编号要关联 L3vpn 模块，BR1 上没有 VRF，未实测 |
| AGE、VLAN_ID | 表里没有 | `18`、`--` | 非必填，可不给 |

**路由和 CLI 的对应（`Route/Ipv4Routes/RouteEntry`）**

CLI 的 42 个目的、47 条路由全部对上，管理距离（`Preference`）也一致。NETCONF 是每个下一跳一行，共 47 行，比 CLI 的 42 个目的多出的 5 行是等价路由的第二个下一跳（CLI 把它们打在续行里），正好符合 `topology.routes` "每个下一跳一行"的语义。

| 检查项值（`topology.routes`） | NETCONF |
|---|---|
| NETWORK、PREFIX_LENGTH | `Ipv4/Ipv4Address`、`Ipv4/Ipv4PrefixLength` |
| PROTOCOL | `Protocol/ProtocolID` + `Protocol/SubProtocolID`，对照见下表 |
| DISTANCE、METRIC | `Preference`、`Metric` |
| NEXTHOP_IP、NEXTHOP_IF | `Nexthop`、`IfName`（直接是接口全名，例如 `GigabitEthernet2/0`；CLI 是 `GE2/0`，拓扑归一化时会统一） |
| VRF | `VRF`（公网为空） |

| ProtocolID/SubProtocolID | CLI 显示 | 实测条数 |
|---|---|---|
| 1/0、1/1、1/4 | Direct | 4、19、1 |
| 2/1 | Static | 1 |
| 4/1 | O_INTRA | 12 |
| 4/2 | O_INTER | 2 |
| 6/6 | BGP | 3 |

其他代码（OSPF 外部路由、IBGP 等）没有实测到。遇到时必须报"未映射"，不能猜。

## 5. 待完成：映射模块（需人工实现）

### 5.1 为什么需要人工

Claude 在本次对话中写这个模块时，回复被 Anthropic 的安全分类器拦下，并被告知不能再写这部分内容，换种写法也不行。模块本身只是数据映射，不连设备、不碰凭据，很可能是误判（负责人可以通过客户端反馈）。在此之前写了一半的文件已经删除，仓库里没有残留。

### 5.2 这个模块要做的事

输入是一条定义和设备返回的 `<data>` XML，输出是检查项值行和问题清单。

1. **定义作为数据维护**：每条定义要说明厂商和平台族、数据命名空间、模块/表/行、读 state 还是 running、对应哪个检查项、主键，以及每个值从哪个叶子取、怎么转换。定义要能被 App 存储、做版本管理和发布，具体格式由实现者设计。
2. **过滤条件从定义生成**：按定义里用到的字段路径，拼出"只选这些叶子"的子树过滤条件，交给 `session.get()` 或 `session.get_config()`。不允许手写整模块读取。
3. **XML 转值行**：
   - 每行输出一个"值名 → 字符串"的字典，和 TextFSM 的输出形式一致；
   - 写法按 CLI 原文，例如 MAC 写成 `aabb-cc00-0210`；
   - 空值视为没有。
4. **代码翻译**：支持多个叶子组合成一个代码（例如 `ProtocolID/SubProtocolID`）。表里没有的代码，记一条问题，值留空，不猜。
5. **关联**：一个值可以用另一条定义的结果翻译，例如 `IfIndex` → 接口表的 `AbbreviatedName`。执行时先读被依赖的表，查不到的记一条问题。
6. **必填**：检查项必填的值缺失时，这一行不输出，同时记一条问题。
7. **问题清单**：每条问题至少要说明是哪一行（用主键或行号）、哪个值、什么问题（例如未映射代码、关联未命中、必填缺失、类型不符），以及原始值。

### 5.3 验收（用第 4.3 节的夹具）

- **ARP**：`arp.xml` 配合 `ifmgr-interfaces.xml`，得到 6 行，和 `cli-display-arp.txt` 逐行一致：IP、MAC、INTERFACE（`GE…`）、TYPE `D`、VRF `--`。没有问题。
- **路由**：`route.xml` 得到 47 行。`cli-display-ip-routing-table.txt` 里每个目的和下一跳的 PROTOCOL、DISTANCE、METRIC、NEXTHOP_IP 都一致，NEXTHOP_IF 是对应的接口全名。没有问题。
- **未映射代码**：把夹具里某行的 `ProtocolID/SubProtocolID` 改成表里没有的组合，这一行 PROTOCOL 为空，同时出现一条"未映射代码"问题；其他行不受影响。
- **过滤条件**：接口表定义生成的过滤条件，发给 BR1 后回包只有几 KB（实测 2 KB），不能是 213 KB 的整模块。
- **安全**：定义里出现非法的元素名或路径，在加载时就要拒绝；平台层本身会拒绝不合法的过滤条件。

## 6. 映射模块之后由 Claude 继续的部分

接口约定（函数名、参数、返回值）确定后，Claude 在本次对话中可以继续做下面这些：

1. **App 骨架**：`app.yaml`（依赖 device-access 和 rule-governance）、数据表和迁移（定义及版本、设备能力、试运行记录）、`bootstrap` 导入种子定义、最小的前端占位页。
2. **服务和接口**：定义的草稿、发布、版本；读取设备 hello、发现能力；试运行（经平台打开 NETCONF 会话 → 调用映射模块 → 保存行和问题）；发布时检查是否覆盖了检查项的必填值。
3. **种子定义**：第一批是接口、ARP、路由，依据就是第 4.3 节。之后在实验室配置样例后，逐个采样对照，再加 OSPF 邻居、接口 IP、LLDP、MAC/VLAN、ACL、NAT。
4. **拓扑接入**：NETCONF 优先，失败回退 CLI；另加一个"两边都采、在事实层比对"的一致性模式。
5. **前端**：后端测完后设计，沿用防火墙策略 App 的外壳和视觉规范。

## 7. 注意事项

- **安全检查**：负责人授权 Claude 管理 GNS3 设备的密码，但 Claude Code 的安全检查不受这个授权影响。2026-10-05 有三次拦截：
  - 写凭据存储（Claude Code 权限分类器，可以在设置里加规则放行）；
  - 脚本读实验室密码登录 NETCONF（回复安全分类器）；
  - 本模块（回复安全分类器）。

  所以 Claude 优先走不碰明文密码的路径：控制台、控制台 XML 视图、平台自己的登录。
- **大表**：真实设备的路由表可能很大。先靠按字段过滤和字节上限；不够时再用 H3C 的分页读取。
- **830 端口主机密钥**：首次见到时自动信任还是等人确认，要在 App 里明确，建议和拓扑发现保持一致。
- **Cisco**：IOL 不支持。架构不依赖厂商，以后加 IOS-XE 定义即可（IOS-XE 17.15 有 IETF 的接口和路由模型，没有 IETF 的 ACL 和 NAT 模型）。

## 8. 相关状态

- **诊断预算改动已部署**（topology 0.9.8、assistant 0.8.10、device-access 0.9.11）：
  - 每轮只按总 token 限制（60 万）；
  - 路径上某台设备的 ACL/NAT/PBR 表没采到时记 `policy_not_computed`，这个方向不会是 `complete`；
  - 命令平台不对时，提示里写明设备平台和该用 `show` 还是 `display`。
- **C2 的 B 组复测**（`.runtime/fx-B-c2.out`）：上面三项都生效了，但仍没找到根因。原因是复测时 PC1 的 MAC 在发现之前已经老化，定位返回 `not_found`，把 AI 带偏了。这是测试脚本的时序问题，以后打流要紧挨着发现做。
- **ACL/NAT 实验报告**：`docs/topology-aclnat-diagnosis-experiment.md`。
- **实验室当前状态**：5 个故障都已撤销，平台在 A 组，快照是正常状态。
