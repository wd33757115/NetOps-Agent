# 设备管理

独立设备资产应用：资产查询与编辑、导入预览、批量操作、动态分组、连接检测、身份发现、巡检命令集绑定、平台模块健康。

- 前端 `/apps/device-management`，API `/api/apps/device-management/devices`。
- 仅检查 `device-management` App 使用权限，不要求规则治理或巡检权限。
- 巡检继续通过自己的只读设备接口选择范围和发起巡检；设备应用不承接巡检执行。
- 资产仓储、凭据解析、就绪投影是共享平台基础设施，沿用现有表，不迁移或复制设备数据。
- 命令集操作复用现有已审计的采集配置服务，独立注册有限的设备配置 API；不开放规则生成、审批或发布。
- 原 `/devices`、`/admin/netops/devices`、`/apps/rule-governance/devices` 跳转到新应用。
- 发布时由 BFF 数据迁移 `0004_device_management_grants`，一次性继承原规则治理的直接与用户组授权；之后两应用权限独立维护。
