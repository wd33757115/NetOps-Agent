# 分层解析

默认入口 `collectors.parse.parse(...)` 先看设备系统家族：

- **IOS/IOS-XE 与 Comware 7**：所有采集能力都经规则治理的系统基础检查项解析（`collectors/rules.py`）。命令、按 VRF 的命令形式和 TextFSM 模板都由已发布的
  采集实现给出，按型号优先、系统家族兜底选择。拓扑只把同名 Value 投影成事实；带否决语义的整表
  （Trunk、二层口、OSPF）由 `collectors/rules_tables.py` 组装。
- **其余家族**（NX-OS 等）：仍走本目录的通用解析层，直到规则治理有经过实机验证的实现。

## 通用解析层

`collectors.parse.parse_generic(identity, capability, raw, complete=True)` 是无 I/O 通用解析器。
不要求型号档案；`identity.os_family` 用于已知接口别名规范化。路由没有通用正则兜底。输出分类
（不支持的命令、错误、分页、截断）经规则治理的公开契约 `classify_output` 完成。

结果包括 typed `records`、身份类 `summary`、`status`、`tier=generic`、
`confidence=medium`、候选/拒绝数量、告警及每行原值。`fact_summary()` 将解析元数据放入
现有 FactSet 的 JSON summary，typed 记录不增加列。来源行号从 1 开始，相对于
`parser_text(raw).text`；原始 raw 仍须作为事实证据保存。只有明确提供 `command`、`prompt`
时才清除首尾完全相同的终端回显，使用空行占位保持行号。

调用方必须传入实际传输完整性 `complete`。分页、截断、数量不符、残缺邻居/表项均不能
产生 complete 集；合法零条与无法解析有不同状态。部分结果可保存历史，但不替换当前完整集。
未知 CLI 表行也产生缺口，避免仅解析成功的子集被当成全表。未经交叉验证的通用事实仍为 medium。

ARP `Aging` 未标单位时只留在 raw，不猜秒；接口 IP 无掩码不能写 IP 事实；
bridge 表的 trunk/hybrid PVID 不冒充 access VLAN；oper down 不推断 admin down。
LLDP 完整键同义词在 `lldp_aliases.yaml`，管理地址的 type/interface ID/OID 不属于地址。

通用层的形状规则只用五种合成的 CLI 风格检查过；NX-OS 等其他型号或厂商未进行实机兼容性验收。

原先固定 NTC 模板的"模板层"已移除：它的结果必须与通用层完全一致，按型号选模板现由规则
治理负责。
