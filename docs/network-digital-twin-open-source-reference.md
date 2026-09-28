# 开源网络发现与状态解析参考

核对日期：2026-09-28。用于细化 `network-digital-twin-roadmap.md` 的 P2-3、P2-10 和后续分析阶段；不是引入整个产品的部署方案。以下区分项目公开做法与本项目的取舍。

后续负责人已要求先研读 IETF，再按完整原生模型实施且禁止项目自定义。本文保留为此前开源调研记录；涉及解析、事实结构、推断/元数据的建议暂停执行，不能替代标准定义。P2-3 尚未完成，最近有 5 个 VLAN 回归失败；下文代码范围描述不代表验收完成。

## 值得借鉴的实现

| 项目 | 官方实现/文档说明 | 本项目借鉴 |
|---|---|---|
| SuzieQ | 用 service YAML 配置每种设备的命令、字段映射及单位转换；原生 JSON 走 normalize，文本走 TextFSM，结果进入一致的表 Schema。缺少匹配配置则跳过该能力。[服务定义](https://suzieq.readthedocs.io/en/latest/service/service-format/) | 按能力组织命令和事实，未知能力明确保留缺口；统一字段后再做跨设备分析。不能因为一条命令成功就认为整台设备已适配。 |
| SuzieQ 的路由/路径分析 | 提供 route、ARP/ND、MAC、LLDP、interface 和 path 等操作；route Schema 描述动作、前缀等语义。[查询与表结构](https://suzieq.readthedocs.io/en/latest/tutorials/how_to_think_about_verbs/) | 路由解析必须保留 VRF、前缀、多下一跳与逻辑接口语义；后续路径计算关联多张事实表。路由表不是拓扑图本身。 |
| Nautobot Device Discovery / Onboarding | SSH 支持拆为平台探测、YAML command mapper、对应解析器；可用 TextFSM/TTP/原生 JSON 等，引入的 Git 模板可优先于内置模板。[扩展平台](https://docs.nautobot.com/projects/device-discovery/en/latest/dev/extending_platform_support/)；未指定平台时尝试 SSHDetect，无法识别则人工指定。[Onboarding](https://docs.nautobot.com/projects/device-onboarding/en/latest/user/app_use_cases/) | 保留“未知邻居→登录识别→选择命令→解析”的顺序。已审核的精确型号覆盖优先，失败再回退；不把 LLDP 中对端型号提示当作已确认的平台。 |
| NAPALM | 按平台列出统一 getter 的支持情况，而非承诺每个平台具备每种能力。[支持矩阵](https://napalm.readthedocs.io/en/latest/support/) | 分开记录命令可采集、解析可用、样本通过、实机验证四种状态。当前不增加第二套设备访问框架。 |
| NTC Templates | 新模板需有原始输出和期望结构测试；建议匹配表头、进入对应状态，并用 Error 捕获未匹配行。[模板开发](https://ntc-templates.readthedocs.io/en/latest/dev/dev_parser/) | 复用模板并固定版本/摘要，增加字段、完整性、错误样本和回归校验。返回非空列表不是可信判据。 |

这些资料支持确定性采集、解析、归一化的分层设计；不能据此声称这些项目采用本路线图的 LLM 兜底方案。文档与发行版可能不同，例如 SuzieQ 服务说明页仍称版本条件为未来能力，而其[发行记录](https://suzieq.readthedocs.io/en/latest/release-notes/)已有按版本选择命令的说明；采用实现前须核对具体代码版本。

## 对本项目的落实

推荐顺序仍为：平台确认 → 已验证的结构化输出/模板 → 通用解析 → 受校验的 LLM 候选。“通用解析为覆盖基础”不应解释为绕过已有可靠模板。

P2-3 暂停前的代码范围（未完成验收）：

- 新拓扑模块只读加载已审阅模板；锁定 `ntc-templates==9.1.0`、`textfsm==2.1.0` 和单文件 SHA256。可选本机模板缺失或摘要变化时回退，不依赖开发者未跟踪文件才能运行。
- H3C 的路由与 VLAN 使用 NTC 模板后做规范化和原文校验；ARP/MAC 同通用解析交叉核对。模板引用、摘要、来源行保留在事实元数据。
- 路由保留 `0.0.0.0/32`、`InLoopBack0`、`NULL0` 和 ECMP。记录数与设备宣告的 Routes 数比对在合并多下一跳之前进行；Destinations 另行检查。
- 实测发现 NTC Comware VLAN 模板的名称字段会截短带空格的名称。新适配器按原文 Name/Port 列边界取完整名称，并验证 VLAN ID；没有修改共享模板或原巡检。
- 表头、数量、未知行、行尾多余字段、分页与截断都参与完整性判断；模板失败不能变成合法空表。合法空表则可以替换旧完整事实集。

P2-10 / P3 后续落实：

- LLM 生成的模板必须以样本+期望结果回放，保留候选身份，审批前不进入受信模板目录；审批也不等同于实机兼容性验证。
- **覆盖率达到 95% 只能表示候选有参考价值，不足以成为 complete。** 有遗漏必须 partial；否则少量未识别路由就可能使后续路径计算漏掉关键下一跳。只有全表候选行全部解释、字段溯源和已知总数一致，且传输完整，才可标 complete。
- 展示层同时展示解析置信度、覆盖缺口与平台验证范围；“本次值有原文依据”与“该厂商所有版本支持”是两件事。
- 观测数据与人工维护资产分开，LLDP 新邻居先成为候选；身份归并和资产关联继续按既定人工确认/证据规则实施。

以上借鉴不引入 SNMP、不改原巡检、不改变现有凭据归属与管理域授权。认证或连接失败仍按既有流程等待人工处理。

## 当前适配范围（P2-3）

| 平台 | 能力 | 解析与验证范围 |
|---|---|---|
| H3C Comware 7 / VSR1000 7.1.064 R1362P12 | version、本机/邻居 LLDP、接口、IP | 通用层；两台实机 raw/clean 样本回放 |
| 同上 | ARP、MAC | 固定 NTC + 通用交叉核对；真实样本；现场 MAC 已老化为空时保留合法空状态 |
| 同上 | routes、VLAN | 固定 NTC + 字段/数量/覆盖检查；两台实机分别 18/19 条路由、各 2 个 VLAN；非默认 VRF 尚无样本，当前路由命令只采默认表 |
| 同上 | LAG、VRF | 仅已实测的空状态；非空结构未适配 |
| 同上 | inventory、IRF | 本实验设备明确拒绝，unsupported；不是全 Comware 平台不支持 |
| Cisco IOS/IOS-XE、NX-OS | ARP、MAC | 已安装模板映射与合成样本；可选仓库精确型号 ARP 模板优先；NX-OS 简化 MAC 样本不匹配 NTC 时回退通用。无实机支持声明 |
| Cisco IOS/IOS-XE、NX-OS | 其他能力 | 既有命令档案和部分通用解析；路由等缺口需可信样本后补齐，不以 H3C 结果外推 |
| 未识别平台 | routes、VLAN | 不猜模板；保留原文和缺口，按后续任务处理 |
