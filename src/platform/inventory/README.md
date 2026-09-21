# 共享设备资产与凭据边界

`repository.py` 持有设备/设备组和连接健康的数据实现；`identity_discovery.py` 提供身份发现；
`readiness_projection.py` 汇总就绪信息；`credentials` 仅在服务端解析凭据引用。
规则治理负责资产维护入口，巡检负责采集入口，两者复用这里的服务。

服务不决定用户是否能使用某个 App：调用方必须完成对应 App 和数据访问校验。
当前 P4-A 沿用现有入口授权，完整应用门禁在 P4-B 切换。
不要给共用仓库增加规则治理权限依赖，否则只有巡检权限的用户将无法采集。

列表和检查接口只返回凭据引用及是否可用，设备 DTO 中 `password` 为 `None`。
`resolve_password` 的返回值只交给服务端连接适配器，不进入前端或 Agent 工具结果。
旧 `src.core.credentials`、`src.core.device_ops` 导入暂时兼容；新增调用使用平台路径。
