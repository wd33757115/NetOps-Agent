# 规则治理业务包

`app.yaml` 是装配入口，登记前端、API、Agent 工具、Celery 队列、Beat 和生命周期。
前端入口为 `/apps/rule-governance`，设备维护为 `/apps/rule-governance/devices`，
API 为 `/api/apps/rule-governance`；旧规则 URL 转入同一 App 授权。

## 业务与执行

- `backend/parser`、`parser_generation`、`rule_control`：解析、规则生成、校验、复核、发布与自动推进。
- `backend/check_items`：目录治理、评估策略资产、运行包装配、灰度验证和反馈。
- `backend/source_api.py`、`source_tasks.py`：规则源样本与直接导入任务。
- `backend/generators`：10 组生成、规划、验证、回放及资产操作的真实实现和元数据。
- `backend/rule_control/generation.py`：明确的 Python 固定业务顺序；按样本冻结的资产要求执行
  Observation、规划、Policy、Event、校验、历史回放及候选持久化。
- `backend/rule_control/generation_jobs.py`：复用控制表保存请求、身份、各步结果和生成产物。
  同种子串行提交；提交与种子历史同一事务。只重派未开始的任务，中断写入进入待核查，
  查询页可查看候选并填写核查说明结案；结案不会批准资产或自动重跑。
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

实施、验收证据与本地启用状态见 `docs/netops-capability-packages-p4.md`。
