# RFC 8349 路由语义核验（IETF-1.6）

核对完整原生 `ietf-routing`、`ietf-ipv4-unicast-routing`、`ietf-ipv6-unicast-routing` 模块，沿用已锁定 41 文件包与 device profile，不修改模块、feature 或既有 VSR 实例。本轮仅验证基础路由模型的关键实例语义，不能代替 OSPF/BGP/TE 等协议模型的完整审查。

依据：[RFC 8349 §4、§7–9](https://www.rfc-editor.org/rfc/rfc8349.html#section-4)。下一跳 choice 的结构、地址字段及数据类型均来自原生模块；“可否据此计算路径”的判断是本项目对证据充分性的分析，不是新增标准约束。

## 配置与状态的区别

| 内容 | 静态配置 `control-plane-protocols/.../static-routes` | 状态 `ribs/rib/routes/route` |
|---|---|---|
| 多下一跳列表 | `next-hop/next-hop-list/next-hop`，以字符串 `index` 为键 | 同名列表没有键，不包含 `index` |
| 多下一跳地址 | 地址族模块内的 `next-hop-address` | 地址族模块内的 `address` |
| 简单下一跳地址 | `next-hop-address` | 地址族模块内的 `next-hop-address` |
| 出接口 | 设备接口 leafref | 设备接口 leafref |
| 目的前缀 | 静态路由的必填键 | 地址族 augment 下的可选叶 |
| 路由元数据 | 不将状态值写回配置 | `source-protocol` 必填；`active` 是 empty，RFC 7951 编码 `[null]` |

配置的 index 仅作引用标识，不表示排序优先级或权重。状态列表不能套用配置的字段编码；IPv4、IPv6 的单下一跳和多下一跳都已分别验证。

## 39 项实例结果及限制

- IPv4/IPv6 单下一跳、双下一跳、四类特殊下一跳通过；地址族须是相应 unicast 派生 identity，基础 ipv4/ipv6 identity 不满足 augment 的 when。
- 简单/特殊/列表属于互斥 choice。必须提供下一跳选项；非法地址、未知 special 枚举、不存在的出接口、错误字段与 uint32 越界被拒绝。
- special-next-hop 四种值是 blackhole、unreachable、prohibit、receive，分别表示静默丢弃、不可达通知、管理禁止通知和本机接收。CLI 出现 0.0.0.0、127.0.0.1 或 NULL 接口，不能仅据此自行选择其中一个枚举。
- 原生 `route-preference` 表达路由偏好（较小值更优），与协议 metric 不等同。本次基础 route 上添加无命名空间 `metric` 或多下一跳 `weight` 被拒绝；今后协议扩展仍须按自己的原生定义审查。
- Schema 接受无目的前缀的状态路由、空内容的状态 next-hop 条目，也允许相同前缀的多条 active 记录。最后一种可用于表达多个候选，不是自动证明选路冲突；这些接受结果均不足以证明完整路径、唯一优选或数据平面的 ECMP/权重/散列行为。报告将其标为证据边界，未补写 mandatory/unique 或选择算法。

这些实例都是合成模型用例。eth0/eth1、文档地址、active 标记和统计时间不代表实验设备实际配置。尚未进行 IPv6/ECMP 实机验收，也没有把 VSR 的单行路由复制成多个下一跳。

## 工具异常与独立复核

Yangson 1.6.3 对两种地址族的空 next-hop choice 抛出 Python TypeError。报告的 `accepted` 为 null，保留异常，不能把它记为正确的语义拒绝。其他 37 个用例取得预期 Schema 结果。

对这两个用例使用 libyang 5.8.6 严格验证，补充原生 YANG Library 上下文，并同时运行两个只补入合法出接口的正对照。共 4 项独立检查：负例明确命中 mandatory choice，正例无诊断通过且保留输入。没有修补 Yangson、删掉原生 choice 或关闭约束。运行环境沿用[挂载验证说明](mount/README.md)。

```powershell
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_routing_semantics.py
```

报告 [routing-semantics.json](reports/routing-semantics.json) 保存全部 39 个输入、具体诊断、37 项 Yangson 验证与 2 项 libyang 复核归属、4 项独立检查、模型/脚本摘要和工具版本。中间文件写入忽略目录 `.runtime/ietf-study/routing-choice-cases/`。实际容器无网络、无设备访问；报告不声称完整数字孪生交付。
