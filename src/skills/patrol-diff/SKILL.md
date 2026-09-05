---
version: 1.0.0
category: general
triggers: []
name: patrol-diff
description: Generate human-facing patrol differences from historical runs without coupling report diff to system NetworkChange production.
input_schema:
  type: object
  properties:
    previous_run_id:
      type: string
    current_run_id:
      type: string
    current_run_id_only:
      type: string
    use_state_observations:
      type: boolean
    persist:
      type: boolean
      description: Defaults to false. Set true only when intentionally producing NetworkChange records.
output_schema:
  type: object
  properties:
    success:
      type: boolean
    change_count:
      type: integer
---

# patrol-diff

`patrol-diff` is for human report comparison. It reuses the deterministic diff
engine but defaults to `persist=false`, so disabling report generation or
rerunning reports does not mutate the operational Change/Event data plane.

## 核心能力

为历史巡检生成面向阅读的差异结果，复用确定性比较逻辑。

## 工作流程

读取前后 run ID，调用 build_human_diff；current_run_id_only 兼容映射到 current_run_id。

## 输出格式

stdout JSON 返回 success、差异和统计，具体内容来自已保存事实。

## 示例

用 previous_run_id 与 current_run_id 比较两个历史周期，保持 persist=false。

## 注意事项

默认不写 NetworkChange；只有明确需要生成操作事实时才使用 persist=true，不以重绘报告为由写入生产变化。
