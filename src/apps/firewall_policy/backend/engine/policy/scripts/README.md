<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

统一入口：`run.py --params params.json --output-dir output`。

`params.json` 必须提供 `policy_file_url`，可选 `ticket_id`、`topology_file_url`、`merge_policies`（布尔值，默认 true）。
没有上传文件时明确报错，不使用默认测试申请。策略表支持 src_ip、dst_ip、port、proto，旧 action 列只接受空值或 permit；start_time/end_time（有效期，须同时填写）和 long_link（长连接小时数，仅 TCP）目前只为华为（USG6000）和 H3C（F5000）生成，规则见 `policy_window.py`。

处理链路：policy_input（policy_window）→ policy_sets/core → policy_plan → policy_optimizer → vendor_config → vendor_validation → plan_preview。

- 地址与端口采用半开区间集合，不逐个展开。
- 逐维等价合并，独立三维区间差集校验。预算不足保留原始有效计划。
- CLI 回读后与逐设备计划比较，未通过不导出。
- 输出设备配置、原有 manifest、JSON 计划、HTML 预览和可编辑申请表。
- 原有回退实现保持不变。

兼容说明：旧拓扑未填写 intra_connections 时仍沿用域间互连并在预览提示；明确填写空列表时不创建域内连接。
指定路径只有通过节点、连接和起终点校验后才能使用。无关的历史错误路径在预览显示，匹配到错误路径的申请阻断。

山石适配器依据 StoneOS 5.5 官方 CLI 文档修正源/目的地址和服务对象，实际设备版本仍需现场验证：
- [Policy CLI](https://kb.hillstonenet.com/en/wp-content/uploads/2017/11/StoneOS_CLI_User_Guide_Policy_5.5R5-1.pdf)
- [Service CLI](https://kb.hillstonenet.com/en/wp-content/uploads/2017/01/StoneOS_CLI_User_Guide_Firewall_5.5R4.pdf)
