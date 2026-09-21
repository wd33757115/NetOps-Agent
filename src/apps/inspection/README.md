# 网络巡检 App

`app.yaml` 自注册前端、API、Agent 工具、Celery 任务与恢复调度。
复制目录后按平台流程构建并重启服务、安装和启用，即可出现在工作台；不支持运行中热插拔。

- 入口：`/apps/inspection`；兼容 `/apps/patrol`。API：`/api/apps/inspection`。
- `backend/launch.py` / `submission.py`：幂等提交、冻结范围和资产、失败重试及重跑。
- `backend/online_tasks.py`：在线采集或经过核验的离线当前证据重放，随后依次归一化、变化检测、事件与检查项结论、报告。
- `backend/offline_tasks.py`：普通离线巡检和双周期历史比较；复用现有持久记录，不更新在线当前状态。
- `backend/patrol/`、`check_items/`：报告、原始证据交付、风险事件、当前证据核验等业务服务及报告资源。
- `frontend/`：巡检工作区、离线工作台适配、证据、风险、报告签发和包内 API 客户端。
- `access.py` / `lifecycle.py`：统一 App 使用权限、Worker 起跑再授权、执行与停用排空锁。

生产执行使用固定 Python 流程与 Celery，不创建 Workflow。历史 Workflow 仅用于读取和取消。
复用现有批次、冻结计划、PatrolRun、离线作业、报告记录以及 `netops.patrol` 队列；没有新任务表、节点引擎或队列。

设备库存、采集器、证据存储及解析/规则求值属于共享基础能力；规则发布资产通过既有运行时读取契约消费。
共享离线文件服务位于 `src/platform/evidence`，以服务端绑定的 purpose 区分规则样本与巡检，
因此规则样本不要求巡检 App 授权，巡检不要求规则编辑权限。原 core/gateway 路径保留兼容导入。

已有个人作业及文件所有权保留。独立核验人读取已提交的当前证据证明，不因此获得个人文件修改权限。
报告下载、旧 API 和 Agent 工具同样受巡检 App 授权约束；长连接定期重新检查授权。

停用先关闭准入和 Beat，再等待执行锁与请求释放，结算未完成任务，保留全部历史数据。
旧版本 Worker 必须先排空；代码升级后重建前端并更新安装摘要，再启用新版本。
验收记录、部署与回退说明见 [P5 实施记录](../../../docs/netops-capability-packages-p5.md)。

1.1.0：`backend/diagnostics.py` 提供有界、只读、无 Workflow 关联查询的诊断契约。
HTTP 位于 `/api/apps/inspection/diagnosis/{search_devices,list_runs,list_events,list_snapshots,read_snapshot}`，
全部使用 POST JSON 查询参数并继承 inspection App 准入；Agent 工具调用相同函数。
默认每页10条、最大20条，可按设备、运行ID、采集时间范围查询；原文每次最多4000字符，先脱敏再分页。
不返回设备凭据、任意扩展属性或整份配置；不会触发采集、状态推进、报告生成或实时健康判断。
资产查询由 `src/platform/inventory/facts.py` 公共契约提供，助手不直接读取业务表。

1.2.0：巡检独立宽版工作区，与防火墙策略、资料管理统一视觉。任务中心合并在线与离线最近任务，支持搜索、来源筛选、任务摘要与结果跳转；五个巡检入口由侧栏导航，保留 URL 深链接。清除旧页面 null 文本，适配窄屏与键盘导航。业务执行、API 与任务队列保持原契约。
