# NETCONF 接口 App 交接

2026-10-05。本文交代 NETCONF 采集这项工作的决定、边界、已完成的部分、卡住的部分和接下来的分工。

## 1. 现状

| 部分 | 状态 |
|---|---|
| 平台 NETCONF 传输层 | 已完成，已合入 `feat/network-twin`（c3d684a）。设备访问相关测试 173 个通过（含 NETCONF 新增的 13 个）。还没有调用方，下次 `start.ps1` 重启时随平台一起加载 |
| 实验室 | BR1、BR2 已开启 NETCONF over SSH（830 端口）并保存，配置文件已提交（c638dd6）。vpn-fabric 当前未运行 |
| 实测数据 | BR1 的 NETCONF 回包和 CLI 对照已存为测试夹具，见第 4 节 |
| 映射模块 | 已完成（2026-10-05，负责人要求 Claude 直接实现）：`src/apps/netconf_api/backend/mapping.py`，种子定义 `backend/seeds/h3c_comware7.yaml`，测试 `tests/unit/test_netconf_api_mapping.py`（29 个通过）。第 5.4 节的验收全部通过，过滤条件已在 BR1 上实测 |
| App 骨架、服务和接口 | 已完成，未部署（2026-10-05）：`src/apps/netconf_api`，测试 `tests/unit/test_netconf_api_service.py`。见第 6 节 |
| 拓扑接入 | 已完成（2026-10-05）：拓扑 0.10.0，依赖 netconf-api 0.2.0 的运行时契约；采集故障记录。见第 6 节 |
| 完整前端 | 未开始 |

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
| `hello-capabilities.txt` | 设备 hello 通告的 384 项能力：165 个 H3C 数据模块、6 个 action 模块（Ping、Tracert 等），IETF 只有 `ietf-netconf-monitoring` |
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
| 1/8 | Direct（NAT 的全局地址，指向 NULL0；BR1 配 NAT 后实测） | 3 |
| 1/9 | Direct（NAT 内部服务器映射的接口地址；BR1 配 NAT 后实测） | 1 |
| 2/1 | Static | 1 |
| 4/1 | O_INTRA | 12 |
| 4/2 | O_INTER | 2 |
| 6/1 | BGP（IBGP，BR2 实测） | 3 |
| 6/6 | BGP（EBGP，BR1 实测） | 3 |

6/1 是部署后在 BR2 上试运行时发现的：种子定义里原来没有这个代码，映射模块按设计报了"未映射"，没有去猜。之后经控制台对照 CLI（`display ip routing-table 8.8.8.0 24 verbose` 显示 `SubProtID: 0x1`、`LastAs` 为本 AS），确认是 IBGP，CLI 写作 `BGP`，于是补进种子定义（v2）。BR2 的回包和 CLI 存在 `tests/fixtures/netconf/h3c-comware7/br2/`。

其他代码（OSPF 外部路由等）还没有实测到。遇到时必须报"未映射"，不能猜。

## 5. 映射模块

### 5.1 经过

第一次写这个模块时，Claude 的回复被 Anthropic 的安全分类器拦下，于是本节先只写了需求和验收，准备交给人工实现。之后负责人要求 Claude 直接实现。第二次只用夹具离线做，不连设备、不读凭据，没有再被拦。

### 5.2 接口（`src/apps/netconf_api/backend/mapping.py`）

| 函数 | 作用 |
|---|---|
| `load(raw)` / `load_all(raws)` | 把存储形式的定义（dict，种子是 YAML）校验成 `Definition`。名称、路径、命名空间、翻译方式不合法时抛 `DefinitionError`；`load_all` 还检查 id 不重复、关联指向已加载的定义 |
| `subtree_filter(definition, extra=())` | 生成只选所需叶子的过滤条件。`extra` 是关联要从这张表读的叶子 |
| `fetcher(session)` | 返回 `fetch(definition, filter)`：`datastore: state` 走 `session.get`，`running` 走 `session.get_config(source="running")` |
| `collect(definitions, id, fetch, fields=)` | 先读被关联的表，再读本表并映射，返回 `Mapped` |
| `map_rows(definition, data, fields=, tables=)` | 对已读到的 `<data>` 做映射。`fields` 就是 `Selection.fields`（值名、类型、是否必填）；不传时不检查必填和类型 |
| `contract_gaps(definition, fields)` | 发布前检查：有没有检查项之外的值、有没有没映射的必填值 |

`Mapped` 包含四项：
- `rows`：每行是"值名 → 字符串"，定义里的每个值都有，没有值时为 `""`，和 TextFSM 一样；
- `refs`：每行对应设备的第几行；
- `problems`：`Problem(row, key, value, kind, raw)` 的列表，`kind` 是 `unmapped_code`、`lookup_miss`、`lookup_ambiguous`、`type_mismatch` 或 `required_missing`；
- `read`：设备返回的行数。

定义的写法见种子文件。每个值的取法：
- 直接取叶子；
- 用 `codes` 翻译（多个叶子用 `/` 拼成一个代码）；
- 用 `lookup: {definition, match, take}` 关联，之后还可以再接 `codes` 或 `format`。`match` 可以是多个叶子；省略 `match` 表示取那张表唯一的一行；
- 用 `format` 转换写法（目前有 `mac_dash4`、`mac_dot4`）。

2026-10-05 为 ACL、NAT、PBR 增加了以下写法，都是定义里的数据，不涉及厂商代码：
- `parts`：一个检查项分散在设备的多张表里时，每张表写一个 part，各表的行依次输出；所有 part 合进同一个过滤条件，一次 RPC 读回。
- `const`：常量值。
- `when`：只有条件满足时才读这个值。
- `where`：哪些行算这张表的实体。
- `expect`：对行的断言，不满足时记一条 `unexpected_value` 问题，这一行不输出。
- `default`：叶子缺失时写入的值。
- `none`：设备给出、但 CLI 不打印的值（例如命中次数 0）。
- `rest`：定义没读、也没在 `ignore` 里声明的叶子，写进一个值（OTHER），拓扑把这条规则标为"无法计算"，防止不认识的条件被悄悄忽略。带 `rest` 的表会按整行读取。
- `order`：组内按某个叶子排序（auto 顺序的 ACL 按 MatchIndex）。
- 叶子 `.`：取行元素自身的文本（每行只有一个叶子的表）。

### 5.3 原始需求

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

### 5.4 验收（用第 4.3 节的夹具）

- **ARP**：`arp.xml` 配合 `ifmgr-interfaces.xml`，得到 6 行，和 `cli-display-arp.txt` 逐行一致：IP、MAC、INTERFACE（`GE…`）、TYPE `D`、VRF `--`。没有问题。
- **路由**：`route.xml` 得到 47 行。`cli-display-ip-routing-table.txt` 里每个目的和下一跳的 PROTOCOL、DISTANCE、METRIC、NEXTHOP_IP 都一致，NEXTHOP_IF 是对应的接口全名。没有问题。
- **未映射代码**：把夹具里某行的 `ProtocolID/SubProtocolID` 改成表里没有的组合，这一行 PROTOCOL 为空，同时出现一条"未映射代码"问题；其他行不受影响。
- **过滤条件**：接口表定义生成的过滤条件，发给 BR1 后回包只有几 KB（实测 2 KB），不能是 213 KB 的整模块。
- **安全**：定义里出现非法的元素名或路径，在加载时就要拒绝；平台层本身会拒绝不合法的过滤条件。

**结果（2026-10-05）**
- **ARP**：通过，6 行逐行一致，没有问题。
- **路由**：通过。这里没有手工逐条对，而是用规则治理已发布的 Comware TextFSM 模板解析同一时刻的 CLI 输出，拿两边的行做多重集合比较，47 行全部相等。接口名用接口表从全名换成简写后再比。
- **未映射代码**：通过，但和原文的写法有一处不同。`topology.routes` 的 PROTOCOL 是必填值，所以：
  - 带检查项的 `fields` 时，这一行不输出，同时记两条问题：`unmapped_code` 和 `required_missing`，其余 46 行不变；
  - 不带 `fields` 时，这一行照常输出，PROTOCOL 为空。
  
  这是按第 6 条"必填缺失则不输出"处理的。
- **过滤条件**：通过，平台的 `check_filter` 原样接受。2026-10-05 经控制台 XML 视图发给 BR1，回包大小如下：
  - 接口表（只选 `IfIndex`、`AbbreviatedName`）：955 字节，整模块是 213 KB；
  - ARP：1.1 KB，整模块是 4.3 KB；
  - 路由：16 KB，整模块是 28 KB。
  
  拿这三份实测回包映射，ARP 得到 6 行、路由得到 47 行，都没有问题。
- **安全**：通过。非法元素名、带前缀的名称、注入命名空间、未知字段、自关联、多种翻译方式混用、多个叶子却不给代码表，这些情况在加载时都会被拒绝。

## 6. 映射模块之后由 Claude 继续的部分

状态：第 1、2、4、5 项已完成，第 3 项完成前两批。

1. **App 骨架**（已完成）：`app.yaml`（依赖 device-access 和 rule-governance）、数据表和迁移（定义及版本、设备能力、试运行记录）、`bootstrap` 导入种子定义、最小的前端占位页。
2. **服务和接口**（已完成）：定义的草稿、发布、版本；读取设备 hello、发现能力；试运行（经平台打开 NETCONF 会话 → 调用映射模块 → 保存行和问题）；发布时检查是否覆盖了检查项的必填值。
3. **种子定义**：
   - 第一批（已完成）：接口、ARP、路由，依据是第 4.3 节；
   - 第二批（2026-10-05 已完成）：ACL 规则、ACL 绑定、NAT 规则、NAT 绑定、PBR 绑定，见下文；
   - 待做：OSPF 邻居、接口 IP、LLDP、MAC/VLAN。做法同样是先在实验室配置样例，再逐个采样对照。
4. **拓扑接入**（已完成，要点见下文）：NETCONF 优先，失败回退 CLI；另加一个"两边都采、在事实层比对"的一致性模式。
5. **前端**（2026-10-06 已完成，0.5.0）：四步工作流（接口定义、编辑与发布、设备能力、试运行），沿用防火墙策略 App 的外壳；为选设备加了 `GET /devices`。说明和现有接口的限制见 `src/apps/netconf_api/README.md`。

**第 1、2 项的实现要点**
- **数据表**：
  - `app_netconf_definitions`：草稿和当前使用的版本，用 `revision` 防止并发覆盖；
  - `app_netconf_definition_versions`：已发布的版本，发布后不再修改；
  - `app_netconf_capabilities`：每台设备最近一次 hello；
  - `app_netconf_trials`：试运行的值行、问题、过滤条件，以及完整的行数和问题数。值行最多保存 5000 行。
- **接口**（`/apps/netconf-api`）：
  - `GET /definitions`、`GET /definitions/{id}`
  - `PUT /definitions/{id}/draft`、`POST /definitions/{id}/discard`、`POST /definitions/{id}/publish`
  - `POST`/`GET /devices/{device_id}/capabilities`
  - `POST /trials`、`GET /trials`、`GET /trials/{id}`
  
  所有接口都需要 `netconf-api` 的使用授权。
- **发布检查**：
  - 草稿保存时就用映射模块校验；
  - 发布时再检查两件事：关联是否指向已发布的定义；按规则治理已发布的检查项，每个平台的值是否在检查项之内、必填值是否都映射了。
  
  现在的限制：检查项的值从规则治理的 `Selection.fields` 读取，所以只有该平台已经有 CLI 实现的检查项才能发布。以后要支持只有 NETCONF 的平台，需要规则治理的运行时契约直接提供检查项的值定义。
- **设备和凭据**：
  - 设备取自平台设备清单，必须是启用状态，并且有管理地址、用户名和凭据引用；
  - App 只传凭据引用，密码由平台解析；
  - 830 端口的主机密钥第一次见到时自动记录（和拓扑发现一致），密钥变化时停止。
- **能力**：定义可以写 `module`（设备 hello 里通告的模块名，H3C 种子是 `Ifmgr`、`ARP`、`Route`）。设备能力页据此列出这台设备能用哪些已发布的定义。
- **种子**：`bootstrap` 以 `system` 身份发布种子版本。种子更新时只替换原来的种子版本，不覆盖用户自己发布的版本。

**第 4 项（拓扑接入）的实现要点**
- **契约**：netconf-api 新增运行时契约 `src/apps/netconf_api/runtime.py`（`DefinitionReader.select/read`、`announced`），只读，拓扑只通过它使用定义。拓扑在 `app.yaml` 里声明依赖 netconf-api。
- **会话**：拓扑自己打开 NETCONF 会话，用该节点的登录、830 端口、`device.netconf` 授权；一个节点只开一个会话。值行用和 CLI 相同的投影代码（`rules.project_netconf`）转成事实。读到的回包和 CLI 输出存在一起，作为证据。
- **模式**（发现任务的 `netconf` 参数）：
  - `prefer`（默认）：NETCONF 能取的表先走 NETCONF；
  - `off`：不用 NETCONF；
  - `compare`：两边都采，在事实层比对，仍以 CLI 的结果为准。只有一方给出的字段（例如 CLI 的 ARP 老化时间）会单独列出，不参与比对。
- **回退到 CLI 的情况**：一张表只有完整、干净地读下来才用 NETCONF 的结果，否则回退到 CLI，原因记在 `fact_metadata.<能力>.netconf.reason` 里：
  - `session:<失败码>`：登录、授权或超时等会话失败；
  - `module_not_announced`：设备 hello 里没有通告定义的模块；
  - `problems`：映射有问题，比如未映射代码、关联未命中；
  - `empty_unverified`：空表，空回包分不清是"没有表项"还是过滤条件没匹配上；
  - `per_vrf_unverified`：这张表在 CLI 里是按 VRF 分别采的，而设备上有 VRF，NETCONF 定义还没按实例验证过；
  - `rpc_error`、`reply_unreadable`、`definition_invalid`、`no_contract`。
- **故障记录**：新表 `app_topology_collection_issues`，接口是 `GET /apps/topology/issues?network_id=&include_cleared=`。
  - 每次观测之后，按"设备 + 数据项 + 方式（ssh/netconf）+ 问题类型"去重记录，保存最近一次的详情、首次和最近出现的时间、次数；
  - 每次出现都写一条 warning 日志；
  - 之后某次采集正常读到这项数据，就自动清除；
  - 记录的内容：节点整体失败；CLI 表读不全（`failed`、`partial`，详情里有被模板拒绝的行等）；NETCONF 回退的原因；比对不一致（`comparison_mismatch`）；
  - 不记的内容：设备本身不支持的命令（`unsupported`，例如路由器上没有交换功能），以及 `empty_unverified`。
- **实测**（BR1 夹具，`tests/unit/test_topology_netconf.py`）：
  - `prefer` 模式下 ARP 和路由都走 NETCONF，CLI 不再执行这两条命令；
  - `compare` 模式下两边的事实完全一致，ARP 只差 CLI 独有的 `age_s`；
  - 未映射代码、登录失败、会话中途断开、没有授权、模块没有通告、设备上有 VRF 这几种情况，都按预期回退到 CLI，并留下了故障记录。

**第二批种子（ACL、NAT、PBR）**
- **样例配置**：BR1 原来没有这些配置，于是设计了一套样例下发并保存，内容见 `scripts/topology/campus_wan/configs/BR1.txt` 末尾。
  - 规则只匹配文档保留地址（源 `192.0.2.0/24`，目的和地址池 `203.0.113.0/24`），所以实验网现有流量、BGP/OSPF 会话和拓扑路径都不受影响。下发后核对过：3 个 BGP 邻居 Established，3 个 OSPF 邻居 Full，PC1 到 8.8.8.8 可达。
  - 覆盖的内容：
    - 基本、高级、命名 ACL，含 auto 匹配顺序；
    - 端口 eq/neq/lt/gt/range、ICMP 类型、established、分片、DSCP、对象组、协议号 51；
    - 生效和失效的时间段；
    - GE5/0 入、出方向的包过滤；
    - 地址组加 no-pat、Easy IP、两条内部服务器、一条静态一对一映射；
    - PBR 的 permit 和 deny 节点，接口和本机两种应用。
  - VPN 实例这个条件是在 BR2 上临时配置测的（叶子名是 `VRF`），测完已撤销，没有保存。
- **夹具**：
  - `tests/fixtures/netconf/h3c-comware7/br1-policy/`：ACL、NAT、PBR 三个模块的回包，和同一时刻的 4 条 CLI 输出；
  - `br2-acl-vrf/`：VPN 实例条件的那次测量。
- **对照结论**（`tests/unit/test_netconf_api_policy.py`，用拓扑的投影代码比对事实）：
  - ACL 绑定、NAT 规则、NAT 绑定、PBR 绑定，事实完全相同；
  - ACL 规则 23 条里只有 rule 50（DSCP）一条不同。两边都把这条规则标为"无法计算"，只是写法不同：CLI 是 `dscp ef`，NETCONF 是 `DSCP=46`。
- **实测到的代码和对应关系**：
  - Action：1 deny，2 permit；
  - ProtocolType：1 icmp，6 tcp，17 udp，47 gre，51，256 ip；
  - 端口操作符：1 lt，2 eq，3 gt，4 neq，5 range（第二个端口值 65536 表示没有）；
  - 组表的 MatchOrder：1 配置顺序，2 auto；auto 时规则按 RuleID 返回，`MatchIndex` 才是匹配顺序；
  - 规则 Status：时间段生效 1，失效 2；
  - 包过滤缺省动作 1 是 Permit；PfilterApply 里 `AppAclType` 为 5 的行是每个方向的缺省动作项；
  - 包过滤运行状态 AclGroupStatus：1 表示生效；
  - 静态映射：只认前缀长度 32（一对一），其他前缀记 `unexpected_value`。

  没有列出的代码一律报"未映射"，表回退到 CLI。
- **部署后第一次真实发现**发现了一个缺口：NAT 配置在 BR1 上生成了代码为 1/8、1/9 的直连路由，路由表的种子里没有这两个代码。结果是路由表回退到 CLI，故障记录里写明了是哪几行、原始代码是什么。之后对照 CLI 确认都显示为 `Direct`，补进路由种子（v3）。夹具是 `br1-policy/route.xml` 和同一时刻的 CLI 路由表。

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
