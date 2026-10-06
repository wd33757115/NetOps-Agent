# 标准树 → 查询表：路径计算的数据组织（设计说明）

2026-10-06，随 PBR（topology 0.11.0）引入。依据：[实施方案 §13](network-digital-twin-implementation.md) 第 1 条（统一流水线）。

## 1. 四步

| 步骤 | 输入 → 输出 | PBR 的实现 |
|---|---|---|
| 1. 采集与解析 | 设备原始响应 → 检查项值行 → 拓扑事实 | CLI：`topology.pbr_rules`、`topology.pbr_bindings` 的 TextFSM 模板；NETCONF：`h3c-comware7.pbr-rules`、`h3c-comware7.pbr-bindings`。两者产出相同的值行，再由 `collectors/rules.py` 投影成同样的事实 |
| 2. 标准树 | 事实 → RFC 7951 JSON（`{"tree", "gaps", "metadata"}`） | `model/device_pbr.py`：最小 OpenConfig 宿主树；表达不了的写进 `gaps`，`metadata.completeness` 为 `partial` |
| 3. 查询表 | 标准树 + 缺口 → 按分析需要组织的行 | `model/pbr.py` 的 `query_table`：每个绑定接口一行，规则按 sequence-id 排序，字段名沿用标准节点名 |
| 4. 计算 | 查询表 + 报文 → 结论 | `model/pbr.py` 的 `evaluate`；`model/path.py` 的 `policy_route` 在每跳调用 |

## 2. 约定

- **查询表只从标准树取。** 第 3 步不读事实。标准树表达不了的东西（例如本机策略），查询表也没有；路径上对应的地方记缺口，而不是绕过标准树去读事实。这样标准树就是分析的唯一依据，导出的树和路径结论不会各说各的。
- **缺口跟着走。** 第 2 步的缺口会进入查询表：规则只导出到第一个表达不了的节点为止，这一行标 `complete: false` 并带原因；绑定导不出来的接口也有一行，没有规则。第 4 步遇到这样的行时，前面的规则能决定就决定，决定不了就是 `undetermined`，路径上记缺口，不当作"没有策略"。
- **字段名用标准节点名**：`network-instance`、`interface-id`、`policy-id`、`sequence-id`、`source-address`、`next-hop`、`forwarding-action`。由标准树里的键能推出来的设备概念（如 Comware 节点号 = `sequence-id // 65536`）在模块里写成函数，不另存一列。
- **查询表在内存里建。** 目前在路径计算时按设备算一次并缓存（`_Tracer.pbr_rows`），不建数据库表；以后要持久化时，存的是标准树，查询表照样从树上取。
- **没采到内容时保持原样**：设备没有 `pbr_rules` 事实时不建查询表，路径继续打 `policy_may_redirect`/`policy_not_computed`。

## 3. ACL、NAT 以后怎么照着改

本次不改 ACL 和 NAT，只说明路线：

- **ACL**：第 2 步已有（`model/device_acl.py`，RFC 8519）。要做的是第 3 步：从 RFC 8519 树抽查询表（按接口和方向：有序的 ace、matches、forwarding 动作），`model/acl.py` 的 `evaluate` 改为读查询表；现在路径直接读 `acl_rules`/`acl_bindings` 事实。树里表达不了的规则（已记在 `acl_entry_not_expressible` 缺口里）在查询表中按 PBR 的方式处理：该 ACL 标不完整，计算到它时决定不了就是 `undetermined`。
- **NAT**：第 2 步还没有。RFC 8512 的列表要求设备不打印的数字键（实例号、策略号、池号），按"不编造列表键"目前不导出（见 `docs/topology-rule-consumption.md` NAT 一节）。要先由负责人决定键的来源（例如按平台固定规则分配，并在 metadata 写明），再照 PBR 的方式做第 2、3 步。
- **其他 CLI 采集**：同样先有标准树（M1—M5 已有的 RFC 8343/8344/8349/8529 等导出），再从树抽查询表，最后让路径改读查询表。可以一个领域一个领域地换，每换一个都用同一份样本对比换前换后的路径结论。

## 4. 校验

- 模型：`scripts/topology/build_openconfig_library.py --check` 核对锁文件里的 SHA-256 和模块库。
- 树：单测用钉住的模块（yangson）校验导出结果，并用一个错误的接口引用确认 leafref 生效。
- 一致性：同一份 BR1 样本上，CLI 与 NETCONF 的值行和事实相同（`tests/unit/test_netconf_api_policy.py`）。
