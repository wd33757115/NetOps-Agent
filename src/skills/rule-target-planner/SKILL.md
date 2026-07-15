---
name: rule-target-planner
description: Compile a validated ObservationContract and sanitized records into deterministic multi-field ChangePolicy targets.
version: 0.1.0
enabled: true
rollout_status: stable
enabled_ratio: 100
entry_script: scripts/run.py
entry_output: none
---

# Rule Target Planner

This skill is the deterministic boundary between semantic Contract generation
and Policy generation. It contains no vendor, model, command, field-name,
threshold, or event-name branches.

It reads a validated `contract` and sanitized `records`, then emits
`policy_targets`, `historical_values_by_field`, field profiles, and explicit
blockers. It never reads raw CLI and never activates an asset.
