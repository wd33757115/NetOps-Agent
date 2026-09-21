# 防火墙策略与变更工单

先确认申请表和拓扑来源。通过 `/api/apps/firewall-policy/uploads` 上传文件，使用返回的 upload_id 调用 `firewall_policy__submit_policy`，使用固定 request_id 防止重复提交。

轮询 `firewall_policy__read_run`，完成后展示策略明细、拓扑证据和应用授权下载入口。所有设备命令仅生成供用户核对，本能力不执行设备下发。

如需变更工单，对完成的策略 run 调用 `firewall_policy__submit_ticket`，只填写工单头信息；不得传入或改写设备命令。读取子任务获取工单文件。不要使用旧 test-run、聊天上传路径或签名对象 URL。
