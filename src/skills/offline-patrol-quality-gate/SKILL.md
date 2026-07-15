---
name: offline-patrol-quality-gate
version: 1.0.0
description: Revalidate an offline historical patrol job immediately before production analysis.
category: network
domain: network
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
entry_script: scripts/run.py
entry_output: none
inputs:
  - name: offline_job_id
    type: string
    required: true
outputs:
  - name: blockers
    type: object
enabled: true
fallback_to_rag: false
---

# Offline Patrol Quality Gate

Rechecks source, parser, identity, period ordering, and device matching invariants
inside the Workflow. A failed gate stops the historical comparison before any
production Change or Event is generated.
