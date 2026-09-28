# 网络孪生原生模型验证包

这是可离线构建的模型与实例验证包，尚未接入 App、数据库或设备。模块结构来自固定标准/草案；实例名称、文件组织、构建摘要和测试报告是验证工具资料，不是新增网络模型实体或字段。

## 构建与验证

使用独立研究环境（pyang 2.7.1、yangson 1.6.3）：

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_model_package.py
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py
```

构建默认离线读取 `.runtime/ietf-study/reproducible-audit/sources`，缓存缺失时加 `--fetch` 下载并校验固定来源。可用 `--cache` 和 `--output` 指定空目录复现。基础来源锁在 `docs/ietf-model-audit/sources.lock.json`，OSPF 及其依赖在本目录 `additional-sources.lock.json`。原文审计因为 SIMAP 原文错误仍退出 1，兼容包的结果单独记录。研究依赖固定为 pyang 2.7.1、yangson 1.6.3；不安装到应用环境。

输出 `.runtime/ietf-study/model-package/` 包含 41 个完整模块/子模块、独立保留的 SIMAP 原文、校验清单、两个验证用 YANG Library、展开树和实例结果。不裁剪模块源码。profile 只选择本次实例验证的模块和 feature，不是宣告所有设备或应用支持这些能力；未来模块仍需按来源、版本及约束纳入。固定的 41 文件清单及本轮结果在 `reports/`，不把下载缓存提交进仓库。

## SIMAP 兼容补丁

`patches/simap-03-syntax.patch` 是可审查差异；同名 JSON 用精确上下文及输入/输出 SHA-256 约束自动应用。构建同时检查二者一致。

改动仅限：补声明已使用的 yang/inet import；修复 hybrid description 提前闭合的引号；闭合 linked-termination-point 和 linked-node 的 list。新增 import 使用包内固定 RFC 9911 类型。保留 namespace 与原文 revision，并通过包清单和补丁摘要明确这是本地工作副本，不能与未经修改的同 revision 原文混同。

没有补入 §7 的四项 feature，没有修改 network-category-none 的继承，没有增加 default/mandatory/when/must，没有补写虚拟设备映射或外部请求格式。无效原文无法进行完整 AST 等价证明；这里只能核验精确改动范围、原文描述依据和修复后约束表现，不能声称形式化证明了语义等价。

## 数据作用域

- topology profile：全网拓扑、SIMAP、L2/L3、inventory 及外部关联定义。
- device profile：单个设备的接口、IP、网络实例和路由；两台 VSR 的同名接口分别放在各自数据树，不能合并成一个全网 interfaces 列表。
- 目前使用 RFC 7895 形式的验证库供 Yangson 装载，不代表实现了服务器的 RFC 8525 接口。
- RFC 8528 挂载需要运行时装载及 parent-reference 处理。当前 Yangson 会拒绝直接塞进 vrf-root 的路由树；这个反例明确记录工具边界，不删除子树假装 mount 已通过。

完整模型仍有未闭合项：虚拟 topology node→inventory/device 的标准关联、SIMAP 原文语义缺口、挂载运行时及全部 TE/协议扩展。缺口不以项目新字段填补。

## 实验实例和验证边界

`instances/` 包含 6 个原生 RFC 7951 实例文件及独立工程溯源清单。清单不送入 YANG 数据树；其中没有认证信息。`build_ietf_lab_instances.py` 只针对已审查的固定批次进行离线映射，先检查所有原始/清理后文件的摘要，再生成实例。它不是通用 CLI 解析器，也不接入生产采集流程。`--check` 比较已提交实例与重建结果，省略时重新生成。

| 实例 | 映射范围 | 验证结论 |
|---|---|---|
| `topology-observed.json` | 2 节点、4 个节点内 TP、4 条有向 LLDP 邻接；使用原生 L2 属性和 SIMAP live 类别 | Schema 通过；邻接不证明直连物理线缆，不据此制造 Hub/交换机实体 |
| `inventory.json` | 2 个 ne-virtual、软件版本及名称 | Schema 通过；虚拟属性依据 GNS3 实验部署。同名 H3C 不合并，未虚构硬件组件承载序列号 |
| `vsr-1-routes.json` / `vsr-2-routes.json` | 18 / 19 条路由：目的前缀、协议类别、优先级、下一跳地址 | 原生 IPv4 unicast RIB 子集通过；保留 0.0.0.0/32 和特殊地址行，不擅改成默认路由或丢弃动作 |
| `vsr-1-interfaces.partial.json` / `vsr-2-interfaces.partial.json` | 各 6 条 IP 接口行的类型、状态、地址/掩码 | **完整 operational 校验失败**：缺 statistics/discontinuity-time；明确作为缺失证据保存 |

接口必填要求见 [RFC 8343 §5](https://www.rfc-editor.org/rfc/rfc8343.html#section-5)。不能用采集时间或估算开机时间代替计数器中断时间，也不因缺值修改 mandatory。路由字段及 address-family 约束依据 [RFC 8349 §7](https://www.rfc-editor.org/rfc/rfc8349.html#section-7)，OSPF 身份依据 [RFC 9129 §3](https://www.rfc-editor.org/rfc/rfc9129.html#section-3)。O_INTRA 只能在当前证据下映射到 ospf 基类；不推定版本、进程或 area-id。成本和出口原值保留在独立溯源清单，未借标准字段塞入自定义含义。

路由实例的 RIB 名是验证投影标识，不是设备原生 YANG 名。为分离路由子集与不完整接口树，未填 outgoing-interface；不能据此进行完整转发路径计算。L2 TP 的 interface-name 是显示名称，不会自动解析到另一个文件中的接口；同名 node-id/ne-id 也不构成标准关联。无 VPN 实例时不人为增加默认 NI。

验证脚本现含 24 个正反例/实例结果。负例必须命中预期诊断；“预期匹配”不等于“所有实例有效”。报告明确保留接口缺失、挂载拒绝及 Schema 未强制的语义要求。关联边界见 `associations.md`。
