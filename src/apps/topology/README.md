# 网络拓扑 App

独立 LLDP 发现应用，使用 `platform.device_access` 的新 SSH 实现。原巡检不参与本应用。

- 入口：`/apps/topology`；队列：`app.topology`。
- 用户在本 App 人工提供凭据。认证、连接、解析失败都进入 `waiting_for_input`，确认或更新凭据后才重试。
- 原始证据在私有对象存储；观测、队列、人工处理与不可变快照在 App 自有数据库表。
- Comware/NX-OS 的线上采集和离线导入使用同一个版本化解析器。
- 在线发现默认逐节点自动识别平台；无需预先知道种子或邻居型号。版本探测与 LLDP 采集共用一次 SSH 登录，手工指定仅作用于该节点，不传递给邻居。
- 未识别、证据冲突、平台尚未适配的节点保留在任务中等待人工确认或跳过；不会用种子平台盲目发送 LLDP 命令。
- 模型基线是 RFC 8345 + SIMAP YANG -03（L2 用 RFC 8944、L3 用 RFC 8346）：每次任务把 LLDP/ARP/MAC/接口地址构建成 L2、L3 两个 network，快照保存标准树、每条链路的依据和画布视图，导出即标准树。所用模块、来源、摘要与 library 在 `resources/yang/`；SIMAP 为本地语法兼容副本，见 `models/network-twin/patches/`。
- 首版是 LLDP 观测与数字孪生的数据基础；不把候选机箱等同于已确认资产，不提供仿真预测。

完整实施、部署、验收范围见 [实施方案](../../../docs/device-access-topology-plan.md)。
