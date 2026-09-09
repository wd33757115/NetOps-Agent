---
name: firewall-policy-generator
version: 1.1.0
description: 从策略申请表生成可核对的逐设备放行计划与配置，支持精确合并和等价校验
category: security
tags:
- firewall
- policy
- configuration
- network
- security
author: NetOps Team
domain: security
celery_queue: netops.firewall
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: dir
triggers:
- 生成防火墙策略
- 防火墙配置
- 创建策略
- policy file
celery_task: execute_firewall_policy_task
execution_mode: async
inputs:
- name: ticket_id
  type: string
  required: false
  description: 工单号，如 TICKET_001
- name: ticket_title
  type: string
  required: false
  description: 工单标题
- name: policy_file_url
  type: string
  required: true
  description: 必须提供策略申请表 Excel 路径或 URL，不使用默认测试表
- name: merge_policies
  type: boolean
  required: false
  description: 是否进行等价合并，默认 true；用户说明不要合并时传 false
- name: topology_file_url
  type: string
  required: false
  description: 拓扑文件路径或URL，可选
- name: include_business_paths
  type: boolean
  required: false
  description: 默认 false；用户要求业务系统访问关系和拓扑路径说明时设为 true，页面和完整预览按申请行展示
- name: requester
  type: string
  required: false
  description: 申请人
- name: assignee
  type: string
  required: false
  description: 处理人
outputs:
- name: config_files
  type: download
  description: 多厂商防火墙配置文件压缩包
- name: config_file_key
  type: string
  description: MinIO 对象键（供下游 Skill 使用）
- name: manifest
  type: object
  description: 设备/脚本/回退结构化摘要（manifest.json）
references:
- type: rag
  source: firewall-policy-guide
  description: 防火墙策略编写指南
- type: file
  path: scripts/run.py
  description: Skill 标准入口（参数、生成、结果校验）
- type: file
  path: scripts/firewall-policy.py
  description: 策略计划、等价合并与厂商配置生成主程序
enabled: true
fallback_to_rag: true
---

# 防火墙放行策略生成

只生成申请需要的 permit 放行策略。设备默认拒绝机制保持原状，不追加拒绝规则。
现有回退流程与相关清单保持原实现，不新增备份或恢复步骤。

## 输入

必须上传策略申请表，包含 src_ip、dst_ip、port、proto 四列，也支持中文表头。
旧表可带 action 列，空值或 permit 接受，deny 拒绝。表头可位于任意行。
支持单 IPv4、CIDR、连续地址范围及多个地址；支持 tcp、udp、tcp/udp、icmp。
TCP/UDP 必须填写端口，ICMP 端口留空。当前不支持有效期及长连接设置，已填写时明确报错。
没有文件、文件错误、拓扑缺失、地址归属冲突或生成失败时返回错误，不能使用样例替代或声称完成。

## 执行与人工干预

统一入口为 scripts/run.py，由平台执行器调用；业务流程位于 scripts/firewall-policy.py。
按照申请表 → 归属分段 → 拓扑路径 → 逐设备计划 → 等价合并 → 厂商命令 → CLI 回读校验执行。
地址和端口按区间计算，不展开主机或逐个端口。优化超过预算或等价校验不通过时保留原始有效规则。
用户说明“不要合并”“不合并”“关闭合并”时，设置 merge_policies=false。
用户要求展示业务系统之间的访问关系和拓扑路径时，设置 include_business_paths=true；默认关闭。
可选列“源业务系统”“目的业务系统”只作为申请说明保留，不参与地址归属或策略合并。名称缺失显示“未填写”，不推测。
说明按申请行及地址归属分段关联最终设备规则，不能把合并规则中的业务名称交叉组合，也不能声称设备已经开通或实测连通。
用户可下载并编辑“策略申请表.xlsx”后重新上传生成，人工修改后重新执行完整验证。

## 输出

- 配置 ZIP：设备 CLI、policy_plan.json、策略计划预览.html、策略申请表.xlsx、原有 manifest.json。
- 单独的策略计划预览链接：申请行号、源/目的地址、安全域、匹配前缀、路径、最终 CLI。
- 可编辑申请表下载链接。
- policy_plan_id 与 optimization：标识本次计划及合并校验结果。

当前适配器包括华为、H3C、H3CF1000、天融信和山石。CLI 回读校验只证明生成文本与计划中匹配条件一致，不能代替目标设备版本验证。
生成流程不调用外部 AI、不下发设备配置、不改变回退行为。
