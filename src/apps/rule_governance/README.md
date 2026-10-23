# 规则治理业务包

1.4.0：删除独立 ObservationContract 数据契约表、生成器、旧生成任务链及接口。新运行统一使用检查项 Value / 语义与 V2 命令适配器；历史报告与执行事实保留。

1.3.4：自动制作在人工启动或继续时恢复已取消的 Parser；重试保留检查项语义绑定，页面区分历史停用记录。

样本导入 → 本地分割与脱敏 → 待归类检查项 → 确认关联 → TextFSM / Projection。
生成入口使用关联检查项的当前语义，检查项版本变化后必须重新确认。
导入详情的旧 production 链接进入待归类页，生成接口不再隐式修改关联。

1.3.0：新增规则包导入与导出，支持依赖闭包、冲突预检、原子草稿导入、样本回放和迁移记录。
检查项与已发布资产目录可多选进入导出；导入产生本地待验证版本，Parser 复核和目录发布沿用原有服务。
规则制作、样本详情、目录、资产、覆盖回填和审计共用独立应用布局；设备管理已拆为独立 App。
宽屏展开工作区，手机使用横向导航和纵向制作任务列表；导入、编辑、复核和详情抽屉统一样式。
沿用既有接口、App 使用权限、生成/审批/发布流程和历史链接；不引入新执行器或数据库迁移。

`app.yaml` 是装配入口，登记前端、API、Agent 工具、Celery 队列、Beat 和生命周期。
前端入口为 `/apps/rule-governance`；设备维护迁至 `/apps/device-management`，
API 为 `/api/apps/rule-governance`；旧规则 URL 转入同一 App 授权。

## 业务与执行

- `backend/parser`、`parser_generation`、`rule_control`：解析、规则生成、校验、复核、发布与自动推进。
- `backend/check_items`：目录治理、评估策略资产、运行包装配、灰度验证和反馈。
- `backend/source_api.py`、`source_tasks.py`：规则源样本与直接导入任务。
- `backend/generators`：TextFSM、变化/事件候选、验证与回放的固定实现。
- `backend/check_items/canonical_rule_context.py`：依据检查项的标准语义生成规则草稿。
- `backend/rule_control/command_autopilot.py`：命令制作检查点、Parser / Projection 验证与发布。
- `backend/rollout_tasks.py`：灰度任务。采集直接交给共享 inventory 的固定巡检任务，
  读取 PatrolRun 和快照，不创建或轮询 Workflow。

规则生成、规则样本导入、灰度采集均不依赖 Workflow。五个旧业务 Workflow 不再提供新执行，
已排队的旧步骤拒绝执行；旧定义、记录和 Python 名称只供历史兼容及排空。
巡检整体固定流程切换在 P5；本包不为它新增 Workflow 适配层。

## 授权、任务和停用

只控制 App 使用权；有授权用户可执行包内全部业务操作。复核、回归、依赖摘要、版本冲突和
发布确认属于业务校验。个人样本及任务仍保留所有者边界；不伪造 admin 角色。
旧 Skill/聊天通用执行入口不能调用迁入的生成器。Agent 的 generate_rules/read_generation
工具调用相同服务，并由平台检查真实用户当前的 AppGrant。

所有治理任务与周期调度由清单进入 `app.rule-governance`。用户任务保存真实执行用户 ID，
Worker 起跑及关键写入前再次查询授权；历史未知 ID 不回填为管理员。系统维护只在应用启用时运行。
请求状态和结果保存在 PostgreSQL，Redis 仅负责投递。

停用先关闭准入，检查 Gateway 与新旧队列 Worker；应用执行及生成器子进程持有共享数据库锁，
排空需取得排他锁。排空取消未开始任务，保留中断产物并使写入 token 失效。
已隔离的待核查记录不算在途执行，但仍阻止相同业务请求重复生产。代码必须保留到排空完成。
规则样本中断对账需确认执行锁已释放，保留证据供人工检查，不自动重复导入。

## 数据与共享边界

`database.mode: legacy`，最低历史版本 `0101_patrol_report_templates`。清单登记必需的既有表；
由平台原 Alembic 链负责，不新增表、迁移版本表或数据库角色。安装前备份并校验迁移和表，
不可用 stamp 掩盖缺失结构。可变模板、映射、生成报告保持原运行数据路径。

设备与凭据位于 `src/platform/inventory`。离线文件传输、导入服务和共享结果组件暂由宿主提供，
治理入口固定 `rule_asset_source` 用途，并保持 owner_id 校验。P5 接管巡检侧的流程与结果；
这些共用服务不通过巡检 Workflow 调用。共享前端类型仍在宿主 `services/api.ts`。

巡检从 `runtime.py` 只读消费已发布、冻结版本；不需要治理 AppGrant：

```python
from src.apps.rule_governance.runtime import CheckItemRuntimeReader, ParserRuntimeBundle
reader = CheckItemRuntimeReader()
bundle = reader.get_version(frozen_version_id)
rules = reader.get_rule_assets(frozen_version_id)
parser_bundle = ParserRuntimeBundle.from_dict(saved_parser_bundle)
```

调用方负责自身业务和数据授权。发布新版本不替换历史引用，废弃版本仍可按冻结 ID 读取。
只读契约不暴露治理写操作。旧 `src/core` 路径只作兼容转发；P7 再检查并清理旧消费者。

## 部署交接

1. 备份业务库和 App 元数据；平台历史迁移到上述版本后执行 `app_packages install rule-governance`。
2. 部署同版前端、Gateway、包 Worker/Beat，同时升级仍消费 `netops.ai`、`netops.patrol` 的 Worker。
3. 用实际 Gateway/前端 URL 执行 verify/enable。校验代码摘要、服务静态资源、队列注册和唯一 Beat。
4. 通过用户管理中的应用使用权限授权。旧 netops-config 组可迁移为本 App 的组授权，不再配置包内角色。
5. 停用须等待 drain 完成；排空前不能删除代码或让旧版本 Worker 继续消费旧队列。


自动制作根据已关联检查项和脱敏 CLI 样本生成 ParserSpec 与 TextFSM，不再自动检索或优先复用 NTC 模板，也不再执行 NTC 专用 Value 改名与字段裁剪。需要复用外部模板时，由工程师通过人工模板入口上传；模板须满足当前方案并通过样本执行、运行时兼容性和正常复核发布流程。历史模板及来源记录保留，旧自动 NTC 候选不再用于新一轮生成的自动复用。自动制作状态轮询同步刷新 Parser 详情。

人工提交 Parser 候选同样绑定当前登录用户与执行标识，保存时重新验证 App 授权，避免沿用旧身份或误报历史任务缺少执行用户。

设备版本与资产信息的新字段定义见 `backend/check_items/assets/device_version_values.yaml`：13 个大小写精确匹配的 Value、7 项必填，每槽位一条记录，子槽位用配对列表保留。版本切换仍经目录治理，旧运行包引用不能绕过。

## 命令适配器删除

删除命令适配器会在同一事务内停用其活动运行包、撤下适配器各版本并取消未完成的目录变更，不再因 Active RuntimeBundle 引用返回 409。检查项、TextFSM 模板、历史执行结果及冻结运行包内容保留，其他适配器不受影响。审计事件记录此次停用的运行包版本 ID；重复请求保持幂等。已删除适配器的运行包不能通过激活或回滚重新启用。

## 已发布资产批量操作

生产资产目录支持 Parser 模板、变化策略和事件规则的单项或批量停用、删除。跨页勾选，每次最多 100 项；切换资产类型、状态或搜索条件时清空选择。操作前确认范围，完成后刷新列表和数量，逐项显示失败原因并保留失败项供重试。

停用使用 deprecated 状态，可在“已停用”列表查看；删除使用 deleted 状态，退出目录、不提供恢复，保留内容和历史引用。每项操作独立事务，失败时其资产和关联运行包状态一起回滚，不影响其他成功项。关联活动运行包同步停用，不自动退回旧规则。审计记录操作者、原因、操作前后状态和受影响运行包。

API：POST /parser/published-assets/bulk-action，参数 kind（parser/policy/event）、action（deprecate/delete）、ids 和可选 reason，沿用应用使用权限。返回逐项 results 与 succeeded/failed 数量。该功能不改变 TextFSM 模板的发布版本机制。
