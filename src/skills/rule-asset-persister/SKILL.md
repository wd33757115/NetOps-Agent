---
name: rule-asset-persister
description: Persist generated ObservationContract, ChangePolicy and EventRule candidates as draft rule assets and pending approval jobs.
version: 0.1.0
category: parser
runtime: python
entrypoint: scripts/run.py
entry_script: scripts/run.py
entry_output: none
inputs:
  - name: source_ref
    type: string
    required: false
    description: Workflow, chat or dataset source reference.
  - name: schema_id
    type: string
    required: false
    description: Stable source schema identity. Parser assets use the Parser Template ID.
  - name: schema_ref
    type: string
    required: false
    description: Human-readable source reference, such as parser_template:<template_id>.
  - name: source_template_id
    type: string
    required: false
    description: Parser Template ID that owns the generated ObservationContract.
  - name: contract_result
    type: object
    required: false
    description: observation-contract-generator result.
  - name: change_policy_result
    type: object
    required: false
    description: change-policy-generator result.
  - name: event_rule_result
    type: object
    required: false
    description: event-rule-generator result.
  - name: event_rule_validation
    type: object
    required: false
    description: event-rule-validator result for all candidate rules.
outputs:
  - name: success
    type: boolean
  - name: assets
    type: array
  - name: jobs
    type: array
---

This Skill does not generate or activate assets. It only persists validated candidates
as draft versions and moves their RuleGenerationJob to `pending_approval` when the
corresponding deterministic validation succeeded.
