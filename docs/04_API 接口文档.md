<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# API 使用与源码索引

核对日期：2026-09-06。浏览器使用 Django BFF 的 `/api/` 路由；FastAPI 内部业务接口通常以 `/api/v1/` 开头。准确参数、分页、状态与响应模型以当前 Pydantic/OpenAPI 及客户端类型为准，避免维护重复端点清单。

## 入口对应

新业务客户端使用 `/api/apps/{app_id}/...`，例如 inspection、rule-governance、firewall-policy、official-documents、materials、assistant。下表中的旧业务前缀仅作为兼容接口定位；不能由旧 URL 绕过 App 准入。

| 功能 | BFF 前缀 | 内部实现 |
| --- | --- | --- |
| 认证与用户 | `/api/auth/` | [auth.py](../web/django_backend/bff/auth.py)、[user_admin.py](../web/django_backend/bff/user_admin.py) |
| 桌面布局 | `/api/auth/me/desktop-layout/` | [desktop.py](../web/django_backend/bff/views/desktop.py) |
| 设备 | `/api/devices/` | [devices_api.py](../src/gateway/devices_api.py)，`/api/v1/devices` |
| 规则与 Parser | `/api/parser/` | [parser_api.py](../src/gateway/parser_api.py)，`/api/v1/parser` |
| 网络巡检 | `/api/patrol/` | [patrol_api.py](../src/gateway/patrol_api.py)，`/api/v1/patrol` |
| 离线巡检 / 规则来源 | `/api/patrol/offline-jobs/`、`/api/parser/asset-source-jobs/` | [offline_patrol_api.py](../src/gateway/offline_patrol_api.py)，对应内部 `/api/v1/` 路径 |
| 公文 | `/api/official-documents/` | [official_documents_api.py](../src/gateway/official_documents_api.py)，`/api/v1/official-documents` |
| 对话、Skill、Workflow、存储 | `/api/chat/`、`/api/skills/`、`/api/workflows/`、`/api/storage/` | [BFF 路由表](../web/django_backend/bff/urls.py)和[网关注册](../src/gateway/main.py) |

其他端点从 [BFF 路由表](../web/django_backend/bff/urls.py)查找，客户端适配见 [services](../web/react_frontend/src/services/)。不要把页面地址如 `/apps/patrol` 当作 API。

本地已启动并允许本地调试的 FastAPI 可通过 `/docs`、`/openapi.json` 查看实时合同。生产 BFF 来源检查可能阻止直连文档端点；不要为了查看 OpenAPI 而开放内部网关。

## 认证与权限

登录、刷新、登出使用 `/api/auth/login/`、`refresh/`、`logout/`；当前用户为 `GET /api/auth/me/`。BFF 校验 JWT 并转发用户、角色、会话与能力信息，后端仍执行业务授权。

业务 App 使用统一 App 授权，模型工具还需来源 App 使用权。已存在的业务归属与发布条件继续校验，见[权限指南](permissions-guide.md)。

下载对象、文档版本、个人桌面布局和会话须保留对应归属校验。服务端从认证结果确定用户，不能接受客户端任意指定其他 user_id 作为所有者。

## 响应与错误

成功响应按各端点合同处理。文件下载、流式事件、异步提交与普通 JSON 不使用同一种消费方式，不能一律从 `data` 字段读取。

统一异常处理见 [exception_handlers.py](../src/gateway/exception_handlers.py)，稳定错误码见 [exceptions.py](../src/core/exceptions.py)。典型错误：

```json
{
  "success": false,
  "error": {
    "code": "validation_error",
    "message": "参数校验失败",
    "details": {}
  },
  "request_id": "请求标识"
}
```

| HTTP 状态 | 排查方向 |
| --- | --- |
| 401 | token 缺失、过期或会话吊销 |
| 403 | 用户/能力限制，或未通过 BFF 来源检查 |
| 409 | 版本冲突、输入/发布状态变化或业务门禁冲突；保留用户未保存内容 |
| 422 | 请求字段或业务合同校验；查看 error.details，不能只凭状态码判断认证问题 |
| 503 / 504 | 所需服务不可用或超时；检查目标队列、存储及网关日志 |

调试使用响应中的 request_id 关联日志。不要向界面直接展示内部对象存储凭据、连接串或未过滤的异常堆栈。

## 长任务与并发

规则制作、公文和巡检可能异步返回任务标识。轮询或订阅状态，成功后按服务端结果打开产物；刷新页面应恢复已有任务，不重复启动。

桌面布局、公文保存及候选应用使用版本/修订校验；冲突时显式重新读取或保留本地草稿。取消或暂停不意味着已经在执行的外部调用立即消失，迟到结果需由服务端租约/版本检查拒绝。

具体操作顺序见 [自动制作](patrol-rule-autopilot-guide-2026-09-05.md)、[网络巡检](patrol-workflow-refactor-guide-2026-09-05.md)与[公文](official-document-workspace-guide-2026-09-05.md)。API 的读写权限、幂等与失败语义应通过对应测试验证，见 [维护指南](maintenance.md)。

个人桌面 API 的 wallpaper 为预设 ID 或有界 JPEG data URL，与布局一起按认证用户和 revision 保存；旧客户端不提交 wallpaper 时保留原设置。
