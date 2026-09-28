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

挂载后续需要具备 schema-mount 能力的验证/服务运行时，按 [RFC 8528 §3](https://www.rfc-editor.org/rfc/rfc8528.html#section-3) 分辨 inline 与 shared-schema，装载对应模块并处理 parent-reference。设备接口、路由分树通过不能替代该验收，也不能利用 LNE 为两台独立设备制造容器。

虚拟端到端关联继续查找原生标准依据；external-relationship 草案的 node/link/TP 引用作用域和 request-builder 缺口仍存在，见 `docs/network-digital-twin-model-audit.md` §5.2。现阶段不添加自定义映射、不借物理 presence 冒充虚拟关系、不调整原文 require-instance。

下一项应优先闭合虚拟关联和挂载验证路径，再按标准必填字段缺口安排采集。当前不恢复 P2-3 模板扩展，也不把接口缺值当作修改标准模型的理由。
