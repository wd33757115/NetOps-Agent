# 网络数字孪生诊断评测集

方案见 [docs/network-twin-plan.md](../../docs/network-twin-plan.md) 第 5 节工作包 B、第 6 节实验网。

每个故障一个目录（`F1-…`、`F2-…`）：

| 文件 | 内容 |
|---|---|
| `inject.cfg` | 注入故障的配置，每次只改一处 |
| `restore.cfg` | 恢复用的配置 |
| `symptom.txt` | 交给助手的用户原话 |
| `expected.json` | 孪生层预期（路径在哪台设备、以什么状态结束）；AI 层标准答案（故障设备、对象、原因类别） |

跑法：注入 → 平台重新发现 → 调路径工具记结果 → 把症状交给助手记回答 → 恢复 → 再发现确认恢复。

评分：孪生层每例 0/1；AI 层按"设备对、原因对、证据对、没有编造"各 0/1。每次跑完把分数记进方案第 8 节。

用例已建（2026-11-14，工作包 C 完成后）：F1–F10 十个目录。F6/F7 在 eNSP 的 PC 上操作（人工），F8 需先给华为映射补 acl-* 表（当时设备都没配 ACL，映射未含）。

孪生层自动跑法：`.runtime/ensp/eval_run.py`（不入库）——`baseline` 采健康网存参照；`run F1 F4 …` 对每个用例注入 → 同一采集流水线采集 → 路径引擎 → 按该用例 `expected.json` 的 checks 打分（path 断言 + 记录断言）→ 恢复并确认回到基线。结果记在 `.runtime/ensp/eval-run/`，分数抄进方案第 8 节。注入与恢复都执行 `save`（2026-11-14 的教训）。

expected.json 的 status 取值与路径引擎一致：`delivered`、`no_route`、`endpoint_unresolved`、`denied_in`、`denied_out` 等。基线（2026-11-14）：client→server 双向 endpoint_unresolved@USG（MAC 表空，端点归属未证明）；client→internet 去程 delivered@AR、回程 endpoint_unresolved@USG；server→internet 去程 delivered@AR、回程 no_route@AR。
