# Patrol Degradation and Parser Asset Closure

## Goal

Separate command execution, parsing, fact readiness, and normalization semantics,
then close the governed production loop from a missing Parser template to LLM-generated,
validated, replayed, approved, and reversible Parser and rule assets.

The authoritative implementation requirements are:

- A missing template never blocks CLI collection or discards Raw Evidence.
- Production raw CLI is never copied into an LLM message or rule-generation job.
- LLM output remains a candidate until validation, replay, and approval complete.
- Runtime resolvers consume active assets only.
- Manual candidate, activation, deprecation, and rollback interfaces remain available.
- Creation, retry, activation, and backfill are idempotent and auditable.

## Delivery Stages

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Freeze the command status matrix and quality metrics | Implemented |
| 1 | Persist execution, parse, fact, and normalization dimensions | Implemented |
| 2 | Gate Normalizer inputs and classify every zero-observation result | Implemented |
| 3 | Create a safe, deduplicated production Parser backlog | Implemented |
| 4 | Govern LLM Parser generation, validation, replay, and activation | Implemented, generation gated |
| 5 | Seed governed LLM rule assets after template activation | Implemented, auto-seed gated |
| 6 | Add explicit dry-run historical backfill | Implemented, commit gated |
| 7 | Expose evidence, quality metrics, actions, and alerts | Implemented |

## Status Contract

`PatrolCollectionResult.status` remains a deprecated compatibility projection.
New code must use these independent dimensions:

```text
execution_status: complete | failed | timeout | cancelled
parse_status: not_attempted | parsed | parse_empty | ignored |
              template_missing | parse_failed | identity_unknown
fact_status: ready | empty | ignored | blocked_by_collection |
             blocked_by_parser | blocked_by_identity |
             blocked_by_contract | normalization_failed
normalization_status: pending | normalized | empty | skipped | blocked | failed
```

Baseline and deletion authority require a complete execution and a `ready` or `empty`
fact result. Parser and normalization failures can never acquire that authority.

## Completion Gate

Completion requires evidence for the full production flow:

```text
production patrol
-> missing-template backlog
-> sanitization
-> LLM candidate generation
-> validation and historical replay
-> approval and activation
-> rule-asset generation
-> next patrol parsed
-> Observation -> Change -> Event
```

It also requires migration validation, unit and integration tests, failure injection,
frontend production build, schema drift checks, and a rollback rehearsal.

## Implementation Map

- Status dimensions and validation: `src/core/patrol/statuses.py`, patrol importer,
  runner, repository, Normalizer, and migration `0032`.
- Production backlog and sanitization: Parser repository/tasks, deterministic sample
  sanitizer, Celery retry schedule, and migration `0033`.
- LLM Parser governance: sanitized production input only, immutable candidate assets,
  full-sample regression validation, pending approval, transactional activation,
  durable outbox, explicit force activation, deprecation, and rollback.
- LLM rule assets: template activation seeds an idempotent ObservationContract job;
  the workflow generates ObservationContract, ChangePolicy, and EventRule candidates
  in dependency order. Business policy intent is supplied by the LLM/operator context,
  not hard-coded optical or interface rules. Runtime reads active assets only; the
  existing manual/built-in provider interface remains available.
- Historical repair: migration `0034` and `ParserBackfillService` provide dry-run,
  isolated preview, approval, deterministic commit, lease fencing, historical event
  namespace, and notification suppression.
- Operations: batch reports and evidence timeline expose all status dimensions and
  asset provenance. Parser Control shows backlog, sanitization, backfill actions, and
  configured SLA/ratio/collision alerts.

## Release Controls

Risky automation remains intentionally disabled until an operator completes canary
validation:

```text
PRODUCTION_PARSER_BACKLOG_ENABLED=true
PARSER_LLM_GENERATION_ENABLED=false
RULE_ASSET_AUTO_SEED_ENABLED=true
PARSER_BACKFILL_ENABLED=false
```

Recommended rollout is backlog observation, then LLM generation canary, then rule
asset auto-seeding, and finally individually approved historical backfills. Disabling
any automation flag does not disable CLI collection or Raw Evidence persistence.
The four-dimensional status schema is additive and always written; the deprecated
`status` projection is retained as its compatibility and rollback boundary.

## Verification Evidence

- Empty PostgreSQL database upgraded through migrations `0001` to `0035`; `alembic
  check` reported no schema drift.
- All 553 unit tests pass, including legacy CPU, memory, environmental, power, fan,
  parser-validation, rule, report, and API paths.
- Eleven real PostgreSQL/Redis fault-injection tests pass, including concurrent
  production candidate deduplication and single-owner outbox publication.
- Backfill tests prove dry-run isolation, current-state protection, no notification
  outbox, active-lease fencing, expired-lease recovery, and deterministic replay.
- Parser rollback rehearsal proves that the previous template and its ObservationContract
  become the sole active version and that the action is audited as `rollback`.
- Ruff checks, Python bytecode compilation, and the frontend production build pass.

Output classification and capability-scoped template reuse are documented in
`docs/patrol-output-capability-and-raw-handling-2026-07-12.md`. Migration `0035`
persists output status, while raw configuration handling emits hash-only facts.

## Operational Acceptance

The implementation is complete, but automatic LLM generation and historical commit
are not silently enabled. Production acceptance still requires real provider credentials,
representative sanitized samples, an operator-approved canary, and observation of the
configured alerts. Those are deployment actions, not missing code paths.
