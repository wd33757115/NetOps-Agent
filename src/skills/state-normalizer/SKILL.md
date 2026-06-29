---
name: state-normalizer
description: Convert parsed patrol command snapshots into versioned StateObservation records and production CurrentState.
entry_script: scripts/run.py
entry_output: none
input_schema:
  type: object
  properties:
    snapshot_id:
      type: string
      description: Normalize one command snapshot.
    run_id:
      type: string
      description: Normalize all parsed snapshots in a patrol run.
    update_current_state:
      type: boolean
      description: Override CurrentState writes. Defaults to true only for production runs.
  oneOf:
    - required: [snapshot_id]
    - required: [run_id]
output_schema:
  type: object
  properties:
    success:
      type: boolean
    observation_count:
      type: integer
    created_count:
      type: integer
    current_state_updates:
      type: integer
---

# state-normalizer

`state-normalizer` is the bridge from parser output to platform state facts.

It reads PostgreSQL `netops_command_snapshots.structured_json`, applies the parser
template data contract (`entity_type`, `primary_keys`, `ignore_fields`), writes
immutable field-level `netops_state_observations`, and updates
`netops_current_state` only for production runs.

`parser_validation` snapshots are allowed to generate observations for validation,
but they are not allowed to update production current state.
