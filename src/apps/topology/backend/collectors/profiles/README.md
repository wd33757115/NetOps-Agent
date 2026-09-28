# 已安装采集档案

`profiles.py` 只读取安装目录中的 YAML，严格校验 schema、重复键、引用、预算和命令。
不接受模型/HTTP 上传任意命令。CLI 必须是公共命令策略认可的完整 L1 只读命令；初始化
须逐字匹配公共驱动的静态关闭分页命令。解析器只能引用能力对应的已实现注册项，null
明确表示等待模板。H3C 为真实 VSR 验证，Cisco 仅提供命令档案，未进行硬件验收。

`collect.collect_node` 是后端服务入口，使用已有 `DeviceAccessService`、调用者授权、
凭据库和实时撤销检查。每节点先 Paramiko 识别，再使用识别后的连接配置；版本证据复用，
不重复发版本命令。三档总预算默认 120/300/600 秒，从识别前计时，重连不重置预算。
第二次连接固定使用首次成功凭据，不因换驱动再次轮询凭据。缺少或冲突的识别需要人工处理。

仅当型号、软件版本与公共驱动 verification.yaml 中的实测记录一致时，优先 Netmiko。
数据库主机密钥验证需要调用方提供相同信任库的 `known_hosts_provider`；缺少时保留原
Paramiko 验证器并返回 driver_warning，不能静默改用另一份文件。文件信任模式仍要求
显式 known_hosts 文件。此流程每节点最多两次成功连接，版本识别和采集都受同一总预算约束。

各模式是“请求的能力集合”，不是每个平台都具备全部能力。未配置命令报告
`no_profile_command`；CLI 拒绝为 unsupported；采集成功但解析未适配为 failed 并保留证据。
Cisco 的本地 LLDP 身份没有经过本仓库样本核实，暂不填猜测命令；`show lldp` 的状态统计
不能冒充机箱身份。IOS `show ip interface brief` 不提供前缀，也不是完整物理接口清单，
因此接口档案使用 `show interfaces`，IP 使用 `show ip interface`，均待模板适配。

命令形式参考 [Cisco IOS show ip interface](https://www.cisco.com/E-Learning/bulk/public/tac/cim/cib/using_cisco_ios_software/cmdrefs/show_ip_interface.htm)
和 [Cisco IOS LLDP 命令说明](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/cether/command/ce-cr-book/ce-s1.html)。
这些资料用于命令语义核对，不代表设备兼容性验收。

当前旧 LLDP 任务与 UI 保留兼容。新的多能力入口尚待后续任务接线与事实投影；本卡不发布、不运行现网迁移。
