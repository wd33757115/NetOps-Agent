# 思科样本检查项 Value 完善

本轮参考 7 份本地 CLI 文件的 131 段 show 输出和已安装 NTC 模板。21 项修订包含 15 项已有检查项、6 项新增检查项，共新增 133 个 Value；另外补齐 VRF 状态字段名。NTC 仅用于设计参考，不恢复自动模板复用或 Value 别名映射。

## 发布边界

21 项完善后的定义已全部通过目录治理流程发布；“接口状态运行信息和统计信息”按用户要求恢复原有 23 个 Value，不纳入本轮完善。按用户确认，已删除硬件组件资产、温度、接口 IP、ARP、MAC、堆叠和 VRRP 各自的一个旧运行包，共 7 个。删除前确认没有历史执行、运行清单或发布引用，并保存完整备份和删除审计；检查项、Parser 及其他子资产保留。活动运行包由 19 个减少至 12 个。

上述七项旧运行包已退出新巡检选择范围；需要按新定义复核采集实现、验证并重新组装运行包，才能恢复相应巡检覆盖。本次完成的是字段定义发布，不表示这些能力已经恢复运行。

版本七项必填和光模块在位但缺光功率的数据不完整要求保留。已有健康阈值、趋势策略不变；新字段先保留证据，不自动添加故障告警。型号或厂商不支持的可选字段保持缺失，不能补零。

## 样本验证限制

- NTC 本地回放：74 段解析到记录、12 段为空尚未验收、42 段没有明确对应模板、3 段模板不兼容。另有一段混入其他命令输出。解析成功不代表业务语义验收通过。
- 明确排除 NTC 宽松命令匹配造成的 memory→CPU、startup-config→standby、interface stats→interface status 误匹配。
- VRF 缺少对应原文命令样本，只完成字段参考，未宣称已验证。接口状态运行信息和统计信息保持既有定义。
- IOS 进程表与 NX-OS 汇总表不同；进程单独成项。show vpc 成员与域状态分开。
- 新定义不代表 Parser/适配器已生成或已有生产覆盖；六个新检查项还需关联命令、验证及纳入巡检 Profile。

## 字段清单

### 设备版本与资产信息（`device.version`）

状态：已发布。

保留已确认的七项必填及槽位/子槽位粒度。NTC OS/VERSION 参考 SOFTWARE_VERSION，PLATFORM/HARDWARE 参考 BOARD_TYPE，LAST_REBOOT_REASON/RELOAD_REASON 参考 REBOOT_REASON；不增加运行时别名映射。Cisco 不提供槽位或两种镜像时仍报告不完整，不能拿 BOOT_IMAGE 同时填两列。

实体键：`SLOT`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `VENDOR` |  | 是 | state / — | 设备厂商；必须来自 CLI 明示，不根据型号猜测。 |
| `SOFTWARE_VERSION` |  | 是 | state / — | 完整软件版本，保留 Version、Release 等内容，不能转成数值。 |
| `BOOT_IMAGE` |  | 是 | state / — | 启动镜像文件名或路径；不能用系统镜像代替。 |
| `SYSTEM_IMAGE` |  | 是 | state / — | 系统软件镜像文件名或路径；不能用启动镜像代替。 |
| `UPTIME` |  | 是 | text / — | 当前槽位板卡的运行时长原文；槽位值优先，只有整机原文明确适用于该槽位才可继承。正常增长不触发变化事件；重启识别需要连续采集证据。 |
| `BOARD_TYPE` |  | 是 | state / — | 当前槽位板卡型号；不把整机型号推断为板卡型号。 |
| `DRAM` |  | 否 | state / — | 当前槽位内存容量原文，保留数值及单位，不进行隐式单位换算。 |
| `FLASH` |  | 否 | state / — | 当前槽位 Flash 容量原文，保留数值及单位，不进行隐式单位换算。 |
| `PATCH_VERSION` |  | 否 | state / — | 补丁版本原文；明确未安装与未采集到必须区分。 |
| `REBOOT_REASON` |  | 否 | state / — | 对应槽位最近一次重启原因原文；不自动推断为故障。 |
| `SUBSLOT` |  | 否 | text / — | 槽位内子槽位标识列表，按原文顺序，与 SUBSLOT_INFO 同位置配对；无子槽位为 []。 |
| `SUBSLOT_INFO` |  | 否 | text / — | 子槽位模块型号或描述列表，与 SUBSLOT 同位置配对；不能丢弃或跨槽位串接。 |
| `HOSTNAME` | 是 | 否 | text / — | 设备主机名原文；不是管理 IP。 |
| `SERIAL` | 是 | 否 | text / — | 对应板卡或设备序列号；NTC 的多成员 List 不能拼成一个槽位值。 |
| `BIOS` | 是 | 否 | text / — | NX-OS BIOS 版本原文。 |
| `ROMMON` | 是 | 否 | text / — | IOS 引导程序版本；不等同于 BOOT_IMAGE。 |
| `SOFTWARE_IMAGE` | 是 | 否 | text / — | IOS 软件特性镜像标识，不能替代实际系统镜像路径。 |
| `RELEASE` | 是 | 否 | text / — | IOS Release 修饰原文。 |
| `CONFIG_REGISTER` | 是 | 否 | text / — | 配置寄存器值，保留十六进制文本。 |
| `MAC_ADDRESS` | 是 | 否 | text / — | 设备或板卡基础 MAC；多成员地址须按所属板卡展开。 |
| `RESTARTED` | 是 | 否 | text / — | 最近重启时间原文；与 UPTIME、重启原因分别保存。 |

NTC 参考：`cisco_ios_show_version.textfsm`、`cisco_nxos_show_version.textfsm`。

### 硬件组件资产（`device.inventory`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

每个物理组件一行；保留原有 model、serial_number、vendor_name 与实体键。没有组件类型时须显式指出身份缺口。

实体键：`component_type + component_id`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `model` |  | 否 | state / — | Device chassis or platform model as an inventory fact. |
| `serial_number` |  | 是 | state / — | Manufacturer serial number for one device hardware component. |
| `vendor_name` |  | 否 | state / — | Manufacturer identity reported for one device hardware component. |
| `name` | 是 | 否 | text / — | 组件名称原文，保留机箱、槽位和收发器位置。 |
| `description` | 是 | 否 | text / — | 组件完整描述。 |
| `product_id` | 是 | 否 | text / — | 厂商产品编号；PID 在 inventory 中不是进程号。 |
| `hardware_version` | 是 | 否 | text / — | 硬件修订号；不等同于软件版本。 |

NTC 参考：`cisco_ios_show_inventory.textfsm`、`cisco_nxos_show_inventory.textfsm`。

### 风扇状态（`environment.fan`）

状态：已发布。

保留 fan_status 与气流方向。NTC FAN/FAN_STATUS 对应原有 fan/fan_status；slot 只能来自明确位置。

实体键：`slot + fan`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `fan_status` |  | 是 | state / — | Current presence and operating health state of one fan. |
| `fan_airflow_direction` |  | 否 | text / None | 风扇实际气流方向原文 |
| `preferred_fan_airflow_direction` |  | 否 | text / None | 设备期望气流方向原文 |
| `fan_model` | 是 | 否 | text / — | 风扇型号；当前 NTC 模板未保留该列，来自样本 Model 列。 |
| `hardware_revision` | 是 | 否 | text / — | 风扇硬件版本原文，-- 表示未提供。 |
| `speed_rpm` | 是 | 否 | gauge / rpm | IOS 环境传感器明确给出的风扇转速；不能把十六进制 Fan Zone Speed 当 RPM。 |

NTC 参考：`cisco_nxos_show_environment.textfsm`。

### 电源状态（`environment.power`）

状态：已发布。

每个电源一行。POWER_SUPPLY_OUTPUT 对应 output_watts；禁止把设备 Total Power 汇总值复制到每个电源。

实体键：`slot + power_supply`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `power_supply_status` |  | 是 | state / — | Current presence and operating health state of one power supply. |
| `mode` |  | 否 | text / None | 电源 AC/DC 模式原文 |
| `current_amperes` |  | 否 | gauge / amperes | 此电源 Current(A)，-- 表示未提供 |
| `voltage_volts` |  | 否 | gauge / volts | 此电源 Voltage(V)，-- 表示未提供 |
| `output_watts` |  | 否 | gauge / watts | 此电源 Power(W)，不等于机框总输入功率 |
| `fan_direction` |  | 否 | text / None | 电源风扇方向原文 |
| `slot_input_watts` |  | 否 | gauge / watts | 槽位 Input Power(W)，在该槽位电源行重复展示，不能按电源求和 |
| `model` | 是 | 否 | text / — | 电源模块型号。 |
| `input_watts` | 是 | 否 | gauge / w | 单个电源实际输入功率。 |
| `capacity_watts` | 是 | 否 | gauge / w | 单个电源额定容量，不是当前输出功率。 |
| `input_state` | 是 | 否 | text / — | 输入供电状态原文。 |
| `output_state` | 是 | 否 | text / — | 输出供电状态原文。 |

NTC 参考：`cisco_nxos_show_environment.textfsm`。

### 温度传感器状态（`environment.temperature`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

MODULE/SENSOR 对应 slot/sensor；CURRENT_VALUE、MINOR_THRESHOLD、MAJOR_THRESHOLD 对应现有温度、warning 和 alarm。未知单位不自动当摄氏度。

实体键：`slot + sensor`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `temperature_celsius` |  | 是 | gauge / celsius | Current temperature reported by one device sensor. |
| `temperature_high_warning_celsius` |  | 否 | threshold / celsius | Device-reported warning threshold for one temperature sensor. |
| `temperature_high_alarm_celsius` |  | 否 | threshold / celsius | Device-reported alarm threshold for one temperature sensor. |
| `sensor_status` | 是 | 否 | text / — | 设备报告的温度传感器状态；保留 Ok/Normal 等原文。 |
| `temperature_low_warning_celsius` | 是 | 否 | threshold / celsius | 设备明确报告的低温告警阈值；未输出时缺失。 |
| `temperature_low_alarm_celsius` | 是 | 否 | threshold / celsius | 设备明确报告的低温严重告警阈值。 |

NTC 参考：`cisco_nxos_show_environment_temperature.textfsm`、`cisco_ios_show_environment_temperature.textfsm`。

### 接口 IP 与协议状态（`interface.ip_assignment`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

保留 interface/address_family 实体键；unassigned 不伪造为 0.0.0.0。接口多个地址需后续适配器明确多值粒度。

实体键：`interface + address_family`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `ip_address` |  | 是 | state / — | IP address assigned to one interface and address family. |
| `interface_status` | 是 | 否 | text / — | 接口管理/物理状态原文，administratively down 与 down 不混同。 |
| `link_status` | 是 | 否 | text / — | NX-OS Link 列原文。 |
| `protocol_status` | 是 | 否 | text / — | 接口协议状态原文。 |
| `vrf` | 是 | 否 | text / — | 接口所属 VRF；默认域只能由命令作用域明确给出。 |

NTC 参考：`cisco_ios_show_ip_interface_brief.textfsm`、`cisco_nxos_show_ip_interface_brief.textfsm`。

### 光模块监测与光功率（`interface.optical_power`）

状态：已发布。

LANE 对应 channel。保留现有 RX/TX 高低阈值和严格要求：在位但缺少光功率仍为数据不完整；不更改已有健康策略。

实体键：`interface + channel`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `transceiver_present` |  | 是 | state / — | Whether an optical transceiver is physically present and diagnosable on the interface channel. |
| `rx_power_dbm` |  | 是 | gauge / dbm | Current received optical power measurement for one interface channel. |
| `tx_power_dbm` |  | 是 | gauge / dbm | Current transmitted optical power measurement for one interface channel. |
| `rx_high_alarm_dbm` |  | 否 | threshold / dbm | Device-reported high receive-power alarm threshold for one interface channel. |
| `rx_high_warning_dbm` |  | 否 | threshold / dbm | Device-reported high receive-power warning threshold for one interface channel. |
| `rx_low_warning_dbm` |  | 否 | threshold / dbm | Device-reported low receive-power warning threshold for one interface channel. |
| `rx_low_alarm_dbm` |  | 否 | threshold / dbm | Device-reported low receive-power alarm threshold for one interface channel. |
| `tx_high_alarm_dbm` |  | 否 | threshold / dbm | Device-reported high transmit-power alarm threshold for one interface channel. |
| `tx_high_warning_dbm` |  | 否 | threshold / dbm | Device-reported high transmit-power warning threshold for one interface channel. |
| `tx_low_warning_dbm` |  | 否 | threshold / dbm | Device-reported low transmit-power warning threshold for one interface channel. |
| `tx_low_alarm_dbm` |  | 否 | threshold / dbm | Device-reported low transmit-power alarm threshold for one interface channel. |
| `part_number` |  | 否 | state / — | Optical transceiver manufacturer part number used to bind baselines. |
| `serial_number` |  | 否 | state / — | Optical transceiver serial number used to bind baselines. |
| `temperature_celsius` | 是 | 否 | gauge / celsius | 光模块温度，不是机箱环境温度。 |
| `voltage_volts` | 是 | 否 | gauge / v | 光模块供电电压。 |
| `bias_current_ma` | 是 | 否 | gauge / ma | 激光器偏置电流；mA 与 A 不可混用。 |
| `transceiver_type` | 是 | 否 | text / — | 模块类型原文，例如 QSFP-100G。 |
| `vendor_name` | 是 | 否 | text / — | 模块 name/vendor 原文，不由设备厂商推断。 |
| `revision` | 是 | 否 | text / — | 光模块硬件修订号。 |
| `wavelength_nm` | 是 | 否 | gauge / nm | 模块波长；只有源输出明确 nm 时使用。 |
| `dom_supported` | 是 | 否 | text / — | DOM 是否支持及不支持原因原文；不提供 DOM 不表示零光功率。 |
| `temperature_high_alarm_celsius` | 是 | 否 | threshold / celsius | 模块明确输出的温度 high_alarm 阈值；保留缺失，不套用通用阈值。 |
| `temperature_low_alarm_celsius` | 是 | 否 | threshold / celsius | 模块明确输出的温度 low_alarm 阈值；保留缺失，不套用通用阈值。 |
| `temperature_high_warning_celsius` | 是 | 否 | threshold / celsius | 模块明确输出的温度 high_warning 阈值；保留缺失，不套用通用阈值。 |
| `temperature_low_warning_celsius` | 是 | 否 | threshold / celsius | 模块明确输出的温度 low_warning 阈值；保留缺失，不套用通用阈值。 |
| `voltage_high_alarm_volts` | 是 | 否 | threshold / v | 模块明确输出的电压 high_alarm 阈值；保留缺失，不套用通用阈值。 |
| `voltage_low_alarm_volts` | 是 | 否 | threshold / v | 模块明确输出的电压 low_alarm 阈值；保留缺失，不套用通用阈值。 |
| `voltage_high_warning_volts` | 是 | 否 | threshold / v | 模块明确输出的电压 high_warning 阈值；保留缺失，不套用通用阈值。 |
| `voltage_low_warning_volts` | 是 | 否 | threshold / v | 模块明确输出的电压 low_warning 阈值；保留缺失，不套用通用阈值。 |
| `bias_current_high_alarm_ma` | 是 | 否 | threshold / ma | 模块明确输出的偏置电流 high_alarm 阈值；保留缺失，不套用通用阈值。 |
| `bias_current_low_alarm_ma` | 是 | 否 | threshold / ma | 模块明确输出的偏置电流 low_alarm 阈值；保留缺失，不套用通用阈值。 |
| `bias_current_high_warning_ma` | 是 | 否 | threshold / ma | 模块明确输出的偏置电流 high_warning 阈值；保留缺失，不套用通用阈值。 |
| `bias_current_low_warning_ma` | 是 | 否 | threshold / ma | 模块明确输出的偏置电流 low_warning 阈值；保留缺失，不套用通用阈值。 |

NTC 参考：`cisco_nxos_show_interface_transceiver_details.textfsm`、`cisco_ios_show_interface_transceiver.textfsm`。

### 接口状态运行信息和统计信息（`interface.runtime_statistics`）

状态：保留既有定义；本轮增加的 8 个字段已撤回，恢复原有 23 个 Value。

按用户要求，本项不参与思科样本字段扩展。既有实体键、字段语义、必填要求和健康规则均保持不变；批量完善参考包已排除本项。

实体键：`interface`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `line_status` |  | 是 | state / None | 接口物理状态，保留管理关闭及故障原因等完整原文 |
| `protocol_status` |  | 是 | state / None | 接口链路协议状态，保留括号中的状态说明 |
| `description` |  | 否 | text / None | 接口描述，空字符串也是有效描述 |
| `hw_address` |  | 否 | text / None | 接口硬件 MAC 地址 |
| `bandwidth_kbps` |  | 否 | gauge / kbps | 接口原文 Bandwidth 配置带宽，单位 kbps，不等于当前吞吐量 |
| `speed` |  | 否 | text / None | 接口当前速度原文，保留原文单位或厂商显示格式 |
| `duplex` |  | 否 | state / None | 接口双工模式原文 |
| `port_link_type` |  | 否 | state / None | 接口链路类型，保留 configured/negotiated 等原文 |
| `vlan_native` |  | 否 | gauge / None | 接口缺省 VLAN/PVID 编号 |
| `mtu` |  | 否 | gauge / bytes | 接口三层最大传输单元，不与二层最大帧长混用 |
| `l2mtu` |  | 否 | gauge / bytes | 接口二层最大帧长度，不与三层 MTU 混用 |
| `input_packets` |  | 否 | counter / count | 接口累计接收报文数 |
| `output_packets` |  | 否 | counter / count | 接口累计发送报文数 |
| `input_bytes` |  | 否 | counter / bytes | 接口累计接收字节数 |
| `output_bytes` |  | 否 | counter / bytes | 接口累计发送字节数 |
| `input_errors` |  | 否 | counter / count | 接口接收错误总数；Huawei Input 下的 Total Error；没有输出则缺失 |
| `output_errors` |  | 否 | counter / count | 接口发送错误总数；只取输出方向明确的错误总数 |
| `input_crc_errors` |  | 否 | counter / count | 接收方向 CRC/FCS 错误计数，保留厂商计数口径 |
| `last_physical_up_time` |  | 否 | text / None | 最近物理 Up 时间，原文 - 表示未记录 |
| `last_physical_down_time` |  | 否 | text / None | 最近物理 Down 时间，原文 - 表示未记录 |
| `negotiation` |  | 否 | state / None | 接口自协商状态原文 |
| `port_mode` |  | 否 | state / None | 接口介质工作模式原文 |
| `loopback` |  | 否 | state / None | 接口环回模式原文 |


### ARP 地址绑定（`network.arp_table`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

保留 VRF/IP 实体键和 MAC；不完整 ARP 条目的 MAC 不可补零。

实体键：`vrf + ip_address`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `mac_address` |  | 是 | state / — | MAC address currently bound to one IP address in an ARP table. |
| `interface` | 是 | 否 | text / — | ARP 邻接出口接口。 |
| `age` | 是 | 否 | text / — | ARP 老化时间原文；IOS 分钟、NX-OS 时分秒及 - 不作无单位数值比较。 |
| `protocol` | 是 | 否 | text / — | 地址协议原文，例如 Internet。 |
| `entry_type` | 是 | 否 | text / — | 协议封装或条目类型原文，例如 ARPA；不能据此直接判定动态/静态。 |

NTC 参考：`cisco_ios_show_ip_arp.textfsm`、`cisco_nxos_show_ip_arp.textfsm`。

### MAC 转发表（`network.mac_table`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

保留 VLAN/MAC 主键。NTC DESTINATION_ADDRESS/MAC_ADDRESS 参考原有 mac_address；CPU、Router 等逻辑出口不应删除。

实体键：`vlan_id + mac_address`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `interface` |  | 是 | state / — | Interface currently associated with one VLAN and MAC address. |
| `entry_type` | 是 | 否 | text / — | MAC 条目类型原文，例如 dynamic/static。 |
| `age` | 是 | 否 | text / — | MAC 老化时间原文，- 为未提供而非零。 |
| `secure` | 是 | 否 | text / — | 安全 MAC 标记原文。 |
| `notify` | 是 | 否 | text / — | 通知标记原文。 |
| `ports` | 是 | 否 | text / — | 有序端口列表；多个出口不得仅保留第一个。 |

NTC 参考：`cisco_ios_show_mac-address-table.textfsm`、`cisco_nxos_show_mac_address-table.textfsm`。

### VRF 清单（`network.vrf_inventory`）

状态：已发布。

补齐原有 network.vrf.status 的 canonical_field=status。当前七份材料没有 show vrf 输出，字段仅完成模板参考，尚无本地样本验收。

实体键：`vrf`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `status` |  | 是 | state / — | Presence or operating status of one virtual routing and forwarding instance. |
| `vrf_id` | 是 | 否 | text / — | 设备本地 VRF ID，保留原文。 |
| `route_distinguisher` | 是 | 否 | text / — | 默认 RD 原文，<not set> 为未配置。 |
| `vpn_id` | 是 | 否 | text / — | VPN 标识原文。 |
| `max_routes` | 是 | 否 | text / — | 最大路由限制原文，保留无限制等非数值状态。 |

NTC 参考：`cisco_nxos_show_vrf_detail.textfsm`、`cisco_ios_show_vrf_detail.textfsm`。

### M-LAG / vPC 域状态（`redundancy.mlag`）

状态：已发布。

一行一个域，保留 domain_id/peer_status。NTC show vpc 只解析成员 ID/PORT/STATUS，不覆盖域健康；成员列表单独建项，不覆盖域状态。

实体键：`domain_id`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `peer_status` |  | 是 | state / — | Current operating state of one multi-chassis link aggregation domain. |
| `peer_keepalive_status` | 是 | 否 | text / — | 对端 keepalive 状态，与 peer adjacency 分开。 |
| `configuration_consistency` | 是 | 否 | text / — | 全局配置一致性状态原文。 |
| `per_vlan_consistency` | 是 | 否 | text / — | 按 VLAN 配置一致性状态原文。 |
| `type2_consistency` | 是 | 否 | text / — | Type-2 一致性状态原文。 |
| `role` | 是 | 否 | text / — | 本机 vPC/M-LAG 角色原文。 |
| `peer_gateway` | 是 | 否 | text / — | peer-gateway 配置/运行状态原文。 |
| `dual_active_excluded_vlans` | 是 | 否 | text / — | 双活排除 VLAN 原文，保留范围表达式。 |
| `graceful_consistency_check` | 是 | 否 | text / — | graceful consistency-check 状态原文。 |
| `auto_recovery_status` | 是 | 否 | text / — | 自动恢复状态原文。 |
| `delay_restore_status` | 是 | 否 | text / — | delay-restore 状态原文。 |

NTC 参考：`cisco_nxos_show_vpc.textfsm`。

### 堆叠成员与端口状态（`redundancy.stack`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

SWITCH/STATE 对应已有 member_id/member_status，不能因 NTC 列名是 VERSION 就当作 OS 版本。

实体键：`member_id`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `member_status` |  | 是 | state / — | Current membership and operating state of one stack or IRF member. |
| `role` | 是 | 否 | text / — | 堆叠成员角色；主控不等于故障。 |
| `mac_address` | 是 | 否 | text / — | 成员基础 MAC 地址。 |
| `priority` | 是 | 否 | gauge / — | 主控选举优先级，非健康分数。 |
| `hardware_version` | 是 | 否 | text / — | show switch detail 的 H/W Version，不是软件版本。 |
| `stack_port1_status` | 是 | 否 | text / — | Stack Port 1 状态。 |
| `stack_port2_status` | 是 | 否 | text / — | Stack Port 2 状态。 |
| `stack_port1_neighbor` | 是 | 否 | text / — | Stack Port 1 邻接成员，None 原文保留。 |
| `stack_port2_neighbor` | 是 | 否 | text / — | Stack Port 2 邻接成员，None 原文保留。 |

NTC 参考：`cisco_ios_show_switch_detail.textfsm`、`cisco_ios_show_switch_detail_stack_ports.textfsm`。

### VRRP 主备状态（`redundancy.vrrp`）

状态：已发布；旧运行包已删除，需重新验证并组装运行包。

保持 interface/vrid 主键与 state。样本命令为空不代表已验证 VRRP 状态；未配置与采集失败应分开处理。

实体键：`interface + vrid`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `state` |  | 是 | state / — | Current operational role of one VRRP instance. |
| `address_family` | 是 | 否 | text / — | IPv4/IPv6 地址族；未来同接口组号双栈时需复核实体键。 |
| `priority` | 是 | 否 | gauge / — | VRRP 选举优先级。 |
| `master_ip` | 是 | 否 | text / — | 当前 Master 地址原文，不是虚拟地址。 |
| `virtual_ip` | 是 | 否 | text / — | VRRP 虚拟网关地址。 |
| `preempt` | 是 | 否 | text / — | 抢占状态原文。 |
| `owner` | 是 | 否 | text / — | 虚拟地址所有者标志原文。 |
| `timer` | 是 | 否 | text / — | VRRP brief 的时间列原文，不猜测秒/毫秒。 |

NTC 参考：`cisco_ios_show_vrrp_brief.textfsm`。

### CPU 使用率（`system.cpu_usage`）

状态：已发布。

保留已有5s/1m/5m 字段及设备阈值策略。NX-OS show system resources 未给五分钟使用率时，不用瞬时值冒充。

实体键：`slot + cpu`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `cpu_5s_percent` |  | 否 | gauge / percent | 明确标注 last 5 seconds 的 CPU 使用率 |
| `cpu_1m_percent` |  | 否 | gauge / percent | 明确标注 last 1 minute 的 CPU 使用率 |
| `cpu_usage_percent` |  | 是 | gauge / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `cpu_high_warning_percent` |  | 否 | threshold / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `cpu_high_alarm_percent` |  | 否 | threshold / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `cpu_interrupt_5s_percent` | 是 | 否 | gauge / percent | 五秒 CPU 使用率中的中断部分，不应再次加到总 CPU 使用率上。 |
| `cpu_user_percent` | 是 | 否 | gauge / percent | NX-OS 当前 user 状态百分比，与五分钟平均值不同。 |
| `cpu_kernel_percent` | 是 | 否 | gauge / percent | NX-OS 当前 kernel 状态百分比。 |
| `cpu_idle_percent` | 是 | 否 | gauge / percent | NX-OS 当前 idle 百分比，不能直接写入五分钟 cpu_usage_percent。 |

NTC 参考：`cisco_ios_show_processes_cpu.textfsm`。

### 内存使用率（`system.memory_usage`）

状态：已发布。

保留既有 KB 和百分比值。容量计算得到的比例是派生指标，不能伪造设备明确输出的五分钟平均值；同一 slot 多池应在适配器验收时分开实体。

实体键：`slot`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `memory_total_kb` |  | 是 | gauge / kb | Mem 行 Total，原文 KB 容量 |
| `memory_used_kb` |  | 是 | gauge / kb | Mem 行 Used，原文 KB 容量 |
| `memory_free_kb` |  | 否 | gauge / kb | Mem 行 Free，原文 KB 容量 |
| `memory_shared_kb` |  | 否 | gauge / kb | Mem 行 Shared，原文 KB 容量 |
| `memory_buffers_kb` |  | 否 | gauge / kb | Mem 行 Buffers，原文 KB 容量 |
| `memory_cached_kb` |  | 否 | gauge / kb | Mem 行 Cached，原文 KB 容量 |
| `memory_free_percent` |  | 否 | gauge / percent | 设备直接输出的 FreeRatio；不由容量相除计算 |
| `memory_usage_percent` |  | 否 | gauge / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `memory_high_warning_percent` |  | 否 | threshold / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `memory_high_alarm_percent` |  | 否 | threshold / percent | 设备明确输出的五分钟平均使用率或对应阈值；不能填入瞬时值或容量推算值 |
| `pool` | 是 | 否 | text / — | 内存池名称，例如 Processor/lsmpi_io；多池数据不能相互覆盖。 |
| `memory_total_bytes` | 是 | 否 | gauge / bytes | IOS Processor Pool 原文总字节数；不能直接放入 memory_total_kb。 |
| `memory_used_bytes` | 是 | 否 | gauge / bytes | IOS Processor Pool 已使用字节数。 |
| `memory_free_bytes` | 是 | 否 | gauge / bytes | IOS Processor Pool 空闲字节数。 |
| `memory_status` | 是 | 否 | text / — | NX-OS Current memory status 原文。 |

NTC 参考：`cisco_ios_show_processes_memory_sorted.textfsm`。

### 板卡模块状态（`device.module`）

状态：已发布。

每个 MODULE 一行，与机箱总体版本、inventory 组件记录分开。

实体键：`MODULE`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `PORTS` | 是 | 否 | gauge / count | 板卡端口数量。 |
| `TYPE` | 是 | 否 | text / — | 模块类型完整原文。 |
| `MODEL` | 是 | 是 | text / — | 板卡产品型号。 |
| `STATUS` | 是 | 是 | text / — | 板卡运行状态原文。 |

NTC 参考：`cisco_nxos_show_module.textfsm`。

### 接口端口状态概览（`interface.port_status`）

状态：已发布。

只定义 show interface status 的端口概览。没有 protocol_status 不得填充为 up，也不冒充完整接口统计。

实体键：`PORT`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `NAME` | 是 | 否 | text / — | 端口描述，可为空。 |
| `STATUS` | 是 | 是 | text / — | 端口连接状态原文；connected/notconnect/disabled/err-disabled 分开。 |
| `VLAN_ID` | 是 | 否 | text / — | VLAN 或 trunk/routed 原文，不能强制全部转整数。 |
| `DUPLEX` | 是 | 否 | text / — | 双工状态，保留 auto 前缀。 |
| `SPEED` | 是 | 否 | text / — | 速率原文，保留 auto、单位及未协商状态。 |
| `TYPE` | 是 | 否 | text / — | 端口介质/模块类型原文。 |
| `FC_MODE` | 是 | 否 | text / — | 设备支持时的 Fibre Channel 模式。 |

NTC 参考：`cisco_ios_show_interfaces_status.textfsm`、`cisco_nxos_show_interface_status.textfsm`。

### 设备时钟（`system.clock`）

状态：已发布。

时钟正常走动不生成变化告警；没有 NTP 同步证据时不判断时钟健康。

实体键：`device_id`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `TIME` | 是 | 是 | text / — | 设备时间原文，保留毫秒；不等于采集时间。 |
| `TIMEZONE` | 是 | 否 | text / — | 设备明确给出的时区，不用服务器时区补齐。 |
| `DAYWEEK` | 是 | 否 | text / — | 星期原文。 |
| `MONTH` | 是 | 是 | text / — | 月份原文。 |
| `DAY` | 是 | 是 | gauge / — | 日。 |
| `YEAR` | 是 | 是 | gauge / — | 年。 |
| `CLOCK_MARKER` | 是 | 否 | text / — | IOS 开头 * 或 . 等时钟状态标记；NTC 模板未保留，不推断已同步。 |

NTC 参考：`cisco_ios_show_clock.textfsm`、`cisco_nxos_show_clock.textfsm`。

### CPU 进程资源（`system.cpu_process`）

状态：已发布。

每个 PID 一行，不将系统总体百分比复制成进程百分比。PID 重用及设备重启会使跨次比较失去连续性；本项先保留证据，不自动告警。

实体键：`PROCESS_PID`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `PROCESS_NAME` | 是 | 是 | text / — | 进程名称原文。 |
| `PROCESS_RUNTIME` | 是 | 否 | counter / ms | 进程累计 CPU 运行毫秒数；重启或进程重建后可归零。 |
| `PROCESS_INVOKED` | 是 | 否 | counter / count | 累计调用次数。 |
| `PROCESS_CPU_TIME_FOR_INVOCATION` | 是 | 否 | gauge / us | 原表 uSecs 每次调用 CPU 时间。 |
| `PROCESS_CPU_USAGE_5_SEC` | 是 | 否 | gauge / percent | 进程五秒使用率。 |
| `PROCESS_CPU_USAGE_1_MIN` | 是 | 否 | gauge / percent | 进程一分钟使用率。 |
| `PROCESS_CPU_USAGE_5_MIN` | 是 | 否 | gauge / percent | 进程五分钟使用率。 |
| `PROCESS_TTY` | 是 | 否 | text / — | 关联终端标识。 |

NTC 参考：`cisco_ios_show_processes_cpu.textfsm`。

### 内存进程资源（`system.memory_process`）

状态：已发布。

NTC 用多个 Value List 保存进程表；检查项要求按同一索引展开为进程行，禁止列表笛卡尔积。多个内部进程可能共用 PID 0，因此以 PID 和进程名共同标识。

实体键：`PROCESS_ID`、`PROCESS`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `PROCESS` | 是 | 是 | text / — | 进程名称原文。 |
| `PROCESS_ALLOCATED` | 是 | 否 | counter / bytes | 进程累计分配字节数，不等于当前占用。 |
| `PROCESS_FREED` | 是 | 否 | counter / bytes | 进程累计释放字节数。 |
| `PROCESS_HOLDING` | 是 | 是 | gauge / bytes | 进程当前持有字节数。 |
| `PROCESS_TTY` | 是 | 否 | text / — | 进程关联终端标识。 |
| `GETBUFS` | 是 | 否 | counter / count | Getbufs 累计次数。 |
| `RETBUFS` | 是 | 否 | counter / count | Retbufs 累计次数。 |

NTC 参考：`cisco_ios_show_processes_memory_sorted.textfsm`。

### vPC 成员链路状态（`redundancy.vpc_member`）

状态：已发布。

一行一个 vPC 成员，来自同一域；不使用成员状态替代 peer-link 或整个 vPC 域的健康。

实体键：`ID`。

| Value | 本轮新增 | 必填 | 语义 / 单位 | 含义 |
|---|---|---|---|---|
| `PORT` | 是 | 是 | text / — | vPC 成员对应的 Port-channel。 |
| `STATUS` | 是 | 是 | text / — | 成员运行状态原文。 |
| `CONSISTENCY` | 是 | 否 | text / — | 单个成员配置一致性结果。 |
| `CONSISTENCY_REASON` | 是 | 否 | text / — | 单个成员一致性失败原因。 |
| `ACTIVE_VLANS` | 是 | 否 | text / — | 成员活动 VLAN 列表或范围原文。 |

NTC 参考：`cisco_nxos_show_vpc.textfsm`。

## 可复现操作

`scripts/check_items/audit_cisco_samples.py` 只在本地回放模板，报告计数与字段，不输出完整 CLI。`scripts/check_items/enrich_cisco_values.py preview --output <报告路径>` 预览；`apply` 通过现有目录服务创建草稿，`--publish-ready` 仅发布无治理阻塞的项。字段参考包为 `scripts/check_items/cisco_reference_values.yaml`。

验证：字段/既有版本与接口语义回归 28 项、规则上下文与文档回归 15 项通过。21 项完善后的定义及 1 项恢复后的定义均通过在线规则上下文校验，重复预览为零项待处理。上线接口验证结果保存在 `.runtime/cisco-check-items/live-verification.json`；原目录快照、影响报告和本地 NTC 审计也保存在同一目录。七个旧运行包的备份为 `deleted-runtime-bundles-backup.json`，删除结果为 `bundle-deletion-result.json`；两者均位于该目录。
