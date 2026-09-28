# 原生关联核验（IETF-1.2）

本项交付关联约束及可执行正反例，不声称已经实现全网设备挂载服务。来源全部沿用固定锁定版本。

| 关联 | 原生定义 | 本轮验证与结论 |
|---|---|---|
| node → NE（物理） | inventory-topology -11 的 inventory-mapping-attributes/ne-ref；inventory -19 的 ne-ref | 带 inventory-topology 类型的物理实例通过；目标缺失也通过，因为 require-instance false。不能用通过结果证明目标已解析 |
| node → NE（虚拟） | software -04 定义 ne-virtual，但物理映射的 presence/description 限定物理语义 | 把 ne-virtual 填入物理映射仍能通过 Schema，作为反例保留，不能用于 VSR 正式关联 |
| TP → 设备接口 | RFC 8944 interface-name 是 string；RFC 8343 interface-ref 是设备树内 leafref | 不能把两个文件同名字符串视作已解析引用。实验保存 LLDP 原生端口全名及设备命令原始接口名，未增加项目关系字段 |
| 设备接口 → NI | RFC 8529 bind-ni-name 指向同一设备的 network-instance/name | 配置正例通过；目标不存在时命中 instance-required。全网两台设备各自作用域，不合并接口树 |
| NI → 路由树 | RFC 8529 vrf-root 上的 RFC 8528 mount-point | 当前 Yangson 未装载挂载 Schema，直接嵌套 routing 报 RawMemberError；不能删掉 routing 后宣称挂载通过 |
| 路由 → IPv4 地址族 | RFC 8349 ipv4-unicast identity / augment 的 when | 必须用 ietf-ipv4-unicast-routing:ipv4-unicast；仅用 ipv4 基类不满足前缀/下一跳 augment 条件 |
| 路由 → 协议 | RFC 8349 source-protocol + RFC 9129 ospf identity | 完整导入 OSPF 模块及依赖；不新建 O_INTRA identity，不把协议 cost 填入 preference |

上述是首轮 Yangson 的边界；续轮已使用具备 schema-mount 能力的 libyang 5.8.6 验证原生 NI 挂载，按 [RFC 8528 §3](https://www.rfc-editor.org/rfc/rfc8528.html#section-3) 分辨 inline 与 shared-schema 并处理 parent-reference。8 个配置正反例和后续 21 项状态/部分观测检查匹配预期，详见 [挂载验证](mount/README.md)。这不是设备接口/路由分树校验，也不能利用 LNE 为两台独立设备制造容器；生产服务和真实设备完整 operational 实例仍待验证。

虚拟端到端关联继续查找原生标准依据；external-relationship 草案的 node/link/TP 引用作用域和 request-builder 缺口仍存在，见 `docs/network-digital-twin-model-audit.md` §5.2。现阶段不添加自定义映射、不借物理 presence 冒充虚拟关系、不调整原文 require-instance。

## 续轮关联审计（IETF-1.4）

已对锁定包的 41 文件逐个解析 extension 声明：仅发现 RFC 8530 的 LNE root 和 RFC 8529 的 vrf-root/vsi-root/vv-root，共 4 个。RFC 8345、当前 SIMAP、inventory 三模块均未声明拓扑 node/NE 下的设备挂载点。报告 `reports/mount-associations.json` 保存各模块结果；这里的路径是声明路径（含 choice），不是数据路径。

进一步固定并核对历史候选，源码摘要见 `mount/history-sources.lock.json`：

- [inventory-topology -00](https://www.ietf.org/archive/id/draft-ietf-ivy-network-inventory-topology-00.html) 的 system-mount-point 是空容器；[-01](https://www.ietf.org/archive/id/draft-ietf-ivy-network-inventory-topology-01.html) 改为 policies-target-point，也未声明 RFC 8528 mount-point。两版只做语法/声明审计，不降级替换当前 -11，也不把锚点名称当作可执行挂载。
- [Peer-Mount -01](https://www.ietf.org/archive/id/draft-clemm-netmod-peermount-01.html) 研究远端数据引用，区别于 RFC 8528 的本地模型挂载。它不是自动向 RFC 8345 node 添加关联；其 §7 模块提取后还在 description 处语法失败，保留诊断，不扩展本次仅针对 SIMAP 的补丁授权。

结论限于已查候选：尚未找到可以直接组成虚拟 node→NE→设备树闭环的定义，不能断言全部 IETF 工作都不存在此能力。**方案优化：设备内挂载与全网关联拆成独立验收项**；前者配置级实验已完成，后者继续待解决。Schema Mount 本身既不发现远端 SSH 设备，也不提供凭据管理或跨 datastore 身份映射。当前不恢复 P2-3 模板扩展，不自创 node 挂载 augment，不通过同名 ID 假装引用已解析。
