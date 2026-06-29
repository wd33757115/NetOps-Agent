---
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
