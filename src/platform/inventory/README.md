# 共享设备资产与凭据边界

`repository.py` 持有设备/设备组和连接健康的数据实现；`identity_discovery.py` 提供身份发现；
`readiness_projection.py` 汇总就绪信息；`credentials` 仅在服务端解析凭据引用。
设备管理 App 负责资产维护入口，巡检 App 负责采集入口，两者复用这里的服务。
HTTP 业务实现位于 `src/apps/device_management/backend/api.py`，此目录的 `api.py` 仅保留导入兼容。

服务不决定用户是否能使用某个 App：调用方必须完成对应 App 和数据访问校验。
设备维护检查 device-management App 授权；巡检读取资产检查 inspection App 授权。
不要给共用仓库增加规则治理权限依赖，否则只有巡检权限的用户将无法采集。

列表和检查接口只返回凭据引用及是否可用，设备 DTO 中 `password` 为 `None`。
`resolve_password` 的返回值只交给服务端连接适配器，不进入前端或 Agent 工具结果。
旧 `src.core.credentials`、`src.core.device_ops` 导入暂时兼容；新增调用使用平台路径。
