# 巡检容量基线（估算）

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文中的估算仅作历史参考，不代表当前容量或生产承诺。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

基于 `scripts/test/patrol_capacity_benchmark.py` 的串行 SSH 估算模型：

假设：每台设备平均 5 条命令，每条命令平均 3 秒，目标窗口 60 分钟，4 个 patrol worker。

| 设备规模 | 总设备秒 | 建议 `PATROL_MAX_CONCURRENCY` | 每 Worker 并发 |
|---------|---------|-------------------------------|----------------|
| 1000 | 15000 | 5 | 2 |
| 2000 | 30000 | 9 | 3 |

建议：

1. 生产默认保持 `PATROL_CONCURRENCY_FAIL_CLOSED=true`。
2. 若设备 RTT 明显高于 3 秒/命令，按比例上调并发或放宽窗口。
3. 连接池 / Redis / DB 连接数至少覆盖 `PATROL_MAX_CONCURRENCY × worker_count`。
4. 先用 100 台冒烟，再按上表扩到 1000/2000。

复算：

```bash
py -3 scripts/test/patrol_capacity_benchmark.py --devices 1000
py -3 scripts/test/patrol_capacity_benchmark.py --devices 2000 --workers 4
```
