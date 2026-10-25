# 防火墙策略业务能力包

`firewall-policy` 1.6.2 包含策略计算、拓扑证据、配置校验、预览、申请表导出、IP 地址封禁、变更工单、前端、API、任务、独立迁移和 Agent 工具。`app.yaml` 是唯一平台注册入口。复制本目录并按平台安装流程构建、安装、校验和启用即可接入；不在运行时安装依赖。

## 使用

- 页面：`/apps/firewall-policy`。上传 XLSX，可选 JSON 拓扑，填写工单号并生成。
- 申请表：下载的模板、按资源台账生成的申请表、生成后导出的“策略申请表.xlsx”是同一个模板（`backend/engine/policy/scripts/request_template.py`）。第 1–8 行是标题、说明和示例，第 9 行是表头，从第 10 行开始填写；第二张表“填写说明”解释各列。读取时按表头定位，表头以上的示例不会被读成申请。
- 按资源台账生成：账号有资源台账应用的授权时，申请表区域多出“按资源台账生成”。填写源、目的（IP、网段、范围或业务系统，可限定子系统）、可选端口、有效期和长连接，先预览台账拼出的访问关系，再生成策略；申请表由服务端按台账生成，作为该账号的上传保存，之后与上传的申请表走同一条流程。下发到哪台防火墙、哪个安全域仍按拓扑计算。
- 任务提交后地址携带 `?run=...`，可关闭页面并从历史记录恢复。取消关闭任务，重试创建新任务并保留原记录。
- 配置包、预览和申请表来自同一份已校验计划；工单引用服务端的完成计划，客户端只可填写工单头。只生成文件，不下发设备、不提交 ITSM 审批。
- 有效期与长连接（华为 USG6000、H3C F5000）：
  - `start_time`、`end_time` 两列须同时填写或同时留空，精确到分钟；`end_time` 只写日期（或写 0 点）表示当天结束。一张申请表只能有一个有效期，设备上建以工单号命名的时间段（华为 `time-range` 视图的 `absolute-range`，H3C 系统视图 `time-range … from … to …`），规则里引用它。工单号须以英文字母开头，只含字母、数字、下划线或短横线，最多 32 个字符。
  - `long_link` 填老化小时数（0–24000，0 表示永不老化）或“是”（168 小时），只对 TCP 生效；华为生成 `long-link enable` 与 `long-link aging-time`，H3C 生成规则内的 `session persistent aging-time`。tcp/udp 申请只在 TCP 规则上开启。
  - 有效期或长连接不同的申请不会合并到同一条策略；CLI 回读同时核对时间段和长连接。H3C F1000、天融信、山石暂不支持，申请行填写了这些列且路径经过这些设备时拒绝生成。
- 默认拓扑使用包内 `backend/engine/policy/scripts/topology.json`，提交时保存快照。生产使用前应核对该拓扑或上传自己的拓扑。

## IP 地址封禁

页面 `/apps/firewall-policy/block`（顶部切换“策略生成 / IP 封禁”）。导入一份地址名单，在所选防火墙上用“地址本 + 安全策略”拒绝这些来源的访问。

- 名单：XLSX 的“封禁地址”表（IP地址、到期时间、备注）和“白名单”表（IP地址、备注），找不到表名时按第一、第二张表读取；或一个 CSV 名单，白名单另选一个 CSV/XLSX 文件。表头可省略，CSV 支持 UTF-8 和 GBK。模板从 `GET /block/template` 下载。
- 地址：一格可写多个地址，支持 IP、CIDR、掩码写法和地址范围（含 `x.x.x.a-b`）。无效地址、IPv6、已过期、超过一个 /16（未勾选“允许大范围封禁”）的行不封禁并逐行说明；`0.0.0.0/0` 始终拒绝。内网、组播等地址照常封禁并给出提示。白名单有无效行时整批拒绝生成，避免漏掉必须放行的地址。
- 合并：按到期时间分组，组内地址做区间合并，再扣除白名单；同一地址出现在多行时按最晚到期（永久优先）归组，各组互不重叠。每段连续地址是一条成员（主机、整网段或范围），每个地址本最多 2000 条（可调小），超出拆分；每条规则最多引用 32 个地址本。
- 设备：从拓扑的防火墙中选择，或手动添加设备名和类型。支持华为 USG（`ip address-set … type object`、`source-address address-set`、`action deny`、`rule move … top`）和 H3C F5000/F1000（`object-group ip address`、`source-ip`、`action drop`、`move rule name … top`）。规则不写安全域、目的地址和服务，对所有安全域的入方向流量生效。
- 到期：留空为永久；有到期时间的组建一个时间段（从生成当天 0 点到到期时间），规则引用它，到期后自动失效。
- 批次：每次导入新建一批对象，名称为 `BLK_<工单号>_<摘要>` 加 `_001`（地址本）、`_R01`（规则）、`_T01`（时间段），不改动其他批次。生成后按厂商回读封禁命令和清理命令：只允许拒绝动作、地址本条数不超限、覆盖范围等于计算结果且不含白名单地址、清理命令正好删除本批次的规则、地址本和时间段。
- 产物：每台设备的封禁命令和清理命令、封禁核对表（汇总、地址本成员及来源行、名单逐行结果、白名单剔除、问题）打包下载；变更工单以清理命令作回退方案。页面只预览地址本成员的开头，完整命令以下载包为准。
- 暂不支持从历史批次中解封部分地址（需要时整批清理后重新导入）。

## API 与权限

所有入口均在 `/api/apps/firewall-policy`，由平台统一校验 App 授权，不设置应用内角色或功能权限。个人上传和任务保持账号数据隔离；不提供跨账号代操作。

| 接口 | 用途 |
|---|---|
| `GET /template` | 下载空白申请模板 |
| `GET /ledger` | 当前账号能否按资源台账生成（资源台账已安装且已授权） |
| `POST /ledger/preview` | 按台账拼出的申请行和说明，不保存 |
| `POST /ledger/file` | 按台账生成的申请表文件 |
| `POST /ledger/uploads` | 按台账生成申请表并保存为该账号的上传，返回上传 ID；request_id 幂等 |
| `POST /uploads?filename=...&kind=policy、topology、block或whitelist&request_id=...` | 原始二进制上传，最大 10 MiB |
| `POST /runs` | 上传 ID、工单号、合并和业务路径选项；request_id 幂等 |
| `GET /block/template` | 下载封禁名单模板 |
| `GET /block/devices` | 拓扑中的防火墙及是否支持封禁 |
| `POST /block/runs` | 名单与白名单上传 ID、工单号、设备（名称和类型）、地址本上限、是否允许大范围；request_id 幂等 |
| `GET /runs?offset=0&limit=20&kind=policy或block` | 当前账号策略任务或封禁批次历史 |
| `GET /runs/{id}` | 任务状态、结果、最近 50 个工单子任务 |
| `POST /runs/{id}/tickets` | 策略任务或封禁批次的变更工单；request_id 和 fields，拒绝传入 manifest、命令或任意 URL |
| `POST /runs/{id}/cancel` | 取消 queued/running 任务 |
| `POST /runs/{id}/retry` | 使用新的 request_id 重试失败或取消的任务，返回新任务 |
| `GET /runs/{id}/artifacts/{key}` | 重新授权并校验 SHA-256 后下载，不发布 MinIO 签名 URL |

Agent 工具与 HTTP API 调用同一业务服务，身份由平台提供。文件需先由应用上传接口接收，不能将聊天附件路径当作上传 ID。

## Workflow 动作

`app.yaml` 的 `workflow.actions` 导出三个动作，供 Workflow 步骤以 `skill: app:firewall-policy/<动作>` 调用（`src/workflows/itsm/` 下的流程即如此）：

| 动作 | 做什么 | 结果与产物 |
|---|---|---|
| `draft_request` | 按资源台账生成策略申请表：`source`、`destination` 填 IP、网段、范围或业务系统名称，业务系统取其未下线主机的 IP，端口取目的主机登记的服务端口（或 `ports` 指定，如 `tcp/443,udp/53`）；可带 `start_time`、`end_time`、`long_link`。经平台工具 `resource_ledger__plan_access` 以发起人身份读取台账（需要资源台账应用及其授权），申请表存入发起人网盘“防火墙申请表”文件夹 | `policy_file_url`（`netdisk:<文件ID>`，交给 `generate_policy`）、`rows`、`notes`；产物 `policy_request_draft`。台账只提供地址和端口，下发到哪台防火墙、哪个安全域由 `generate_policy` 按拓扑计算 |
| `generate_policy` | 取得 `policy_file_url`（及可选 `topology_file_url`）：`netdisk:<文件ID>` 以运行所属用户的身份从网盘读取（聊天附件就是这种），其他只接受 http(s) 下载，均不超过 10 MB；作为该用户的上传保存，提交策略任务并等待完成（最长 360 秒） | `policy_run_id`、`devices`、`risk_level`；产物 `config_zip`、`policy_preview`、`policy_request` |
| `write_change_ticket` | 按 `policy_run_id` 生成变更工单，只接受工单头字段 | `ticket_run_id`；产物 `change_excel` |

`firewall-access-from-ledger` 流程把三个动作串起来，并开放聊天启动（聊天里附带的拓扑 JSON 作为 `topology_file_url` 交给 `generate_policy`，不附带时用默认拓扑）：用户说“开通北京 10.248.255.11 到西安风险评估系统的防火墙策略”并给出工单号即可，不用上传申请表。台账里查不到的地址照常放行，在结果的 `notes` 里说明。

动作以 Workflow 运行所属的用户执行，受该用户的应用授权约束；没有用户的运行（未认证的 Webhook）直接失败。任务和上传与页面提交的一样出现在该用户的历史记录里。请求标识由 Workflow 运行编号生成，同一运行重投递不会重复提交，重试 Workflow 会提交新任务。产物由平台保存为步骤产物，下载链接由平台签名，本包仍不发布 MinIO 签名地址。

## 持久化与执行

包拥有 `app_firewall_uploads`、`app_firewall_runs` 和独立迁移链 `firewall_policy_0001`。文件存入私有 MinIO `apps/firewall-policy/`；数据库只记录校验值和私有对象键。

队列为 `app.firewall-policy`。30 秒补偿扫描保证已入库但未成功投递的任务可恢复。60 秒租约，算法进程每 5 秒检查任务和授权，计算上限 300 秒；每个任务最多自动执行 3 次。失败或超时不会发布半成品。撤销授权、停用 App 后拒绝新请求，Worker 检查撤权并关闭任务；旧 Worker 的迟到结果不能覆盖新租约。停用保留数据和历史产物。

平台依赖：PostgreSQL、MinIO、Celery Broker、Django 授权服务及 `NETOPS_APP_INTERNAL_SECRET`。Worker/Beat 使用平台动态队列与调度配置，生产启用必须通过 Gateway、Worker、Beat 和前端构建摘要检查。

文件与业务记录暂不自动删除；中断或并发上传可能留下未引用对象，需按数据库引用核对后由运维清理。备份需同时覆盖业务数据库和 MinIO。禁止删除仍被运行任务、上传或完成产物引用的对象。

## 兼容边界

旧 `src/skills` 脚本路径是到本包的兼容导入/CLI 入口，不再维护第二份实现。旧 Skill/test-run 和旧工作流中的对应 Skill 调用返回应用入口提示，避免绕过 App 授权。历史 Skill 产物保留原路径，未自动转换为新任务；ITSM 自动流程已改为调用本包的 Workflow 动作。宿主页面及服务文件仅保留兼容导出。

