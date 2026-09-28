# IETF 模型研究复现

这里的锁定清单和报告是研究证据，不是应用提供的 YANG Library，也不是项目新增的数据模型。标准及草案源码下载到忽略目录，不修改源码，不替换生产模型。

- `sources.lock.json`：38 个来源的固定 URL、SHA-256、提取后模块 SHA-256 和四组编译清单。共 38 个不同版本的模块文件，包括历史与新版公共类型。
- `report.json`：本次检查快照，包含工具版本、脚本/来源清单摘要、诊断、需求覆盖和逐实例结果。`all_checks_passed=false` 是真实结果，不能解释为全部通过。
- 语义结论、关联边界和待解决项见 [原生模型核验清单](../network-digital-twin-model-audit.md)。

## 复现命令

从仓库根目录执行，研究环境需要 `pyang==2.7.1`、`yangson==1.6.3`。已使用独立 `.runtime/yang-model-tools` 环境，未修改应用依赖。

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/audit_ietf_models.py
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/audit_ietf_models.py --offline
```

默认结果位于 `.runtime/ietf-study/reproducible-audit/report.json`。指定 `--output .runtime/ietf-study/audit-from-download` 可用独立缓存复现；本次已实际从空缓存运行该命令，再使用 `--offline` 核验。

脚本在源文件或提取结果与锁定摘要不同时终止；不自动更新摘要。组合目录若存在清单之外的 YANG 文件，也会拒绝运行，避免缓存偷偷满足依赖。更换模型版本时更新来源清单、语义核验和报告，不只改文件名。

## 结果解释

| 检查 | 结果 |
|---|---|
| 30 个历史 RFC/标准依赖文件 | 编译通过，保留此前公共类型 revision。 |
| 原文 SIMAP -03 + 基础/类型依赖 | 编译失败：原文 description 语法错误。程序退出码 1。 |
| inventory、software、topology、external 及基础/新版类型（10 文件） | 编译通过，无诊断。 |
| RFC 基线改用 RFC 9911 公共类型，再加上述扩展（35 文件） | 编译通过，无诊断。 |
| concept -13 的 36 项需求 | 对应表 36 行，无漏项、额外项或重复项。 |
| 16 个原生定义正反例 + 2 个原始附录实例 | 18 项结果符合预期；其中 4 项故意展示 Schema 接受但原文语义不成立/有歧义的实例。 |

实例验证使用 `ContentType.all`，包含 config false 的 inventory 数据；identity 提供模块声明为实现，公共类型为导入。没有把只读数据误当配置拒绝，也没有修改原生模型放宽检查。

四个语义缺口分别为：支撑链路自环、物理映射引用虚拟 NE、port-ref 引用 chassis、跨网络重复 node-id 导致外部关联歧义。这些用例记录“Schema 接受”，**并不批准对应业务数据**。后续语义校验只能执行原文已有约束，不能新增模型属性或改写标准。

两个原始实例取自 inventory-topology -11 附录 A/B，仅去除分页和恢复 RFC 8792 单反斜杠折行。附录 A 的 port-ref 写成路径字符串，但类型实际是组件 ID 的 leafref；由于允许未解析引用，Schema 接受它不等于已正确关联 inventory。附录 B 验证端口 breakout 只读结构。

SIMAP 实例因原文模块无法编译而未运行；schema mount 的跨树完整性、全套 TE/协议扩展以及数字孪生应用验收均未完成。脚本不执行外部访问模板，不连接设备。
