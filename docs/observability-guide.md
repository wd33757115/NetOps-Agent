# 流式进度与 Langfuse

核对日期：2026-09-06。结构化日志、浏览器 SSE 与 Langfuse 是不同的观测通道；关闭可选 Langfuse 不应关闭基本聊天流。

## 聊天流

浏览器请求 `POST /api/chat/stream/`，由 BFF 转发到 `POST /api/v1/chat/stream`。前端通过 fetch 读取事件流，后端由 Supervisor 执行进度产生状态和结果。

当前事件包括 `trace_start`、`status`、`node_start`、`skill_execute`、`trace_update`、`final_answer` 与 `error`。`node_start` 这个历史事件名可能携带 `status: completed`，消费方应读状态而不是凭名称判断仍在执行。流式传输成功不等于业务任务完成，最终以结果/错误及对应持久任务为准。

源码：[chat_stream.py](../src/gateway/chat_stream.py)、[chatStream.ts](../web/react_frontend/src/services/chatStream.ts)。详细执行事件按用户能力过滤；管理员可收到 Langfuse 链接。角色或前端过滤不能替代后端聊天执行授权。

## 可选 Langfuse

项目当前适配 Langfuse v2 客户端接口，配置 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，并为相应进程安装观测依赖。是否启用由密钥配置判断；客户端初始化失败返回不可用并记录日志。

实现见 [langfuse.py](../src/observability/langfuse.py)及同目录模块。记录的用户、会话、图执行与节点摘要用于关联排障，不能把日志平台当作业务事实或发布审批数据库。

开发可使用 [Windows 统一 Compose](../deployment/compose.windows.yml) 的 `observability` profile；完整部署的 `observability` profile 为另一入口。不要同时启动占用同一端口的两套服务。已有环境使用自己的账号和项目密钥，旧方案中的演示账号不是生产配置。

## 关联排障

使用 request_id 连接 BFF/API 请求，使用 run_id、celery_task_id 定位后台步骤，使用 trace_id 连接可选模型/图跟踪。日志配置见 [日志规范](08_日志规范%20&%20日志字典.md)。敏感请求、原始 CLI 和模型内容按实际数据策略脱敏后再分享。

反向代理必须允许持续事件流，避免缓冲或超时截断；相应部署配置见 [部署说明](10_部署安装手册.md)。SSE 断开时先检查网络、代理和服务端状态，不能自动把任务重复提交作为通用恢复手段。

验证需覆盖无 Langfuse 配置时仍能返回 SSE 结果、事件权限过滤、超时/错误，以及最终结果持久化。相关测试位于 [tests/observability](../tests/observability/)。
