<!-- document-status: archived -->
> 历史版本交付/设计记录；当时的部署状态、待办和测试数量不代表当前版本。当前状态见[实施状态](netops-architecture-implementation-plan.md)。

# P3：防火墙策略完整业务包

日期：2026-09-19。包：`src/apps/firewall_policy`，版本 1.0.0。

## 已交付

原策略算法、规则等价合并、厂商配置与校验、证据预览、申请表导出和 ITSM Excel 工单实现迁入包内。实际工单模板和申请模板随包保存。宿主只保留兼容导入；新页面通过清单自动发现，API、任务、调度和 Agent 工具由内核注册。

页面通过独立文件上传和任务接口提交申请，不再依赖聊天会话或同步 test-run。策略生成成功后保存完整计划及 manifest；工单引用父任务，客户端不能替换设备命令。配置下载、HTML 预览、申请表和工单下载通过应用接口重新鉴权，并校验文件摘要。

任务拥有幂等请求标识和持久状态。页面 URL 保存任务 ID，刷新后恢复策略结果和最近的工单任务；历史记录显示最近 20 个策略任务，API 可分页查询更早记录。失败/取消后重试创建新记录，沿用原输入快照，保留原记录。任务中断有租约和补偿扫描，旧执行者不能覆盖新结果。

数据库新增两张业务表，使用独立 Alembic 版本表，无历史平台表重建。默认拓扑在提交时冻结，包升级不改变旧任务的输入。MinIO 对象在包专属前缀下保持私有。

统一 App 授权覆盖页面入口、API、Worker、工具及下载。应用内部没有角色、按钮或额外功能权限；当前个人上传/任务仍按账号隔离。撤权和停用会拒绝新执行，正在计算的任务定期检查授权，禁止发布失效结果。

## 运行与启用

已执行本地 PostgreSQL 备份和安装迁移。当前安装状态：

| App | 版本 | 状态 | 启用 | 授权记录数 |
|---|---|---|---|---|
| official-documents | 1.1.0 | installed | false | 2 |
| delivery-probe | 1.1.0 | installed | false | 0 |
| firewall-policy | 1.0.0 | installed | false | 0 |

三个包的安装摘要均与当前源码匹配。本地备份：`.runtime/app-package-backups/before-p3-20260919-133646.dump`（47,396,731 字节）。防火墙业务迁移头：`firewall_policy_0001`。

部署时使用已有平台安装和就绪流程；此批没有部署长期运行的 Gateway/Worker/Beat，也没有重建全套生产 Docker 镜像。

1. 在构建环境检查包依赖，执行前端 `npm run build`，发布完整后端与前端构建。
2. 在迁移窗口执行 `python web/django_backend/manage.py app_packages install firewall-policy`；后续升级使用 `upgrade`。
3. 启动同版本 Gateway、Worker 与唯一 Beat。Worker 需消费 `app.firewall-policy`；使用平台 Worker 启动器会自动发现包队列。
4. 配置 Django 授权地址、至少 32 字符的应用内部共享密钥、业务数据库、MinIO 和 Broker。核对默认拓扑，或在每次申请时上传明确的 JSON 拓扑。
5. 执行 `python web/django_backend/manage.py app_packages enable firewall-policy --gateway-url <每个Gateway地址> --frontend-url <生产前端地址>`，由平台检查真实部署就绪；再在用户管理页赋予 App 使用权。

旧环境缺少就绪条件时，不应直接改数据库 enable 标志绕过检查。

## 验证

| 范围 | 结果 |
|---|---|
| 原策略/预览/工单算法与新业务包 | 68 项通过；真实 XLSX 与 JSON 输入、厂商命令、ZIP 和工单内容校验 |
| 平台内核与包交付回归 | 20 项通过 |
| PostgreSQL 独立 schema | 13 项通过；迁移、幂等、任务恢复、取消、撤权和下载隔离 |
| Django 权限、生命周期与真实联调 | 19 项通过，包含既有公文跨服务回归和内部回查限流验证 |
| 前端单元测试 | 37 个文件、184 项通过 |
| 桌面及移动 Chrome | 18 项通过；上传、策略核对、业务路径、工单信息、刷新恢复、取消重试、授权下载、无障碍 |
| 真实交付联调 | 隔离 Redis、实际 Worker/Beat、BFF/Gateway HTTP、MinIO 和生产前端校验通过 |
| 静态与构建 | 新包 Ruff/ESLint、生产构建、Django 无迁移漂移、diff 空白检查通过 |

真实联调包含超过旧 Django 2.5 MiB 默认限制的 JSON 上传，真实算法任务和工单子任务、Agent 读取同一记录、撤权后禁止下载，以及停止消费后的任务排空。隔离数据库和测试对象在测试退出时清理。测试 Worker、Beat、HTTP 服务已退出，独立 Redis 容器已停止并自动删除。

集成测试发现内部授权请求会与用户操作重复计入同一限流额度，已改为：仅有效服务签名的内部授权回查豁免重复计数；外部业务请求、无效签名和原有授权判断不变。新增用例覆盖伪造头不能取得豁免。

## 边界与后续

- 旧 Skill/test-run、旧工作流中的防火墙和工单 Skill 调用已被统一入口挡住，返回 App 提示。历史产物保留，未自动迁成任务记录；旧 ITSM 自动流程尚未重写。
- 本能力仅生成配置及工单，不向设备下发、不提交审批。策略对应的设备版本和实际拓扑仍由用户核对。
- 当前不自动删除上传及历史产物；异常中断可能留下未引用对象，需按数据库引用核对后清理。详细契约见包内 README。
- DeepSeek Harness SDK 尚未替换 LangGraph；该决策保留到 P6 实测。下一批按计划实施 P4 规则治理业务包。
