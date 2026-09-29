# 分层解析

`collectors.parse.parse_generic(identity, capability, raw, complete=True)` 是无 I/O 通用解析器。
不要求型号档案；`identity.os_family` 用于已知接口别名规范化。路由没有通用正则兜底。
默认入口 `parse(...)` 先做分类及通用回放，再优先选取校验通过的受信模板结果；
该入口只读加载安装目录中的模板，`use_templates=False` 可单独运行通用层。

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

已验证：两台 H3C VSR1000 实际样本，七项能力 raw/clean 重放与事实库往返。
`tests/fixtures/topology/synthetic/generic-styles.json` 中五种 CLI 风格均为明确的合成样本。
其他型号或厂商未进行实机兼容性验收。旧线上 LLDP 解析器保留，现场多能力接线按后续卡实施。

## 固定模板层

`template.py` 固定 NTC 9.1.0 / TextFSM 2.1.0 和逐文件 SHA256；精确型号匹配时优先只读
使用已有仓库 ARP 模板。本机可选模板不存在、发生修改或回放失败时回退 NTC/通用层。
不修改模板库及原巡检；不执行模型上传的模板。

ARP/MAC 必须同通用事实一致；Comware 路由校验数量、表头、完整行和规范化字段，合并
ECMP 并保留前缀/下一跳行号。VLAN 名从原表列边界校验提取，修复上游模板对空格名称
的截短。未知行或残缺不能变成完整集。`tier=template, confidence=high` 表示本次解析
经校验，并非该厂商所有型号已实机支持；硬件验证另见 profile.verified。

来源元数据包括模板引用/摘要和解析输入中的行号；统一通过 `fact_summary()` 存入现有
事实集。支持矩阵与设计取舍见 `docs/network-digital-twin-open-source-reference.md`。
