# 公共 SSH 设备访问

独立的新模块，提供 Paramiko 与独立 Netmiko 适配器；原巡检及其 Netmiko 路径保持原状，今后也不迁移。
新增受控会话使用 `DeviceAccessService.open_session()` 与 `SessionBudget`，要求显式
`device.session` grant、已识别的平台和持久化审计回调。L1/L2 在预算内执行，L3 等待审批，
L4 拒绝；用上下文管理器或 `close()` 结束。原固定包 `execute_batch()` 接口仍保留。

`NetmikoDriver(platform, known_hosts_provider=...)` 只用于已识别平台的后台采集。
provider 从设备访问 App 密钥库生成该域/地址/端口的严格信任文件；未知主机不能自动接受。
仅允许内置的临时终端宽度/分页初始化，不允许自动 enable、配置模式或配置写入；
初始化命令写入会话审计。人工终端继续使用 Paramiko。验证版本和命令见
[`drivers/verification.yaml`](drivers/verification.yaml)，Cisco/华为尚未实机验证。

凭据库路径可用 `VaultDriver.for_identified_driver()` 切换驱动，切换只使用刚成功的凭据，
不重新遍历凭据集；先关闭识别连接再重新登录。P2 平台档案依据验证记录选择驱动。

## 使用

这是可信后端 Python 接口，不是任意命令 HTTP/MCP 接口。DSH、拓扑与人工终端的业务层必须先验证用户、设备范围和操作，再签发 `AccessContext`。不得使用请求体生成授权项。

```python
from src.platform.device_access import AccessContext, AccessGrant, BatchRequest, Command
from src.platform.device_access import DeviceAccessService
from src.platform.device_access.contracts import ConnectionProfile, Target

# 这两个 resolver 从受权限保护的服务端存储读取，不接受模型提交的连接参数。
service = DeviceAccessService(
    target_resolver=authorized_target_store.get,
    profile_resolver=authorized_connection_store.get,
)

# 在上游完成当前用户和此设备的权限校验后签发；命令来自已审核配置。
context = AccessContext(
    actor_id=current_user.user_id,
    source_app="topology",  # 后续 DSH 诊断可使用 assistant
    task_id=run_id,
    grants=(AccessGrant(target_ref, profile_id, frozenset({"display version"})),),
)
result = service.execute_batch(
    context,
    BatchRequest(target_ref, profile_id, (Command("identity", "display version"),)),
    on_result=save_evidence,  # 可返回证据引用；原始输出不进入普通日志
    should_continue=is_run_active,
)
```

`ConnectionProfile` 支持人工提交的用户名/密码，也支持服务端控制的 `credential_ref`。
密码字段不出现在 repr 中。拓扑 App 与设备访问 App 已实现人工输入和临时引用；`security.py` 负责 Redis 加密、作用域与有效期，业务层管理人工介入流程。
新的直接调用方默认只连接一次；凭据库最多尝试三个不同凭据，每个一次，只有认证失败继续下一集。主机信任或网络错误立即人工处理。首版拓扑临时凭据流程仍保持 `connection_attempts=1`。

只支持严格主机密钥校验；可配置服务端 `known_hosts_file`。Windows 部署应显式配置该文件。
连接配置控制提示符正则、编码、分页标记、输出上限；命令必须为单行且在精确白名单内。
自动采集使用交互 shell，提示符超时、分页、断流和输出上限都视为不完整结果，不继续使用该流。
不自动回答设备改密/确认提示或启用特权模式。识别平台后的受控会话由两种驱动自动执行服务端白名单内的临时终端初始化（关闭分页、必要时设置行宽），并记录审计；初始化命令被拒绝则停止采集。未知平台先走识别流程，既有固定命令包仍包含分页命令。人工终端保持原始交互。

结果中的 `output_complete` 表示已收到预期提示符且没有检测到分页、断流或超限，不能替代业务解析与覆盖校验。
连接、授权、取消和预算等批次异常会抛出；已完成证据通过逐条回调保存。回调抛错会停止批次并关闭连接。
调用方应使用 `errors.failure_code()` 或异常自身类型映射人工处理状态。

## 配额与部署

新模块在 Redis 模式下复用现有 `netops:patrol:ssh:*` 键与对应容量配置，以兼容未改动的巡检配额。
没有导入巡检模块。已有资产必须传相同 `device_id`，否则无法与其原有设备配额归为同一设备。
未知候选目标使用管理域、地址和端口组合去重。站点来自服务端资产范围，不能由浏览器随意指定。
Redis 不可用时拒绝连接；Redis 关闭时仅提供新模块的进程内配额，不能承诺跨进程或与巡检合并计数。

依赖清单为本目录 `requirements.txt`；Redis 模式还需要现有平台的 Redis 依赖和配置。
公共终端与诊断 HTTP/WS 由独立 `device-access` App 注册，拓扑由 `topology` App 注册。两者有独立迁移和依赖锁文件；未迁移旧巡检调用方。已在两台实验 H3C 验证。

device-access 0.7.0 / topology 0.4.0 按 D-5 简化：不再有管理域、访问范围、凭据集和连接授权。两个 App 调用本模块时管理域固定传 `default`；本模块的 `management_domain` 参数与文件授权函数保持不变。SSH 终端用已保存会话或默认用户名密码登录，拓扑用默认或本次临时用户名密码登录；主机密钥由 SSH 终端 App 保存（终端首次连接需用户确认，拓扑首次连接自动记录，变化时都停下等待确认）。升级执行包内迁移 device_access_0006 / topology_0003，迁移会删除旧授权与凭据表，升级前先备份数据库。
