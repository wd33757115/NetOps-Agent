# 公共 SSH 设备访问

独立的新模块，采用 Paramiko；原巡检及 Netmiko 路径保持原状，今后也不迁移。
完整边界与后续阶段见 [实施方案](../../../docs/device-access-topology-plan.md)。

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
新的直接调用方默认只连接一次；认证和主机信任错误不会重试。拓扑必须保持 `connection_attempts=1`，所有连接失败交给人工。

只支持严格主机密钥校验；可配置服务端 `known_hosts_file`。Windows 部署应显式配置该文件。
连接配置控制提示符正则、编码、分页标记、输出上限；命令必须为单行且在精确白名单内。
自动采集使用交互 shell，提示符超时、分页、断流和输出上限都视为不完整结果，不继续使用该流。
不自动回答设备确认提示，不自动启用特权模式，也不自行发送分页设置命令。

结果中的 `output_complete` 表示已收到预期提示符且没有检测到分页、断流或超限，不能替代业务解析与覆盖校验。
连接、授权、取消和预算等批次异常会抛出；已完成证据通过逐条回调保存。回调抛错会停止批次并关闭连接。
调用方应使用 `errors.failure_code()` 或异常自身类型映射人工处理状态。

## 配额与部署

新模块在 Redis 模式下复用现有 `netops:patrol:ssh:*` 键与对应容量配置，以兼容未改动的巡检配额。
没有导入巡检模块。已有资产必须传相同 `device_id`，否则无法与其原有设备配额归为同一设备。
未知候选目标使用管理域、地址和端口组合去重。站点来自服务端资产范围，不能由浏览器随意指定。
Redis 不可用时拒绝连接；Redis 关闭时仅提供新模块的进程内配额，不能承诺跨进程或与巡检合并计数。

依赖清单为本目录 `requirements.txt`；Redis 模式还需要现有平台的 Redis 依赖和配置。
公共终端与诊断 HTTP/WS 由独立 `device-access` App 注册，拓扑由 `topology` App 注册。两者有独立迁移和依赖锁文件；没有迁移旧巡检调用方或连接实际设备。部署和设备授权文件格式见实施方案第 11 节。

## 验证

```text
python -m pytest tests/unit/test_device_access.py tests/unit/test_device_access_ssh.py tests/unit/test_device_access_admission.py
```

测试使用驱动/Redis 替身及本机 SSH 服务端。硬件适配与真实 Redis 多进程验收仍待后续执行。
