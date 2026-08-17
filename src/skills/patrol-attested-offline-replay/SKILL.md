---
name: patrol-attested-offline-replay
description: Replay independently attested offline-current CLI evidence into a separate production PatrolRun using the frozen CheckItem RuntimeBundle.
entry_script: scripts/run.py
entry_output: none
enabled: true
rollout_status: stable
enabled_ratio: 100
input_schema:
  type: object
  required:
    - execution_plan
    - execution_plan_id
    - workflow_run_id
  properties:
    execution_plan:
      type: object
      description: Immutable device execution plan created from the approved current-authority request.
    execution_plan_id:
      type: string
    workflow_run_id:
      type: string
output_schema:
  type: object
  properties:
    success:
      type: boolean
    snapshot_run_id:
      type: string
    runtime_asset_manifest:
      type: object
    source_accounting:
      type: object
---

# patrol-attested-offline-replay

This is a production ingestion adapter, not a rule-generation Skill. It reads
only the immutable Snapshot set frozen by an independently approved
`PatrolCurrentAuthorityRequest`, verifies every raw hash and identity binding,
then creates a separate production `PatrolRun` using the exact RuntimeBundle
frozen for the candidate batch.

The source offline job and its original PatrolRun are never updated. Missing
commands are persisted as explicit collection failures so downstream
Assessment and reports can say `unable_to_determine` instead of silently
claiming health.
