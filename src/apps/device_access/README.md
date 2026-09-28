# 设备访问 App

公共 SSH 的业务入口，独立于拓扑和巡检。入口 `/apps/device-access`。

人工创建的连接绑定账号、管理域、IP、端口、允许的诊断命令集，默认一小时有效。密码只以加密短效引用保存在 Redis。终端是另一个授权动作；票据单次有效，首帧传递，不放入 URL。连接撤销会阻止新的命令，并在终端检查点关闭会话。

`device_access__collect` 提供 DSH 可调用的固定命令集采集；不接受密码或任意命令。助手只有在用户显式选择连接后，才启用独立的诊断 workspace。认证失败或中断后不自动重试。

完整参数、范围文件与 known_hosts 配置见 [实施方案](../../../docs/device-access-topology-plan.md)。
