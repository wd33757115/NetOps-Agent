import { inspectionBatchApi, inspectionDeviceApi } from '../../../../src/apps/inspection/frontend/api'
// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { api, getChatWebSocketUrl, type AuthSession, API_BASE_URL } from '../config/api'

export interface ChatRequest {
  query: string
  thread_id?: string
  /** Netdisk file ids from the assistant's attachment upload. */
  attachments?: string[]
}

export interface ChatResponse {
  response: string
  thread_id: string
  agent_type?: string
  celery_task_id?: string
  download_url?: string
  references?: any[]
}

export type ServiceCheckStatus = 'ok' | 'degraded' | 'down' | 'skipped'

export interface ServiceCheck {
  id: string
  name: string
  status: ServiceCheckStatus
  message: string
  latency_ms?: number | null
  detail?: Record<string, unknown> | null
}

export interface DiagnosticsResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  timestamp: string
  summary: string
  checks: ServiceCheck[]
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  agent_type?: string
  celery_task_id?: string
  download_url?: string
  references?: any[]
  created_at: string
}

export interface Conversation {
  id: string
  title: string
  user_id?: string
  thread_id?: string
  status: string
  summary?: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationDetail {
  conversation: Conversation
  messages: Message[]
}

export interface SkillItem {
  name: string
  description: string
  category: string
  tags: string[]
  enabled: boolean
  version?: string
  fallback_to_rag?: boolean
  domain?: string
  celery_queue?: string | null
  rollout_status?: SkillRolloutStatus
  enabled_ratio?: number
  min_platform_version?: string | null
  min_permission_level?: string
  catalog_indexed?: boolean
}

export interface ParserTemplateItem {
  id: string
  vendor: string
  model?: string
  family?: string
  profile: string
  canonical_command: string
  category: string
  version: string
  status: string
  source_job_id?: string
  validation_json?: Record<string, unknown>
  created_at: string
}

export interface ParserTemplatePreview {
  id: string
  vendor: string
  profile: string
  canonical_command: string
  category: string
  version: string
  status: string
  sha256: string
  hash_valid: boolean
  content: string
  validation: Record<string, unknown>
}

export interface ParserTemplateComparison {
  template_id: string
  active_template_id?: string
  sample_count: number
  passed_samples: number
  candidate_contract_sha256?: string
  active_contract_sha256?: string
  contract_changed: boolean
  candidate_template: ParserTemplatePreview
  candidate_contract: Record<string, unknown>
  active_contract: Record<string, unknown>
  parser_field_contract?: {
    schema_version: 'parser-field-contract/v1'
    status: 'aligned' | 'schema_drift' | 'parser_spec_missing' | 'template_invalid'
    aligned: boolean
    parser_spec_record_sets: Record<string, string[]>
    template_record_sets: Record<string, string[]>
    differences: Array<{
      record_set: string
      parser_spec_present: boolean
      template_present: boolean
      missing_in_template: string[]
      undeclared_in_parser_spec: string[]
    }>
    errors: string[]
  }
  parser_output_profile?: Record<string, unknown>
  capability_claim?: string
  check_item_capabilities?: Array<{
    schema_version: string
    check_item_id: string
    check_item_version?: number
    status: string
    shape_assessment?: {
      status: string
      observed_roles: string[]
      missing_required_roles: string[]
    }
    target?: {
      name?: string
      required_semantic_roles?: string[]
      required_observation_types?: string[]
    } | null
    canonical_mapping?: {
      status: string
      mapped_observation_types: string[]
      missing_required_observation_types: string[]
    }
    blockers?: Array<{
      code: string
      message: string
      context?: Record<string, unknown>
    }>
    claim_boundary?: {
      parser_structure_proven?: boolean
      canonical_mapping_proven?: boolean
      check_item_runtime_ready?: boolean
      statement?: string
    }
  }>
  review_readiness?: {
    can_review: boolean
    blockers: string[]
  }
  semantic_review?: {
    schema_version: string
    status: 'accepted'
    review_mode: 'human_after_ai' | 'human_direct'
    reviewed_by: string
    reviewed_at: string
    reason: string
    result_confirmed: boolean
    primary_key_confirmed: boolean
    evidence_digest: string
  } | null
  release_actor?: {
    username: string
    role: string
    same_as_semantic_reviewer: boolean
    admin_override_applies: boolean
    can_activate: boolean
    blockers: string[]
  }
  activation_readiness?: {
    can_activate: boolean
    blockers: string[]
  }
  approval_readiness: {
    can_approve: boolean
    blockers: string[]
  }
  rows: Array<{
    snapshot_id: string
    dataset_id: string
    device_id: string
    command: string
    raw_text: string
    raw_text_sha256: string
    raw_text_redacted?: boolean
    evidence_kind?: string
    sample_role?: string
    changed: boolean
    current?: ParserComparisonParseResult
    proposed: ParserComparisonParseResult
  }>
}

export interface ParserComparisonRecordSet {
  name: string
  entity_type?: string
  primary_keys: string[]
  required_fields?: string[]
  records: Array<Record<string, unknown>>
}

export interface ParserComparisonParseResult {
  status: string
  failure_code?: string
  records?: unknown
  record_count?: number
  record_sets?: ParserComparisonRecordSet[]
}

export interface ParserDatasetCoverage {
  dataset_id: string
  snapshot_count: number
  device_count: number
  command_count: number
  eligible_count: number
  parsed: number
  empty: number
  ignored: number
  missing: number
  failed: number
  identity_unknown: number
  structured_coverage_rate: number
  parse_success_rate: number
  legal_empty_rate: number
  missing_template_rate: number
  parse_failure_rate: number
  primary_key_stability_rate: number | null
  primary_key_evaluated_count?: number
  first_imported_at?: string
  last_imported_at?: string
  scope_type?: 'dataset' | 'offline_patrol_run'
  patrol_run_id?: string | null
  offline_job_id?: string | null
  job_purpose?: 'offline_patrol' | 'rule_asset_source' | null
  period_role?: string | null
  period_status?: string | null
  observed_at?: string | null
}

export interface ParserImportPreview {
  success: boolean
  preview: boolean
  dataset_id: string
  source_path: string
  file_count: number
  device_count: number
  command_count: number
  unique_commands: number
  ready: number
  template_missing: number
  parse_failed: number
  ignored: number
  raw_compare?: number
  identity_unknown: number
  duplicate_snapshots: number
  new_snapshots: number
}

export interface ParserGenerationProgress {
  generation_run_id?: string
  stage?: string
  attempt_number?: number
  started_at?: string
  heartbeat_at?: string
  deadline_at?: string
  request_deadline_at?: string
  elapsed_ms?: number
  cancellable?: boolean
  completed_at?: string
  terminal_reason?: string
  details?: {
    model_call_id?: string
    model_calls_used?: number
    model_call_limit?: number
    candidate_sha256?: string
    diagnostic_code?: string
    sample_count?: number
    [key: string]: unknown
  }
}

export interface ParserJobItem {
  id: string
  job_type: string
  vendor: string
  model?: string
  profile: string
  canonical_command: string
  status: string
  source_mode: 'parser_validation' | 'production'
  pipeline_version?: 'legacy' | 'simple_v2'
  parser_spec_status?: 'missing' | 'draft' | 'confirmed'
  parser_spec_json?: Record<string, unknown>
  parser_spec_sha256?: string
  parser_spec_revision?: number
  parser_spec_revisions?: Array<{
    id: string
    revision_number: number
    parser_spec_status: 'draft' | 'confirmed'
    parser_spec_json: Record<string, unknown>
    parser_spec_sha256: string
    previous_parser_spec_sha256?: string
    source: 'engineer' | 'llm_suggestion'
    actor: string
    reason: string
    invalidated_template_ids_json: string[]
    created_at: string
  }>
  sample_count: number
  structure_variant_count?: number
  structure_signatures?: string[]
  compatibility_scope?: 'model' | 'family'
  generated_candidate_count?: number
  blocking_candidate_count?: number
  can_retry?: boolean
  attempts: number
  contract_revision_count?: number
  template_attempt_count?: number
  model_calls_used?: number
  best_attempt_id?: string
  agent_state_json?: Record<string, unknown> & {
    generation_progress?: ParserGenerationProgress
    stop_reason?: string
  }
  agent_budget_json?: Record<string, unknown>
  failure_code?: string
  last_error?: string
  celery_task_id?: string
  dispatched_at?: string
  started_at?: string
  completed_at?: string
  created_at: string
  updated_at: string
  samples?: ParserJobSample[]
  attempt_summary?: {
    total: number
    best_attempt_id?: string
    contract_revisions: number
    template_attempts: number
  }
  generation_attempts?: ParserGenerationAttempt[]
  model_call_ledger?: ParserModelCallLedger[]
  generation_readiness?: {
    check_item_binding?: { check_item_id: string; version_id: string; digest: string }
    ready: boolean
    blockers: string[]
    warnings?: string[]
    validation_mode?: 'generation_sample_only' | 'independent_acceptance'
    role_counts: Partial<Record<'generation' | 'acceptance' | 'negative' | 'unassigned', number>>
  }
}

export interface ParserModelCallLedger {
  id: string
  job_id: string
  call_number: number
  generation_run_id: string
  status: string
  model_provider?: string
  model_name?: string
  protocol_version?: string
  candidate_sha256?: string
  failure_signature?: string
  result_json?: Record<string, unknown>
  token_usage_json?: Record<string, number>
  latency_ms?: number
  error?: string
  created_at: string
  completed_at?: string
}

export interface ParserGenerationAttempt {
  id: string
  job_id: string
  attempt_number: number
  parent_attempt_id?: string
  stage: 'contract' | 'template' | 'validation' | 'holdout' | 'regression' | string
  action: string
  model_provider?: string
  model_name?: string
  textfsm_runtime_version: string
  dialect_version: string
  dialect_hash: string
  prompt_version?: string
  prompt_hash?: string
  contract_hash?: string
  train_sample_set_hash?: string
  holdout_sample_set_hash?: string
  template_sha256?: string
  template_text?: string
  validation_report_json: Record<string, unknown>
  failure_signature?: string
  candidate_rank?: number
  status: string
  token_usage_json?: Record<string, number>
  latency_ms?: number
  created_at: string
}

export interface ParserJobSample {
  id: string
  snapshot_id: string
  snapshot_source: 'postgresql'
  dataset_id?: string
  device_id: string
  output_signature: string
  sample_role?: 'unassigned' | 'generation' | 'acceptance' | 'negative'
  expectation_json?: {
    record_count: number
    primary_keys: string[][]
  }
  sanitization_status: 'pending_sanitization' | 'sanitized' | 'failed'
  sanitization_version?: string
  sanitized_text?: string
  sanitized_preview?: string
  sanitized_hash?: string
  redaction_summary_json?: Record<string, number>
  content_classification?: {
    status: 'data' | 'valid_empty' | 'empty_unverified' | 'unsupported_command' | 'command_error'
    confidence: number
    reason: string
    recommended_role: 'generation' | 'negative' | 'unassigned'
  }
  source_context?: {
    run_id?: string
    offline_job_id?: string
    period_role?: string
    source_file_name?: string
    device_name?: string
    management_ip?: string
    vendor?: string
    model?: string
    command?: string
    canonical_command?: string
    observed_at?: string
    parse_status?: string
    output_status?: string
    start_line?: number
    end_line?: number
  }
  created_at: string
}

export interface ParserBackfillJob {
  id: string
  template_id: string
  status: string
  dry_run: boolean
  filters_json: Record<string, unknown>
  summary_json?: Record<string, number>
  requested_by: string
  approved_by?: string
  approval_reason?: string
  last_error?: string
  created_at: string
  completed_at?: string
}

export interface ParserRuleGenerationJob {
  id: string
  job_type: string
  status: string
  source_ref?: string | null
  asset_ref?: string | null
  asset_type?: string | null
  asset_id?: string | null
  asset_version_id?: string | null
  asset_digest?: string | null
  payload_json: Record<string, unknown>
  validation_report_json?: Record<string, unknown> | null
  approval_required: boolean
  created_by?: string | null
  created_at?: string
  updated_at?: string
  completed_at?: string | null
}

export interface ParserRuleGenerationJobPreview {
  success: boolean
  job: ParserRuleGenerationJob
  asset_summary: Record<string, unknown>
  candidate_preview: Record<string, unknown>
  payload_summary: Record<string, unknown>
  validation_summary: Record<string, unknown>
  historical_replay?: Record<string, unknown> | null
  agent_loop?: Record<string, unknown> | null
  approval_readiness: {
    can_approve: boolean
    blockers: string[]
  }
  validation_report: Record<string, unknown>
}

export interface ParserRuleGenerationContextPreview {
  success: boolean
  ready: boolean
  job_id: string
  workflow: string
  context: Record<string, unknown>
  missing_context: string[]
  privacy?: Record<string, unknown>
  context_id?: string
  context_digest?: string
  dataset_ids?: string[]
}

export interface ParserRuleAssetBundleStep {
  key: string
  label: string
  status: 'passed' | 'ready' | 'blocked' | 'skipped'
  detail: Record<string, unknown>
}

export interface ParserRuleAssetReplayScope {
  mode?: string
  run_ids?: string[]
  run_id_count?: number
  sampled_run_ids?: string[]
  sampled_run_id_count?: number
  missing_run_ids?: string[]
}

export interface ParserRuleAssetReplayRepresentativeness {
  total_change_count?: number
  sampled_change_count?: number
  sample_coverage_rate?: number
  matched_change_count?: number
  match_rate?: number
  window_days?: number
  window_start?: string | null
  window_end?: string | null
  change_shape_count?: number
  matched_change_shape_count?: number
  change_shapes?: Record<string, number>
  sparse_history?: boolean
}

export interface ParserRuleAssetReplay {
  success?: boolean
  sampled_change_count?: number
  matched_change_count?: number
  total_change_count?: number
  collision_count?: number
  event_count?: number
  event_types?: Record<string, number>
  matched_entity_types?: Record<string, number>
  top_devices?: Record<string, number>
  replacement_rule_ids?: string[]
  replacement_logical_keys?: string[]
  estimated_daily_event_count?: number
  representativeness?: ParserRuleAssetReplayRepresentativeness
  scope?: ParserRuleAssetReplayScope
}

export interface ParserRuleAssetContextPrivacy {
  success?: boolean
  raw_cli_included?: boolean
  scope_type?: string | null
  source_snapshot_count?: number | null
  candidate_record_count?: number | null
  sampled_record_count?: number | null
}

export interface ParserRuleAssetContextGate {
  success: boolean
  mode?: string | null
  context_id?: string | null
  context_digest?: string | null
  errors: string[]
  replay_run_id_count: number
  privacy: ParserRuleAssetContextPrivacy
}

export interface ParserRuleAssetReplayGate {
  success?: boolean
  outcome_success?: boolean
  scope_success?: boolean
  evidence_success?: boolean
  scope_required?: boolean
  scope_mode?: string
  expected_run_ids?: string[]
  actual_run_ids?: string[]
  sampled_run_ids?: string[]
  missing_run_ids?: string[]
  sampled_change_count?: number
  total_change_count?: number
  warnings?: string[]
  errors?: string[]
}

export interface ParserRuleAssetBundle {
  id: string
  status: string
  source_ref?: string | null
  observation_type?: string | null
  observation_types?: string[]
  semantic_label?: string | null
  asset_scope?: 'single_observation' | 'multi_observation' | string
  command?: string | null
  vendor?: string | null
  model?: string | null
  context_id?: string | null
  context_digest?: string | null
  context_gate?: ParserRuleAssetContextGate
  offline_job_id?: string | null
  offline_job_ids?: string[]
  dataset_ids?: string[]
  asset_counts: Record<string, number>
  active_asset_counts: Record<string, number>
  asset_requirements?: {
    requires_observation_contract?: boolean
    requires_change_policy?: boolean
    requires_current_state_rule?: boolean
    requires_event_rule?: boolean
    requires_event_rule_assets?: boolean
    requires_historical_replay?: boolean
    [key: string]: unknown
  }
  release_ready: boolean
  engineering_review?: {
    decision?: string
    actor?: string
    reason?: string
    reviewed_at?: string
    replacement_bundle_id?: string
  }
  blockers: string[]
  replay: ParserRuleAssetReplay
  replay_gate?: ParserRuleAssetReplayGate
  steps: ParserRuleAssetBundleStep[]
  children: Array<Record<string, unknown>>
  created_by?: string | null
  created_at?: string
  updated_at?: string
  completed_at?: string | null
}

export interface ParserRuleAssetEngineeringDraft {
  bundle_id: string
  bundle_status: string
  base_digest?: string | null
  context_id?: string | null
  context_digest?: string | null
  offline_job_id?: string | null
  contract: Record<string, unknown> | null
  change_policies: Array<Record<string, unknown>>
  event_rules: Array<Record<string, unknown>>
  review: Record<string, unknown>
  editable: boolean
  context_mode?: string | null
  asset_requirements?: ParserRuleAssetBundle['asset_requirements']
  completeness: {
    contract_count: number
    change_policy_count: number
    event_rule_count: number
    complete: boolean
  }
}

export interface ParserRuleAssetBundlePage {
  items: ParserRuleAssetBundle[]
  total: number
  limit: number
  offset: number
}

export interface ParserPage<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  status_counts?: Record<string, number>
}

export interface ParserPageParams {
  status?: string
  search?: string
  limit?: number
  offset?: number
  [key: string]: string | number | boolean | undefined
}

export interface ParserAssetGap {
  id: string
  status: string
  check_item_id: string
  check_item_version_id?: string | null
  implementation_version_id?: string | null
  canonical_command?: string | null
  gap_type: string
  reason_code: string
  occurrence_count: number
  last_seen_at: string
  patrol_batch_id?: string | null
  sanitized_sample_refs_json?: string[]
  evidence_refs_json?: string[]
  recommended_action: {
    code: 'import_rule_samples'
    label: string
    description: string
    target_url: string
  }
}

export interface ParserWorkItem {
  id: string
  task_type: 'parser' | 'backfill' | 'rule_generation'
  status: string
  priority: 'high' | 'normal' | 'low'
  title: string
  scope: string
  blocker?: string | null
  next_action: string
  created_at?: string | null
  updated_at?: string | null
  age_seconds: number
  target_workspace: string
  target_subtab: string
  production_impact?: number
}

export type OfflinePatrolJobPurpose = 'offline_patrol' | 'rule_asset_source'
export type OfflinePatrolJobType = 'parser_validation' | 'single_patrol' | 'history_compare'
export type OfflinePatrolReportIntent = 'historical' | 'current_attestation'

export interface OfflinePatrolPeriod {
  id: string
  role: 'sample' | 'baseline' | 'current'
  label: string
  observed_at: string
  dataset_id: string
  patrol_run_id?: string | null
  status: string
  result_json?: Record<string, unknown> | null
}

export interface OfflinePatrolSourceFile {
  id: string
  period_role: string
  file_id: string
  relative_path: string
  sha256?: string | null
  size_bytes: number
  status: string
  error_message?: string | null
}

export interface OfflinePatrolDeviceBinding {
  id: string
  period_role: string
  external_device_id: string
  device_name: string
  management_ip?: string | null
  inventory_device_id?: string | null
  match_status: string
  confidence: number
  match_reason?: string | null
  match_candidates?: Array<{
    inventory_device_id: string
    device_name: string
    management_ip?: string | null
    vendor?: string | null
    model?: string | null
    confidence: number
    match_reason: string
  }>
}

export interface OfflineOutputReviewItem {
  review_item_id: string
  period_role: string
  source_relative_path: string
  device_id?: string | null
  device_name?: string | null
  management_ip?: string | null
  canonical_command: string
  start_line?: number | null
  end_line?: number | null
  raw_text_hash?: string | null
  raw_text_length: number
  status: 'empty_unverified'
}

export interface OfflineOutputDisposition extends OfflineOutputReviewItem {
  decision: 'ignore_valid_empty'
  reason: string
  decided_by: string
  decided_at: string
  applies_to_current_preview?: boolean
}

export interface OfflinePatrolJob {
  id: string
  purpose: OfflinePatrolJobPurpose
  job_type: OfflinePatrolJobType
  report_intent: OfflinePatrolReportIntent
  name: string
  display_name?: string
  owner_id: string
  status: string
  vendor?: string | null
  model?: string | null
  publish_events: boolean
  folder_id?: string | null
  workflow_run_id?: string | null
  celery_task_id?: string | null
  attempt_number: number
  progress: { phase?: string; percent?: number; failed_phase?: string | null }
  preview_totals: Record<string, number>
  blocker_count: number
  warning_count?: number
  archived: boolean
  created_at: string
  updated_at: string
  completed_at?: string | null
  periods?: OfflinePatrolPeriod[]
  source_files?: OfflinePatrolSourceFile[]
  device_bindings?: OfflinePatrolDeviceBinding[]
  output_review_items?: OfflineOutputReviewItem[]
  output_dispositions?: OfflineOutputDisposition[]
  preview?: Record<string, unknown>
  result?: Record<string, unknown>
  blockers?: Array<Record<string, unknown>>
  error_message?: string | null
}

export interface OfflineRuleAssetPipelineStep {
  key: string
  label: string
  status: 'not_started' | 'in_progress' | 'ready' | 'available' | 'not_available' | 'blocked' | 'completed'
  detail: string
  counts: Record<string, number>
  optional: boolean
}

export interface OfflineRuleAssetPipelineAction {
  code: string
  label: string
  description: string
  target_url: string
}

export interface OfflineRuleAssetPipeline {
  offline_job_id: string
  status: 'in_progress' | 'blocked' | 'completed'
  progress_percent: number
  current_step: string
  next_action: OfflineRuleAssetPipelineAction
  secondary_actions: OfflineRuleAssetPipelineAction[]
  steps: OfflineRuleAssetPipelineStep[]
  dataset_ids: string[]
  template_ids: string[]
  context_ids: string[]
  bundle_ids: string[]
  command_units?: OfflineCommandAssetUnitSummary
  runtime_targets?: Array<{
    check_item_id: string
    check_item_version_id: string
    implementation_id: string
    implementation_version_id: string
    status: CheckItemRuntimeBundleHealthStatus
    blocking_reasons: string[]
    bundle_version_id?: string | null
    bundle_digest?: string | null
  }>
}

export type OfflineCommandAssetUnitStatus =
  | 'not_started'
  | 'ready'
  | 'running'
  | 'needs_review'
  | 'blocked'
  | 'failed'
  | 'completed'

export type OfflineCommandAssetStageStatus =
  | 'not_started'
  | 'ready'
  | 'running'
  | 'needs_review'
  | 'blocked'
  | 'failed'
  | 'passed'
  | 'skipped'

export interface OfflineCommandAssetUnitSummary {
  total: number
  completed: number
  running: number
  needs_review: number
  blocked: number
  by_status: Record<string, number>
  by_stage: Record<string, number>
  by_mode: Record<string, number>
}

export interface OfflineCommandAssetUnitStage {
  key: string
  label: string
  status: OfflineCommandAssetStageStatus
  detail: string
  metrics: Record<string, unknown>
  blockers: string[]
  artifacts: Array<Record<string, unknown>>
  next_action?: OfflineRuleAssetPipelineAction | null
}

export interface OfflineCommandAssetUnit {
  id: string
  offline_job_id: string
  check_item_id?: string | null
  candidate_check_item_id?: string | null
  candidate_check_item_version_id?: string | null
  candidate_check_item_name?: string | null
  candidate_association_status?: 'preproduction' | null
  asset_key: string
  vendor: string
  model?: string | null
  models: string[]
  family?: string | null
  profile: string
  canonical_command: string
  handling: string
  asset_mode: 'full_chain' | 'observation_only' | 'change_only' | 'raw_hash' | 'ignored' | 'preproduction_mapping' | 'preproduction_text'
  status: OfflineCommandAssetUnitStatus
  current_stage: string
  current_stage_label: string
  progress_percent: number
  sample_count: number
  device_count: number
  output_signature_count: number
  blocker_count: number
  parse_counts: Record<string, number>
  stage_statuses: Record<string, OfflineCommandAssetStageStatus>
  next_action?: OfflineRuleAssetPipelineAction
  parser_job_id?: string | null
  parser_template_id?: string | null
  rule_seed_job_id?: string | null
  context_id?: string | null
  bundle_id?: string | null
  projection_version: number
  updated_at?: string | null
}

export interface OfflineCommandAssetUnitDetail extends OfflineCommandAssetUnit {
  not_applicable_confirmation?: {
    eligible: boolean
    confirmed: boolean
    evidence_count: number
    classification?: 'valid_empty' | null
    blockers: string[]
  }
  stages: OfflineCommandAssetUnitStage[]
  evidence: Array<{
    snapshot_id: string
    period?: { role: string; label: string; dataset_id: string; observed_at: string } | null
    device_id: string
    device_name: string
    observed_at: string
    parse_status: string
    output_status: string
    parser_template_id?: string | null
    raw_text_hash: string
    structured_record_count: number
  }>
  evidence_pagination: {
    page: number
    page_size: number
    total: number
    page_count: number
  }
  parser_attempts: Array<Record<string, unknown>>
  rule_assets: Array<Record<string, unknown>>
  bundle?: Record<string, unknown> | null
}

export interface OfflineCommandAssetUnitPage {
  items: OfflineCommandAssetUnit[]
  page: number
  page_size: number
  total: number
  page_count: number
  summary: OfflineCommandAssetUnitSummary
}

export interface CheckItemProductionGroup {
  id: string
  check_item_id?: string | null
  check_item_version_id?: string | null
  name: string
  display_name: string
  category: string
  description: string
  display_description: string
  classification_required: boolean
  preproduction_associated?: boolean
  governance_status: string
  governance_bucket: string
  current_stage: string
  current_stage_label: string
  progress_percent: number
  sample_count: number
  evidence_device_count: number
  implementation_count: number
  command_count: number
  blocker_count: number
  vendors: string[]
  next_action?: OfflineRuleAssetPipelineAction | null
  implementations: Array<OfflineCommandAssetUnit & {
    mapping_status: 'catalog_resolved' | 'preproduction_associated' | 'unclassified' | 'not_required'
    implementation_id?: string | null
    implementation_version_id?: string | null
    implementation_name?: string | null
  }>
}

export interface CheckItemProductionGroupPage {
  items: CheckItemProductionGroup[]
  page: number
  page_size: number
  total: number
  page_count: number
  summary: {
    total: number
    action_required: number
    in_progress: number
    awaiting_approval: number
    published: number
    not_applicable: number
    exception: number
    unclassified: number
    by_bucket: Record<string, number>
    by_status: Record<string, number>
  }
}

export interface OfflinePatrolReportRisk {
  source?: string
  event_type: string
  severity: string
  device_id?: string
  device_name?: string
  domain?: string
  entity_type?: string
  entity_key?: string
  field?: string
  current_value?: unknown
  unit?: string | null
  observed_at?: string
  evidence_id?: string
  evidence_chain?: Record<string, unknown>
  correlation?: Record<string, unknown>
}

export interface OfflinePatrolReportTrendGroup {
  severity: string
  event_type: string
  risk_name: string
  device_name: string
  count: number
  objects: string
  current_values: string
  criterion: string
}

export interface OfflinePatrolReportTrend {
  available: boolean
  continuity_evidence?: string
  period_count: number
  periods?: Array<Record<string, unknown>>
  comparisons?: Array<{
    from?: string
    to?: string
    change_count: number
    event_count: number
    command_count: number
    parser_ready_rate: number
  }>
  new_evidence_count?: number
  persistent_evidence_count?: number
  resolved_evidence_count?: number
  new_evidence_groups?: OfflinePatrolReportTrendGroup[]
  persistent_evidence_groups?: OfflinePatrolReportTrendGroup[]
  resolved_evidence_groups?: OfflinePatrolReportTrendGroup[]
}

export interface OfflinePatrolComparisonEvidenceSide {
  snapshot_id?: string | null
  run_id?: string | null
  command?: string | null
  observed_at?: string | null
  parse_status?: string | null
  raw_text?: string | null
  structured_data?: unknown
}

export interface OfflinePatrolComparisonEvidence {
  change_id?: string | null
  device_id?: string | null
  command?: string | null
  comparison_kind?: 'structured_event' | 'unstructured_text' | string
  parser_failure_text_comparison?: boolean
  baseline?: OfflinePatrolComparisonEvidenceSide | null
  current?: OfflinePatrolComparisonEvidenceSide | null
}

export interface OfflinePatrolAnalysisSummary {
  title: string
  generated_at: string
  assessment: {
    code: string
    status: string
    trust: string
    basis: string
    conclusion_allowed: boolean
  }
  metrics: Record<string, number>
  risk_summary: {
    total: number
    by_severity: Record<string, number>
    by_type: Record<string, number>
    correlated_evidence_count: number
  }
  event_summary: {
    fact_count: number
    fault_fact_count: number
    recovery_fact_count: number
    resolved_prior_fault_count?: number
    state_improvement_count?: number
    fault_by_type: Record<string, number>
    recovery_by_type: Record<string, number>
  }
  risks: OfflinePatrolReportRisk[]
  correlated_evidence: OfflinePatrolReportRisk[]
  trend?: OfflinePatrolReportTrend
  numeric_trends?: {
    notice_count: number
    recovered_count: number
    unknown_count: number
    summary: string
    items: Array<{
      observation_id: string
      device_id: string
      entity_key: string
      label: string
      status: 'notice' | 'recovered'
      unit: string
      baseline_value: number
      current_value: number
      explanation: string
    }>
  }
  structured_event_evidence?: {
    structured_change_count: number
    event_fact_count: number
    no_event_count: number
    items: Array<{
      change_id?: string | null
      device_id?: string | null
      device_name?: string | null
      command?: string | null
      entity_key?: string | null
      field?: string | null
      baseline?: {
        snapshot_id?: string | null
        value?: unknown
      }
      current?: {
        snapshot_id?: string | null
        value?: unknown
      }
      event?: {
        event_label?: string | null
        severity?: string | null
        status?: string | null
      }
    }>
  }
  unstructured_comparison?: {
    collected_output_count: number
    compared_output_count: number
    changed_output_count: number
    items: Array<{
      change_id?: string | null
      device_id?: string | null
      device_name?: string | null
      command?: string | null
      profile_label?: string | null
      added_line_count?: number
      removed_line_count?: number
      parser_failure_text_comparison?: boolean
    }>
  }
  data_quality: Record<string, unknown>
  evidence: Record<string, unknown> & {
    analysis?: {
      attempt_id?: string
      revision?: string
      rule_bundle_digest?: string
      source_change_count?: number
    }
  }
}

export type PatrolCurrentAuthorityStatus =
  | 'not_requested'
  | 'blocked'
  | 'ready_for_attestation'
  | 'approved'
  | 'attesting'
  | 'dispatching'
  | 'completed'
  | 'failed'
  | 'revoked'
  | 'expired'

export interface PatrolCurrentAuthority {
  schema_version: string
  offline_job_id: string
  report_intent?: OfflinePatrolReportIntent
  request_id?: string
  candidate_batch_id?: string | null
  status: PatrolCurrentAuthorityStatus
  blocker_codes: string[]
  blockers: Array<{
    code: string
    message: string
    remediation?: {
      code?: string
      label?: string
      target?: 'sources' | 'proof_form' | 'new_current_job'
    }
  }>
  request_digest?: string
  source_manifest_digest?: string
  identity_binding_digest?: string
  scope_contract_digest?: string
  intended_target_matrix_digest?: string
  scope_decision_digest?: string
  scope_reconciled?: boolean
  source_file_count?: number
  device_count?: number
  selected_check_item_count?: number
  current_period?: Record<string, unknown>
  proof?: {
    completeness_statement?: string
    completeness_evidence_refs?: string[]
    observed_at_evidence_refs?: string[]
    collection_tool?: string
    collector_identity?: string
  }
  proof_options?: {
    observed_at_evidence_refs?: string[]
    completeness_evidence_refs?: string[]
  }
  requested_by_principal_id?: string
  requested_by_display?: string
  prepared_at?: string
  expires_at?: string
  qualification?: {
    id: string
    status: string
    digest: string
    attested_by_principal_id: string
    attested_by_display: string
    attested_at?: string | null
    revoked_at?: string | null
    revocation_reason?: string | null
  } | null
  production_batch?: {
    patrol_batch_id: string
    dispatch_status: string
    dispatch_error?: string | null
    dispatch_error_code?: string | null
    report_status: string
    report_error?: string | null
    runtime_manifest_id?: string | null
    runtime_manifest_digest?: string | null
    target_count: number
    launched_count: number
    skipped_count: number
    workflow_status_counts: Record<string, number>
    execution_status_counts?: Record<string, number>
  } | null
}

export interface OfflinePatrolJobPage {
  items: OfflinePatrolJob[]
  total: number
  page: number
  page_size: number
}

export interface ParserReplayReport {
  id: string
  job_id: string
  job_type: string
  job_status: string
  asset_type?: string | null
  asset_id?: string | null
  asset_version_id?: string | null
  asset_digest?: string | null
  source_ref?: string | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  success?: boolean | null
  sampled_change_count: number
  matched_change_count: number
  estimated_daily_event_count: number
  collision_count: number
  event_types: Record<string, number>
  matched_entity_types: Record<string, number>
  top_devices: Record<string, number>
  collision_rules: Record<string, number>
  unexpected_matches: Array<Record<string, unknown>>
  recovery_pair_coverage?: unknown
  report_json: Record<string, unknown>
}

export interface ParserApprovalAudit {
  id: string
  job_id: string
  job_type: string
  job_status: string
  action: 'approved' | 'rejected' | 'deprecated' | string
  actor?: string | null
  reason?: string | null
  occurred_at?: string | null
  asset_type?: string | null
  asset_id?: string | null
  asset_version_id?: string | null
  asset_ref?: string | null
  asset_digest?: string | null
  source_ref?: string | null
  validation_success?: boolean | null
  synthetic_success?: boolean | null
  replay_success?: boolean | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  governance?: Record<string, unknown>
}

export interface ParserApprovalAuditPage extends ParserPage<ParserApprovalAudit> {
  summary?: {
    total: number
    approved: number
    rejected: number
    deprecated: number
  }
}

export interface ParserAgentLoopAttempt {
  id: string
  job_id: string
  job_type: string
  job_status: string
  asset_type?: string | null
  asset_id?: string | null
  asset_version_id?: string | null
  asset_digest?: string | null
  source_ref?: string | null
  attempt: number
  stage: string
  generator?: string | null
  mode?: string | null
  success?: boolean | null
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  validator_feedback: Record<string, unknown>
  revision_request?: Record<string, unknown> | null
  ready_for_approval?: boolean | null
  next_action?: string | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ParserChangePolicy {
  id: string
  policy_id: string
  policy_name: string
  version_id: string
  version: number
  observation_type: string
  semantic_type: string
  compare_mode: string
  policy: Record<string, unknown>
  validation_report?: Record<string, unknown> | null
  version_status: string
  status: string
  source_job_id?: string | null
  check_item_version_id?: string | null
  digest: string
  activated_at?: string | null
  deprecated_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface ParserRuntimeRuleAssetBundle {
  ready: boolean
  collection_ready: boolean
  analysis_ready: boolean
  event_evaluation_ready: boolean
  digest: string
  observation_contracts: Record<string, Record<string, unknown>>
  change_policies: Record<string, ParserChangePolicy | Record<string, unknown>>
  event_rules: ParserEventRule[] | Array<Record<string, unknown>>
  missing_observation_contracts: string[]
  missing_change_policies: string[]
  missing_event_rules: string[]
  observation_types: string[]
  counts: {
    observation_contracts: number
    change_policies: number
    event_rules: number
  }
}

export interface ParserRuntimeRuleAssetHealth extends Partial<ParserRuntimeRuleAssetBundle> {
  status?: string
  missing_count?: number
  observation_schemas?: string[]
  reason?: string
  error?: string
}

export type CheckItemRuntimeBundleHealthStatus = 'ready' | 'missing' | 'invalid'

export interface CheckItemRuntimeBundleHealthItem {
  check_item_id: string
  check_item_version_id: string
  check_item_name: string
  check_item_display_name?: string
  result_mode: string
  assessment_mode: string
  implementation_id: string
  implementation_version_id: string
  implementation_name: string
  commands: string[]
  status: CheckItemRuntimeBundleHealthStatus
  ready: boolean
  blocking_reasons: string[]
  bundle_id?: string | null
  bundle_version_id?: string | null
  bundle_digest?: string | null
  activated_at?: string | null
}

export interface CheckItemRuntimeBundleHealthPage {
  items: CheckItemRuntimeBundleHealthItem[]
  page: number
  page_size: number
  total: number
  page_count: number
  summary: {
    scope: 'active_catalog'
    catalog_version: number
    check_item_count: number
    implementation_pair_count: number
    ready_count: number
    missing_count: number
    invalid_count: number
    ready_rate: number
    ready: boolean
    status_counts: Record<string, number>
  }
}

export interface RuntimeBundleAssemblyIssue {
  code: string
  message: string
  remediation?: string | null
  remediation_action?: {
    code: string
    label: string
    description: string
    target_url: string
    asset_type?: string | null
  } | null
  context?: Record<string, unknown>
}

export interface RuntimeBundleCommandRoute {
  command: string
  canonical_command: string
  priority: number
  status: 'executable' | 'unavailable' | 'ambiguous'
  execution_mode?: string
  selected_parser_template_id?: string | null
  selected_contract_ids: string[]
  candidate_count: number
  reasons: Array<Record<string, unknown>>
}

export interface AssetRequirementDecision {
  required: boolean
  reason_code: string
  required_observation_types?: string[]
  conditional_observation_types?: string[]
  required_event_types_by_observation?: Record<string, string[]>
  conditional_event_types_by_observation?: Record<string, string[]>
  required_event_classes?: Record<string, string>
  required_event_severities?: Record<string, string>
  required_recovery_pairs_by_observation?: Record<
    string,
    Array<{ fault: string; recovery: string }>
  >
}

export interface AssetRequirementProfile {
  schema_version: 'asset-requirement-profile/v1' | string
  result_mode: string
  assessment_mode: string
  requirements: Record<string, AssetRequirementDecision>
  required_asset_types: string[]
  not_required: Record<string, string>
  required_runtime_facts: string[]
}

export interface RuntimeBundleAssemblyPreview {
  schema_version: number
  check_item: {
    id?: string | null
    version_id: string
    name?: string | null
    display_name?: string | null
    result_mode?: string
    assessment_mode?: string
  }
  implementation: {
    id?: string | null
    version_id: string
    name?: string | null
    selector?: Record<string, unknown>
    commands?: Array<{ command: string; canonical_command: string; priority: number }>
  }
  selected: {
    parser_template_ids: string[]
    observation_contract_ids: string[]
    change_policy_version_ids: string[]
    event_rule_version_ids: string[]
    event_rule_bindings: Record<string, string[]>
    comparison_profile_version_id?: string | null
    entity_identity_policy_version_id?: string | null
  }
  asset_requirement_profile?: AssetRequirementProfile | null
  asset_requirement_profile_digest?: string | null
  candidates: {
    parsers: Array<Record<string, any>>
    observation_contracts: Array<Record<string, any>>
    change_policies: Array<Record<string, any>>
    event_rules: Array<Record<string, any>>
    comparison_profiles: Array<Record<string, any>>
  }
  command_routes: RuntimeBundleCommandRoute[]
  compatibility: {
    expected_observation_types?: string[]
    produced_observation_types?: string[]
    observation_projection?: string[]
    current_state_rule_types?: string[]
    change_policy_types?: string[]
    executable_commands?: string[]
    unavailable_commands?: string[]
    command_availability?: 'available' | 'unavailable' | 'not_applicable'
    /** @deprecated Fallback command execution is disabled. */
    unavailable_fallbacks?: string[]
    /** @deprecated Fallback command execution is disabled. */
    fallback_resilience?: 'full' | 'reduced' | 'none' | 'not_applicable'
    parser_manifests?: Record<string, Record<string, unknown>>
  }
  active_bundle?: {
    bundle_id: string
    version_id: string
    version: number
    digest: string
    activated_at?: string | null
  } | null
  drafts: Array<{
    version_id: string
    version: number
    digest: string
    created_at?: string | null
  }>
  operationally_ready: boolean
  command_availability?: 'available' | 'unavailable' | 'not_applicable'
  /** @deprecated Fallback command execution is disabled. */
  fallback_resilience: 'full' | 'reduced' | 'none' | 'not_applicable'
  blockers: RuntimeBundleAssemblyIssue[]
  warnings: RuntimeBundleAssemblyIssue[]
  preview_digest: string
}

export interface CheckItemRuntimeBundleVersion {
  bundle_id: string
  version_id: string
  version: number
  version_status: 'draft' | 'active' | 'deprecated'
  digest: string
  check_item_version_id: string
  implementation_version_id: string
  parser_bundle: Record<string, Record<string, unknown>>
  compatibility: Record<string, any>
  validation_report: Record<string, any>
  assets: Array<Record<string, any>>
  activated_at?: string | null
  deprecated_at?: string | null
}

export interface RuntimeBundleAssemblyPayload {
  check_item_version_id: string
  implementation_version_id: string
  parser_template_ids?: string[]
  observation_contract_ids?: string[]
  change_policy_version_ids?: string[]
  event_rule_version_ids?: string[]
  comparison_profile_version_id?: string | null
}

export type RolloutCampaignStatus =
  | 'draft'
  | 'target_frozen'
  | 'shadow'
  | 'canary_5'
  | 'canary_20'
  | 'canary_50'
  | 'full_100'
  | 'observing'
  | 'paused'
  | 'rolled_back'
  | 'completed'

export type RolloutStageStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'passed'
  | 'failed'
  | 'paused'
  | 'rolled_back'

export interface RolloutStageAttempt {
  id: string
  campaign_id: string
  stage: string
  attempt_number: number
  percentage: number
  status: RolloutStageStatus
  target_manifest_digest: string
  runtime_bundle_digest: string
  comparison_context_digest: string
  baseline_window_json: Record<string, unknown>
  thresholds_json: Record<string, unknown>
  metrics_json?: Record<string, unknown> | null
  comparison_result_json?: Record<string, unknown> | null
  evidence_refs_json?: Array<Record<string, unknown>> | null
  blockers_json?: string[] | null
  decision_digest?: string | null
  started_by?: string | null
  approved_by?: string | null
  approval_comment?: string | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  approved_at?: string | null
}

export interface RolloutStageExecution {
  id: string
  campaign_id: string
  stage_attempt_id: string
  execution_mode: 'snapshot_replay' | 'live_capture'
  status: 'queued' | 'capturing' | 'running' | 'evaluating' | 'completed' | 'failed' | 'cancelled'
  selection_manifest_json?: {
    schema_version?: string
    stage?: string
    targets?: Array<{
      target_key?: string
      device_id?: string
      check_item_version_id?: string
      periods?: Array<{
        run_id?: string
        snapshot_ids?: string[]
      }>
    }>
  } | null
  selection_manifest_digest: string
  capture_batch_id?: string | null
  capture_manifest_json?: Record<string, unknown> | null
  capture_manifest_digest?: string | null
  capture_requested_at?: string | null
  capture_completed_at?: string | null
  source_snapshot_ids_json?: string[] | null
  snapshot_set_digest: string
  fact_high_watermark: string
  baseline_projection_digest?: string | null
  candidate_projection_digest?: string | null
  comparison_manifest_id?: string | null
  decision_digest?: string | null
  worker_owner?: string | null
  heartbeat_at?: string | null
  attempt_count: number
  failure_code?: string | null
  error?: string | null
  created_by?: string | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  updated_at: string
}

export interface RolloutCampaign {
  id: string
  idempotency_key: string
  name: string
  description?: string | null
  status: RolloutCampaignStatus
  current_stage?: string | null
  paused_from_stage?: string | null
  target_manifest_json?: {
    schema_version?: string
    selector?: Record<string, unknown>
    matrix?: Array<Record<string, string>>
  } | null
  target_manifest_digest?: string | null
  runtime_bundle_refs_json?: Array<{
    role: 'baseline' | 'candidate'
    version_id: string
    digest: string
  }> | null
  runtime_bundle_digest?: string | null
  comparison_context_ids_json?: string[] | null
  comparison_context_digest?: string | null
  baseline_window_json?: Record<string, string> | null
  thresholds_json?: Record<string, unknown> | null
  rollback_plan_json?: Record<string, unknown> | null
  pause_context_json?: Record<string, unknown> | null
  last_error?: string | null
  lock_version: number
  created_by: string
  frozen_by?: string | null
  paused_by?: string | null
  completed_by?: string | null
  created_at: string
  updated_at: string
  frozen_at?: string | null
  paused_at?: string | null
  rolled_back_at?: string | null
  completed_at?: string | null
  attempts: RolloutStageAttempt[]
  stage_executions: RolloutStageExecution[]
  current_attempt?: RolloutStageAttempt | null
  next_action: string
  stage_sequence: string[]
}

export interface RolloutCampaignPage {
  items: RolloutCampaign[]
  total: number
  page: number
  page_size: number
  page_count: number
  summary: {
    total: number
    status_counts: Record<string, number>
    active: number
    awaiting_approval: number
    paused: number
    rolled_back: number
  }
}

export interface RolloutCampaignFreezePayload {
  target_manifest: {
    selector?: Record<string, unknown>
    matrix: Array<{
      device_id: string
      check_item_version_id: string
      candidate_implementation_version_id?: string
      baseline_route:
        | {
          mode: 'runtime_bundle'
          runtime_bundle_version_id: string
          implementation_version_id?: string
        }
        | {
          mode: 'legacy_route'
          route_id: string
          digest: string
          implementation_version_id?: string
        }
        | {
          mode: 'no_prior_production'
        }
      candidate_runtime_bundle_version_id: string
    }>
  }
  runtime_bundle_refs: Array<{
    role: 'baseline' | 'candidate'
    version_id: string
    digest: string
  }>
  comparison_context_ids: string[]
  baseline_window: { start_at: string; end_at: string }
  thresholds?: Record<string, unknown>
  rollback_plan: {
    mode: 'legacy_route' | 'runtime_bundle' | 'no_prior_production'
    verified: boolean
    evidence_refs: Array<Record<string, unknown> | string>
    [key: string]: unknown
  }
  reason: string
}

export interface ParserCoverage {
  templates_total: number
  templates_active: number
  template_status_counts?: Record<string, number>
  jobs_total: number
  jobs_pending: number
  job_status_counts?: Record<string, number>
  missing_candidates: number
  repair_candidates: number
  command_aliases: number
  device_profiles: number
  identity_signatures: number
  auto_dispatch_enabled: boolean
  dialect_version?: string
  dialect_active_templates?: number
  dialect_platform_violations?: number
  dialect_runtime_blocked?: number
  dialect_regression_evidence_missing?: number
  production_open_jobs: number
  pending_sanitization_samples: number
  sanitized_samples: number
  sanitization_failed_samples: number
  backfills_pending_approval: number
  backfills_failed: number
  effective_plan_coverage?: {
    eligible_command_count?: number
    ready_command_count?: number
    ready_rate?: number
    inventory_vendor_count?: number
    active_template_count?: number
    [key: string]: unknown
  }
  rule_generation_total?: number
  rule_generation_status_counts?: Record<string, number>
  alert_count: number
  blocked_by_parser_ratio_24h: number
  alerts: Array<{ code: string; count: number; severity: 'warning' | 'error'; message: string }>
}

export interface ParserDispatchControl {
  auto_dispatch_enabled: boolean
}

export interface ParserCommandAlias {
  id: string
  alias: string
  canonical_command: string
  confidence: number
  status: string
}

export interface ParserDeviceProfile {
  id: string
  vendor: string
  model: string
  family?: string
  confidence: number
  status: string
}

export interface ParserIdentitySignature {
  id: string
  vendor: string
  model: string
  family?: string
  evidence_command: string
  pattern: string
  confidence: number
  status: string
}

export interface ParserTemplateExport {
  success: boolean
  filename: string
  content_base64: string
  sha256: string
  template_count: number
  exported_count: number
  manifest: Array<Record<string, unknown>>
}

export interface ParserEventRule {
  rule_id: string
  rule_name: string
  description?: string
  status: string
  current_version: number
  version_id: string
  version: number
  event_type: string
  severity: string
  condition: Record<string, unknown>
  version_status: string
  source_job_id?: string | null
  check_item_version_id?: string | null
  validation_report?: Record<string, unknown> | null
  digest?: string | null
  created_by?: string | null
  created_at?: string | null
  activated_at?: string | null
  deprecated_at?: string | null
}

export interface ParserEventRuleTestResult {
  success: boolean
  sample_count: number
  matched_count: number
  unmatched_count: number
  rows: Array<{
    index: number
    matched: boolean
    error?: string | null
    sample: Record<string, unknown>
  }>
}

export interface ParserEventRuleReplayResult {
  success: boolean
  rule_count: number
  sampled_change_count: number
  matched_change_count: number
  estimated_daily_event_count: number
  event_count: number
  event_types: Record<string, number>
  matched_entity_types: Record<string, number>
  top_devices: Record<string, number>
  collision_count: number
  collision_rules: Record<string, number>
  unexpected_matches: Array<Record<string, unknown>>
  recovery_pair_coverage?: unknown
}

export interface ParserCommandProfile {
  profile_id: string
  profile_name: string
  status: string
  current_version: number
  profile_version_id?: string
  version?: number
  version_status?: string
  items: Array<{ action: string; command: string }>
  profile_mode?: 'check_items' | 'legacy_commands'
  check_item_ids?: string[]
  check_items?: ParserProfileCheckItemSelection[]
  coverage?: Record<string, unknown>
}

export interface ParserProfileCheckItemSelection {
  id: string
  profile_version_id: string
  check_item_version_id: string
  check_item_id: string
  check_item_name?: string
  check_item_category?: string
  check_item_version: number
  result_mode: string
  assessment_mode: string
  report_projection: string
  priority: number
  required: boolean
  status: string
}

export interface ParserCheckItem {
  check_item_id: string
  check_item_version_id: string
  version: number
  name: string
  display_name?: string
  category: string
  description: string
  display_description?: string
  result_mode: string
  assessment_mode: string
  canonical_contract: Record<string, unknown>
  report_projection: string
  applicability: Record<string, unknown>
  freshness_policy: Record<string, unknown>
  empty_result_policy: Record<string, unknown>
  coverage_policy: Record<string, unknown>
  status: string
  digest: string
  patrol_status?: CheckItemPatrolStatus | null
  implementation_count: number
  implementations: Array<{
    implementation_id: string
    implementation_version_id: string
    name: string
    description: string
    match: Record<string, unknown>
    commands: Array<{ command: string; priority: number }>
    collector: string
    parser_requirement: string
    template_scope?: string | null
    adapter_spec?: Record<string, unknown>
    output_quality_policy: Record<string, unknown>
    selection_priority: number
    status: string
  }>
}

export interface CheckItemCatalogImplementationCandidate {
  implementation_id: string
  implementation_version_id: string
  version: number
  name: string
  description: string
  check_item_ids: string[]
  match: Record<string, unknown>
  commands: Array<{ command: string; priority: number }>
  collector: string
  parser_requirement: 'structured' | 'normalized_diff' | 'hash_only' | 'evidence_only'
  template_scope?: string | null
  adapter_spec?: Record<string, unknown>
  selection_priority: number
  output_quality_policy: Record<string, unknown>
  status: string
  digest: string
}

export interface CheckItemCatalogCheckItemCandidate {
  check_item_id: string
  check_item_version_id: string
  version: number
  name: string
  category: string
  description: string
  result_mode: 'structured' | 'normalized_diff' | 'hash_only' | 'evidence_only'
  assessment_mode: 'health' | 'change' | 'evidence'
  canonical_contract: Record<string, unknown>
  report_projection: string
  applicability: Record<string, unknown>
  freshness_policy: Record<string, unknown>
  empty_result_policy: Record<string, unknown>
  coverage_policy: Record<string, unknown>
  status: string
  digest: string
}

export type CheckItemCatalogCandidate =
  | CheckItemCatalogImplementationCandidate
  | CheckItemCatalogCheckItemCandidate

export interface CheckItemCatalogChange {
  id: string
  asset_kind: 'check_item' | 'collection_implementation'
  asset_id: string
  version_id: string
  version: number
  baseline_version_id?: string | null
  status: 'draft' | 'pending_approval' | 'approved' | 'rejected' | 'cancelled'
  payload_digest: string
  impact_snapshot_json?: CheckItemCatalogImpact | null
  impact_digest?: string | null
  created_by: string
  creation_reason: string
  submitted_by?: string | null
  submission_reason?: string | null
  submitted_at?: string | null
  decided_by?: string | null
  decision_reason?: string | null
  rollout_campaign_id?: string | null
  cutover_bundle_version_ids_json?: string[] | null
  cutover_evidence_json?: Record<string, unknown> | null
  cutover_digest?: string | null
  decided_at?: string | null
  created_at: string
  updated_at: string
  candidate: CheckItemCatalogCandidate
}

export interface CheckItemCatalogImpactMessage {
  code: string
  message: string
  count?: number
  next_action: string
}

export interface CheckItemCatalogImpact {
  schema_version: 'check-item-catalog-impact/v1'
  change_id: string
  asset_kind: 'check_item' | 'collection_implementation'
  asset_id: string
  candidate_version_id: string
  baseline_version_id?: string | null
  candidate_digest: string
  inventory_device_count: number
  target_pair_count: number
  rollout_scope: Array<{ device_id: string; check_item_id: string }>
  rollout_scope_count: number
  affected_check_item_ids: string[]
  summary: {
    unchanged: number
    changed: number
    newly_supported: number
    newly_unsupported: number
    ambiguous: number
  }
  rows: Array<{
    device_id: string
    device_name: string
    vendor?: string | null
    family?: string | null
    model?: string | null
    check_item_id: string
    change: string
    before: Record<string, unknown>
    after: Record<string, unknown>
  }>
  rows_truncated: boolean
  active_runtime_bundle_version_ids: string[]
  semantic_diff?: {
    changed_fields: string[]
    added_observation_types: string[]
    removed_observation_types: string[]
    collection_implementation_ids: string[]
  }
  blockers: CheckItemCatalogImpactMessage[]
  warnings: CheckItemCatalogImpactMessage[]
  impact_digest: string
}

export interface CheckItemCatalogPublicationOptions {
  change_id: string
  requires_rollout: boolean
  eligible_campaigns: Array<{
    campaign_id: string
    name: string
    completed_at?: string | null
    candidate_runtime_bundle_version_ids: string[]
    target_count: number
    evidence_digest: string
  }>
}

export interface CheckItemCatalogImplementationDraftInput {
  id: string
  name: string
  description?: string
  check_item_ids: string[]
  match: Record<string, unknown>
  commands: Array<{ command: string; priority: number }>
  collector: string
  parser_requirement: 'structured' | 'normalized_diff' | 'hash_only' | 'evidence_only'
  template_scope?: string | null
  adapter_spec?: Record<string, unknown>
  output_quality_policy?: Record<string, unknown>
  selection_priority: number
  reason?: string
}

export interface CheckItemCatalogCheckItemDraftInput {
  id: string
  name: string
  category: string
  description?: string
  result_mode: 'structured' | 'normalized_diff' | 'hash_only' | 'evidence_only'
  assessment_mode: 'health' | 'change' | 'evidence'
  canonical_contract: Record<string, unknown>
  report_projection: string
  applicability?: Record<string, unknown>
  freshness_policy?: Record<string, unknown>
  empty_result_policy?: Record<string, unknown>
  coverage_policy?: Record<string, unknown>
  reason?: string
}

export interface CanonicalObservationDefinition {
  id: string
  description: string
  semantic_type: string
  value_types: string[]
  canonical_unit?: string | null
  dimensions: string[]
  allowed_dimension_values: Record<string, string[]>
  allowed_values: unknown[]
  state_groups: Record<string, unknown[]>
  assessment_roles: string[]
}

export interface CanonicalObservationCatalog {
  catalog_id: string
  version: number
  status: string
  digest: string
  items: CanonicalObservationDefinition[]
}

export interface CanonicalRuleTarget {
  observation_type: string
  canonical_field: string
  required: boolean
  semantic_type: string
  value_types: string[]
  unit?: string | null
  dimensions: string[]
  allowed_values: unknown[]
  state_groups: Record<string, unknown[]>
  assessment_roles: string[]
  policy_constraints: Record<string, unknown>
  event_constraints: Record<string, unknown>
  threshold_references: Record<string, { field_name?: string; [key: string]: unknown }>
  baseline_comparison: Record<string, unknown>
  context_fields: string[]
}

export interface CanonicalRuleContext {
  schema_version: 'canonical-rule-context/v1'
  check_item_id: string
  check_item_version_id: string
  result_mode: string
  assessment_mode: string
  contract: Record<string, unknown>
  targets: CanonicalRuleTarget[]
  asset_requirement_profile: Record<string, unknown>
  rule_asset_requirements: {
    requires_observation_contract: boolean
    requires_change_policy: boolean
    requires_current_state_rule: boolean
    requires_event_rule: boolean
    requires_event_rule_assets: boolean
    requires_historical_replay: boolean
    current_state_observation_types: string[]
    change_policy_observation_types: string[]
    required_event_types_by_observation: Record<string, string[]>
  }
  digest: string
  check_item?: Record<string, unknown>
}

/** What an unsaved check-item draft will judge, one entry per standard field. */
export interface CheckItemRulePreview {
  valid: boolean
  errors: string[]
  assessment_mode: 'health' | 'change' | 'evidence' | null
  assessment_mode_label?: string
  fields: Array<{
    canonical_field: string
    observation_type: string
    outcome: 'health' | 'change' | 'reference' | 'evidence'
    lines: string[]
    warnings: string[]
    /** Which row of the rule generation guide the field falls in, what it generates and why. */
    guide_row?: string
    summary?: string
    reason?: string
    change_rules?: number
    event_rules?: number
  }>
  /** The draft as field -> judgment -> outcomes, compared with the published version when there is one. */
  graph?: CheckItemRelations
}

/** What the published version of a check item judges today, in the same sentences as the preview. */
export interface CheckItemRulesInForce {
  check_item_id: string
  version: number
  check_item_version_id: string
  leftover_count: number
  fields: Array<{
    canonical_field: string
    observation_type: string
    lines: string[]
    stale_count: number
    stale_versions: number[]
  }>
}

/** Where a check item stands in the patrol: the first unmet step names the state. */
export interface CheckItemPatrolStatus {
  state: 'ready' | 'display_only' | 'rules_missing' | 'adapters_pending' | 'no_adapter' | 'topology'
  judged_fields: number
  rules_wanted: number
  rules_active: number
  rules_awaiting_approval: boolean
  adapters: number
  adapters_ready: number
  pending_change: { id: string; status: 'draft' | 'pending_approval' } | null
  blockers: string[]
}

type RelationChange = 'same' | 'added' | 'removed' | 'changed'

/** Events on record under one event type that a rule of the check item produces or produced. */
export interface CheckItemEventTally {
  event_type: string
  event_class: 'fault' | 'recovery' | 'change' | string
  severity: string
  field?: string
  open?: number
  total?: number
  count?: number
}

/** A new field as one stored output shows it: the captured value, its line, and the line above. */
export interface AdapterFieldSource {
  value: string
  line: string
  above: string
}

/** What a trial of adding fields to an adapter found. A trial saves nothing. */
export interface AdapterFieldTrial {
  status: 'ready' | 'no_data' | 'regressed' | 'failed' | 'unsupported'
  requested_fields: string[]
  sample_count: number
  record_count?: number
  model_calls: number
  existing_field_count: number
  rules_in_force: number
  rules_added?: number
  added_lines?: string[]
  fields: Array<{
    name: string
    description: string
    records: number
    samples: number
    examples: string[]
    sources: AdapterFieldSource[]
  }>
  provided_fields: string[]
  absent_fields: string[]
  regression: { passed: boolean; differences: string[] } | null
  reason?: string
  detail?: string
  /** The adapter, the check item or the template changed after the trial. */
  stale: boolean
}

export interface AdapterFieldsSaved {
  fields: string[]
  left_out_fields: string[]
  implementation_version: number
  previous_implementation_version: number
  runtime_bundle_version_ids: string[]
  outputs_replayed: number
  records_replayed: number
  at: string
  by: string
}

/** The fields an adapter lacks for one check item, and the last trial of adding some. */
export interface AdapterFieldsState {
  implementation_id: string
  implementation_name: string
  check_item_id: string
  status: 'idle' | 'queued' | 'running' | 'ready' | 'no_data' | 'regressed' | 'failed' | 'unsupported' | 'saved' | 'cancelled'
  requested_fields: string[]
  started_at?: string | null
  finished_at?: string | null
  sample_count: number
  /** The check item's fields, and how many of them the adapter provides: the detail page's "4 / 7". */
  field_count: number
  provided_count: number
  missing_fields: Array<{ name: string; description: string; use: string; absent: boolean }>
  result: AdapterFieldTrial | null
  saved?: AdapterFieldsSaved | null
  error?: string | null
  error_detail?: string | null
}

/** How a field's settings become rules: one row per kind of setting. */
export interface RuleGenerationGuide {
  rows: Array<{
    key: string
    kinds: string
    use: string
    needs: string
    change_rule: string
    event_rules: string
    effect: string
  }>
  fixed: string[]
  timing: string[]
}

/** A check item as field -> judgment -> outcomes; `change` is set when two versions are compared. */
export interface CheckItemRelations {
  /** `draft` is an unsaved definition: nothing is in force yet and no events are on record. */
  mode: 'current' | 'diff' | 'draft'
  patrolled?: boolean
  rows: Array<{
    field: { name: string; description: string; observation_type: string; change?: RelationChange }
    rule: {
      kind: 'health' | 'change' | 'state'
      title: string
      detail: string
      lines: string[]
      state?: 'active' | 'missing'
      change?: RelationChange
      before?: { title: string; detail: string }
    }
    outcomes: Array<{
      key: string
      kind: 'event' | 'abnormal' | 'trend' | 'counted'
      text: string
      event_type?: string
      event_class?: 'fault' | 'recovery' | 'change' | string
      severity?: string
      judges_current?: boolean
      resolves?: string[]
      state?: 'active' | 'missing'
      open?: number
      total?: number
      change?: RelationChange
      before_text?: string
    }>
  }>
  reference: string[] | { fields: string[]; added: string[]; removed: string[] }
  evidence: string[] | { fields: string[]; added: string[]; removed: string[] }
  retired?: CheckItemEventTally[]
  errors: string[]
}

export interface CheckItemEventList {
  event_type: string
  total: number
  items: Array<{
    id: string
    device_id: string
    entity_key: string
    status: string
    severity: string
    last_seen_at: string | null
    occurrence_count: number
    old?: unknown
    new?: unknown
    closed_note?: string | null
  }>
}

/** A trial of deleting one check item: the delete is tried on the server and rolled back. */
export interface CheckItemDeletePreview {
  check_item_id: string
  deletable: boolean
  blockers: string[]
  name?: string
  version?: number
  version_count?: number
  cancelled_change_count?: number
  rules?: Array<{ kind: 'change_policy' | 'event_rule'; name: string }>
  runtime_bundle_version_ids?: string[]
  adapters_withdrawn?: Array<{ implementation_id: string; name: string }>
  adapters_kept?: Array<{ implementation_id: string; name: string; also_serves: string[] }>
  events_closed?: CheckItemEventTally[]
}

/** The last patrol's stored values judged again under a candidate version; nothing is collected or written. */
export interface CheckItemReleaseTrial {
  available: boolean
  reason?: 'new_check_item' | 'not_structured' | 'no_run' | 'error'
  detail?: string
  run?: { run_id: string; observed_at: string | null; devices: number; values: number }
  now?: { abnormal: number; undetermined: number }
  after?: { abnormal: number; undetermined: number }
  rules?: Array<{
    field: string
    event_type: string
    text: string
    change: 'same' | 'added' | 'removed'
    now: number
    after: number
  }>
  added_count?: number
  added?: Array<{ device_id: string; entity_key: string; field: string; value: unknown; severity: string; event_type: string }>
  removed_count?: number
  removed?: Array<{ device_id: string; entity_key: string; field: string; value: unknown; severity: string; event_type: string }>
  no_data_fields?: string[]
  /** The same patrol compared with its baseline again: changes found and the events made of them. */
  comparison?: {
    available: boolean
    reason?: 'no_baseline'
    baseline?: { run_id: string; observed_at: string | null }
    now?: CheckItemTrialComparisonCount
    after?: CheckItemTrialComparisonCount
    rules?: CheckItemReleaseTrial['rules']
    added_count?: number
    added?: CheckItemTrialChange[]
    removed_count?: number
    removed?: CheckItemTrialChange[]
  }
}

export interface CheckItemTrialComparisonCount {
  changes: number
  events: { fault: number; recovery: number; change: number }
}

/** One change between two patrols and the event a rule makes of it; no event type means it is only counted. */
export interface CheckItemTrialChange {
  device_id: string
  entity_key: string
  field: string
  old: unknown
  new: unknown
  change_type: string
  event_type: string
  event_class: string
  severity: string
}

/** The previous release drafted again as a new version, with what publishing it would switch. */
export interface CheckItemRollbackPreview {
  change_id: string
  check_item_id: string
  current_version: number
  restores_version: number
  new_version: number
  restores_wording: boolean
  impact_digest: string
  preview: CheckItemReleasePreview
}

/** A trial of publishing one check item change: the switch is tried on the server and rolled back. */
export interface CheckItemReleasePreview {
  applicable: boolean
  check_item_id?: string
  from_version?: number | null
  to_version?: number
  releasable: boolean
  blockers: string[]
  fields: Array<{
    canonical_field: string
    change: 'unchanged' | 'changed' | 'added' | 'removed'
    before: string[]
    after: string[]
  }>
  graph?: CheckItemRelations
  trial?: CheckItemReleaseTrial
  release: {
    events_closed?: CheckItemEventTally[]
    rules_activated: number
    rules_retired_without_successor: number
    adapters_repinned: number
    /** Adapters a rule package brought with this version, going into force with it. */
    adapters_brought_in?: number
    runtime_bundle_version_ids: string[]
    adapters: Array<{
      implementation_id: string
      name: string
      repinned: boolean
      in_force?: boolean
      dropped_fields?: string[]
      brought_in?: boolean
      templates?: Array<{ template_id: string; replay: ImportedTemplateReplay | null }>
      /** Commands this platform only compares as text: the imported template is never read for them. */
      text_only_commands?: string[]
      /** What that means and what can be done about it, worded once by the server. */
      text_only_warning?: string | null
    }>
  } | null
}

/** How an imported parser template fared when replayed before it goes into force. */
export interface ImportedTemplateReplay {
  passed: boolean
  reason?: string | null
  samples?: number
  samples_passed?: number
  outputs?: number
  outputs_parsed?: number
  records_differing?: number
}

export interface CheckItemOverview {
  check_item_id: string
  name: string
  version: number
  check_item_version_id: string
  result_mode: string
  status: CheckItemPatrolStatus
  fields: Array<{
    canonical_field: string
    description: string
    use: 'health' | 'change' | 'reference' | 'evidence'
    use_label: string
    lines: string[]
    rules_missing: boolean
    stale_count: number
    provided_by: number | null
    /** Adapters whose device was found not to print this field. */
    absent_by?: number | null
  }>
  structured_adapters: number
  adapters: Array<{
    implementation_id: string
    implementation_version_id: string
    name: string
    scope: string
    command: string
    state: 'ready' | 'previous_version' | 'pending' | 'not_required'
    running_check_item_version: number | null
    fields_provided: number | null
    missing_fields: string[] | null
    /** Of the missing fields, those a trial on this adapter's own outputs did not find. */
    absent_fields?: string[]
    field_trial?: AdapterFieldsState['status']
  }>
  leftover_count: number
  graph?: CheckItemRelations
  /** The version that was in force before this one, if a rollback has something to go back to. */
  rollback_version?: number | null
}

export interface CanonicalRuleCandidates {
  schema_version: 'canonical-rule-candidates/v1'
  check_item_id: string
  check_item_version_id: string
  context_digest: string
  success: boolean
  change_policies: Array<Record<string, unknown>>
  event_rules: Array<Record<string, unknown>>
  blockers: Array<{ code: string; observation_type: string; message: string }>
  input_summary: {
    source: 'canonical_semantics_only'
    sample_count: 0
    parser_template_count: 0
  }
  digest: string
  drafts_created?: boolean
  change_policy_assets?: Array<Record<string, unknown>>
  event_rule_version_ids?: string[]
  bundle_id?: string | null
  bundle_status?: string | null
  rule_job_ids?: string[]
}

export interface CheckItemCatalogCheckItemDeleteResult {
  success: boolean
  check_item_id: string
  status: 'deprecated'
  cancelled_change_count: number
  idempotent_replay: boolean
}

export interface CheckItemCatalogImplementationDeleteResult {
  success: boolean
  implementation_id: string
  status: 'deprecated'
  cancelled_change_count: number
  idempotent_replay: boolean
}

export interface AdapterProjectionCandidateInput {
  check_item_id: string
  parser_template_id: string
  source_record_set: string
  generation_provider?: 'deterministic' | 'llm'
  max_agent_attempts?: number
  proposal?: Record<string, unknown>
}

export interface AdapterProjectionCandidateResult {
  success: boolean
  mode: 'projection_candidate' | 'projection_validate'
  proposal?: Record<string, unknown>
  validation: {
    success: boolean
    errors: string[]
    adapter_spec?: Record<string, unknown>
    projection_manifest?: Record<string, unknown>
  }
  generation_provider?: 'deterministic' | 'llm'
  agent_loop?: Record<string, unknown>
  generation_context: {
    schema_version: string
    selected_check_item: {
      id: string
      version_id: string
      entity_type: string
      identity_fields: string[]
    }
    parser: {
      template_id: string
      source_record_set: string
      source_fields: string[]
    }
    allowed_target_fields: string[]
    required_target_fields: string[]
    observation_targets: Array<Record<string, unknown>>
  }
}

export interface AdapterProjectionPreviewResult {
  success: boolean
  batches: Array<{
    check_item_id: string
    check_item_version_id?: string
    quality_status: string
    records: Array<Record<string, unknown>>
    errors: string[]
    evidence?: Record<string, unknown>
  }>
  observations: Array<Record<string, unknown>>
}

export interface AdapterProjectionReplayAttestation {
  success: boolean
  status: 'passed' | 'complete' | 'ready' | 'failed' | 'blocked' | 'pending'
  collected_sample_count: number
  sample_count: number
  standard_records_verified: boolean
  primary_keys_verified: boolean
  quality_status_verified: boolean
  rule_results_verified: boolean
  evidence_summary_verified: boolean
  evidence_digest?: string
}

export interface AdapterProjectionCutoverRoute {
  route_id: string
  implementation_version_id: string
  check_item_version_ids: string[]
  status: 'ready' | 'blocked'
  success: boolean
  adapter_spec?: Record<string, unknown> | null
  blockers: Array<{ code: string; message: string; [key: string]: unknown }>
  warnings: Array<{ code: string; message: string; [key: string]: unknown }>
  replay_attestation: Partial<AdapterProjectionReplayAttestation>
}

export interface AdapterProjectionCutoverReadiness {
  schema_version: 'adapter-projection-cutover/v1'
  route_count: number
  ready_route_count: number
  blocked_route_count: number
  production_cutover_permitted: boolean
  hard_gate: string
  routes: AdapterProjectionCutoverRoute[]
  blockers: Array<{
    route_id: string
    blockers: Array<{ code: string; message: string; [key: string]: unknown }>
  }>
  digest: string
  inventory_scope?: Record<string, unknown>
  replay_attestation_sources?: Record<string, 'audit' | 'request'>
  recorded_implementation_version_ids?: string[]
  cutover_authorization?: {
    authorization_id: string
    actor: string
    authorized_at?: string
    readiness_digest: string
    matches_current_readiness: boolean
    reason?: string
    [key: string]: unknown
  } | null
}

export interface AdapterProjectionCutoverAuthorizationResult {
  success: boolean
  status: 'authorized'
  authorization: Record<string, unknown>
  readiness: AdapterProjectionCutoverReadiness
}

export interface ParserCommandProfileBinding {
  binding_id: string
  profile_version_id: string
  profile_id: string
  profile_name: string
  profile_version: number
  version_status: string
  selector_json: Record<string, unknown>
  priority: number
  status: string
}

export interface ParserCommandProfileImpactPreview {
  success: boolean
  mode: string
  profile_name: string
  profile_mode?: 'check_items' | 'legacy_commands'
  profile_version_id?: string | null
  selector: Record<string, unknown>
  command_count: number
  check_item_count?: number
  matched_device_count: number
  returned_device_count: number
  blocked_device_count: number
  ready_device_count: number
  eligible_command_count: number
  ready_command_count: number
  blocked_command_count: number
  ready_check_item_count?: number
  blocked_check_item_count?: number
  not_applicable_check_item_count?: number
  coverage_rate: number
  evidence_saved?: boolean
  rows: Array<{
    device_id?: string
    device_name?: string
    ip?: string
    vendor?: string
    model?: string
    family?: string
    site?: string
    role?: string
    groups?: string[]
    command_count: number
    check_item_count?: number
    eligible_count: number
    ready_count: number
    blocked_count: number
    not_applicable_count?: number
    status: string
    violations: Array<Record<string, unknown>>
    plan: Record<string, unknown>
  }>
}

export interface ParserEffectivePlanPreview {
  success: boolean
  plan: {
    profile_name: string
    profile_version_id?: string
    credential_ref?: string | null
    collector_options?: Record<string, unknown>
    policy_source?: string
    binding_id?: string
    selector?: Record<string, unknown>
    check_item_ids?: string[]
    runtime_manifest_id?: string | null
    runtime_manifest_digest?: string | null
    collection_items: Array<{
      command: string
      action: string
      collection_item_id?: string
    }>
  }
  commands: string[]
}

export interface ManagedDevice {
  device_id: string
  device_name: string
  ip?: string
  management_ip?: string
  username?: string
  credential_ref?: string
  vendor?: string
  model?: string
  family?: string
  platform?: string
  device_type?: string
  os_version?: string
  serial_number?: string
  site?: string
  role?: string
  status?: string
  last_connection_status?: string
  last_connection_checked_at?: string | null
  last_connection_failure_code?: string | null
  last_connection_error?: string | null
  last_connection_latency_ms?: number | null
  groups?: string[]
  command_file?: string
  attributes?: Record<string, unknown>
  patrol_readiness?: {
    status: string
    mode: string
    command_count: number
    eligible_count: number
    ready_count: number
    blocked_count: number
    policy_source?: string | null
    profile_name?: string | null
    profile_version_id?: string | null
    violations?: Array<Record<string, unknown>>
    plan_violations?: Array<Record<string, unknown>>
    connection_issues?: Array<Record<string, unknown>>
    runtime_asset_health?: ParserRuntimeRuleAssetHealth
    error?: string
  }
}

export interface DeviceUpsertPayload {
  device_name: string
  management_ip: string
  username?: string
  credential_ref?: string
  vendor?: string
  model?: string
  family?: string
  platform?: string
  device_type?: string
  os_version?: string
  serial_number?: string
  site?: string
  role?: string
  status?: string
  output_path?: string
  command_profile?: string
  attributes?: Record<string, unknown>
  groups?: string[]
}

export interface DeviceMutationResponse {
  success: boolean
  device_id?: string
  device: ManagedDevice
}

export interface DeviceImportRow {
  row_number: number
  valid: boolean
  errors: string[]
  action: 'create' | 'update' | 'invalid'
  existing_device_id?: string
  device: DeviceUpsertPayload
}

export interface DeviceImportPreview {
  success: boolean
  total: number
  create_count: number
  update_count: number
  invalid_count: number
  rows: DeviceImportRow[]
}

export interface DeviceImportApplyResult extends DeviceImportPreview {
  dry_run: boolean
  applied_count: number
  applied: Array<{
    row_number: number
    action: 'create' | 'update'
    device_id: string
    device: ManagedDevice
  }>
  skipped: DeviceImportRow[]
}

export interface DeviceImportPayload {
  content: string
  format?: 'csv' | 'xlsx'
  filename?: string
}

export interface DeviceImportTemplate {
  filename: string
  format: 'csv'
  columns: Array<{
    name: string
    required: boolean
    aliases?: string[]
    description?: string
    example?: string
  }>
  required_columns: string[]
  csv: string
}

export interface DeviceBulkFilters {
  device_id?: string
  device_name?: string
  ip?: string
  group?: string
  vendor?: string
  model?: string
  family?: string
  platform?: string
  device_type?: string
  site?: string
  role?: string
  status?: string
  connection_status?: string
}

export interface DeviceBulkUpdate {
  command_profile?: string
  credential_ref?: string
  status?: string
  platform?: string
  device_type?: string
  site?: string
  role?: string
  groups?: string[]
  group_mode?: 'replace' | 'append'
}

export interface DeviceBulkRequest {
  device_ids?: string[]
  dynamic_group_id?: string
  filters?: DeviceBulkFilters
  update: DeviceBulkUpdate
}

export interface DeviceBulkPreviewRow {
  device_id: string
  device_name?: string
  ip?: string
  current: ManagedDevice
  update: DeviceBulkUpdate
}

export interface DeviceBulkPreview {
  success: boolean
  target_count: number
  changed_fields: string[]
  rows: DeviceBulkPreviewRow[]
}

export interface DeviceBulkApplyResult extends DeviceBulkPreview {
  applied_count: number
  devices: ManagedDevice[]
}

export interface DeviceBulkDeletePreview {
  success: boolean
  target_count: number
  rows: Array<{
    device_id: string
    device_name?: string
    ip?: string
    current: ManagedDevice
  }>
}

export interface DeviceBulkDeleteResult {
  success: boolean
  target_count: number
  deleted_count: number
  devices: ManagedDevice[]
}

export interface DevicePatrolLaunchRequest {
  request_id?: string
  device_ids?: string[]
  dynamic_group_id?: string
  filters?: DeviceBulkFilters
  mode?: string
  save_baseline?: boolean
  ticket_id?: string
  launch_mode?: string
  workflow_name?: string
}

export interface DevicePatrolPreviewRow {
  device_id: string
  device_name?: string
  ip?: string
  device: ManagedDevice
  plan: ParserEffectivePlanPreview['plan']
  commands: string[]
  command_count: number
  coverage_violations?: Array<Record<string, unknown>>
  connection_issues?: Array<Record<string, unknown>>
  runtime_asset_health?: ParserRuntimeRuleAssetHealth
}

export interface PatrolProductionReadinessCheck {
  id: string
  name: string
  scope: string
  status: 'ok' | 'warning' | 'blocked'
  message: string
  remediation: string
  latency_ms: number
  detail: Record<string, unknown>
  blocking: boolean
  failure_code?: string | null
}

export interface PatrolProductionReadiness {
  schema_version: string
  status: 'ready' | 'degraded' | 'blocked'
  can_launch: boolean
  checked_at: string
  workflow_name: string
  launch_mode: string
  blocking_count: number
  warning_count: number
  checks: PatrolProductionReadinessCheck[]
  blockers: PatrolProductionReadinessCheck[]
  warnings: PatrolProductionReadinessCheck[]
}

export interface PatrolTargetQualityReadiness {
  status: 'ready' | 'degraded' | 'not_evaluated'
  can_determine_health: boolean
  warning_device_count: number
  parser_warning_count: number
  runtime_asset_warning_count: number
  message: string
}

export interface DevicePatrolPreview {
  success: boolean
  target_count: number
  ready_count: number
  error_count: number
  mode: string
  save_baseline: boolean
  launch_mode: string
  workflow_name: string
  platform_readiness: PatrolProductionReadiness
  quality_readiness: PatrolTargetQualityReadiness
  rows: DevicePatrolPreviewRow[]
  errors: Array<{
    device_id?: string
    device_name?: string
    error: string
    failure_code?: string
    coverage_violations?: Array<Record<string, unknown>>
    connection_issues?: Array<Record<string, unknown>>
    platform_violations?: Array<Record<string, unknown>>
  }>
}

export interface DevicePatrolLaunchResult {
  success: boolean
  target_count: number
  queued_count: number
  launched_count: number
  dispatch_status: 'queued' | 'pending' | 'dispatching' | 'completed' | 'partial'
  dispatch_task_id?: string
  mode: string
  save_baseline: boolean
  ticket_id?: string
  patrol_batch_id?: string
  launch_mode: string
  workflow_name: string
  skipped_count?: number
  skipped_errors?: DevicePatrolPreview['errors']
  runs: Array<{
    device_id: string
    device_name?: string
    ip?: string
    execution_plan_id?: string
    workflow_run_id: string
    workflow_name: string
    patrol_batch_id?: string
    command_count: number
  }>
  tasks: Array<{
    device_id: string
    device_name?: string
    ip?: string
    execution_plan_id?: string
    celery_task_id: string
    patrol_batch_id?: string
    command_count: number
  }>
}

export interface DeviceListResponse {
  items: ManagedDevice[]
  total: number
  limit: number
  offset: number
}

export interface DeviceDynamicGroup {
  id: string
  name: string
  description?: string | null
  selector: Record<string, unknown>
  status: string
  created_at?: string | null
  updated_at?: string | null
}

export interface DeviceDynamicGroupPreview {
  success: boolean
  group: DeviceDynamicGroup
  total: number
  limit: number
  items: ManagedDevice[]
}

export interface DeviceCredentialReference {
  ref: string
  scheme: string
  name: string
  available: boolean
}

export interface DeviceCredentialInspection {
  ref: string
  scheme?: string | null
  supported: boolean
  available: boolean
  failure_code?: string | null
  message?: string
}

export interface DeviceEffectivePlanPreview {
  success: boolean
  device: ManagedDevice
  plan: ParserEffectivePlanPreview['plan'] & {
    device_id: string
    action: string
    mode: string
  }
  commands: string[]
  parser_coverage?: Array<Record<string, unknown>>
  command_asset_states?: Array<{
    command: string
    template_id?: string | null
    parser_status: string
    projection_status: string
    policy_status: string
    event_rule_status: string
    next_action: string
    focus?: {
      workspace?: string
      subtab?: string
      command?: string
    }
  }>
  coverage_summary?: {
    status: string
    mode: string
    command_count: number
    eligible_count: number
    ready_count: number
    blocked_count: number
    coverage_rate: number
    violations: Array<Record<string, unknown>>
    connection_issues: Array<Record<string, unknown>>
    runtime_asset_health?: ParserRuntimeRuleAssetHealth
  }
}

export interface DeviceConnectivityTestResult {
  success: boolean
  failure_code?: string | null
  device: ManagedDevice
  connection_issues?: Array<Record<string, unknown>>
  device_name?: string
  ip?: string
  device_type?: string
  username?: string
  credential_ref?: string
  prompt?: string
  elapsed_seconds?: number
  error?: string
}

export interface DeviceIdentityDiscoveryResult {
  success: boolean
  identity_status: string
  failure_code?: string | null
  error?: string | null
  device: ManagedDevice
  connection_issues?: Array<Record<string, unknown>>
  identity?: {
    vendor?: string | null
    model?: string | null
    family?: string | null
    confidence?: number
    source?: string | null
    evidence_command?: string | null
    evidence_text?: string | null
    discovered_at?: string | null
  }
  commands?: string[]
  command_outputs?: Array<{
    command: string
    canonical_command?: string
    output_preview?: string
    output_length?: number
  }>
  warnings?: Array<{ code: string; message: string }>
  device_type?: string
  prompt?: string
  elapsed_seconds?: number
}

export interface DatabaseResetResult {
  success: boolean
  dry_run: boolean
  total_rows: number
  table_counts: Record<string, number>
  deleted_tables: string[]
  preserved_tables: string[]
}

export interface DeviceConnectionHealthRecord {
  id: string
  device_id: string
  status: string
  failure_code?: string | null
  error?: string | null
  latency_ms?: number | null
  source: string
  metadata?: Record<string, unknown>
  checked_at?: string | null
}

export interface DeviceConnectionHealthResponse {
  device: ManagedDevice
  items: DeviceConnectionHealthRecord[]
  limit: number
}

export interface DeviceExecutionPlanRecord {
  execution_plan_id: string
  device_id: string
  action: string
  mode: string
  profile_name: string
  profile_version_id?: string | null
  binding_id?: string | null
  policy_source: string
  credential_ref?: string | null
  collector_options?: Record<string, unknown>
  selector?: Record<string, unknown>
  plan?: Record<string, unknown>
  command_count: number
  plan_sha256: string
  status: string
  launch_source?: string | null
  patrol_batch_id?: string | null
  workflow_run_id?: string | null
  celery_task_id?: string | null
  failure_code?: string | null
  error?: string | null
  created_at?: string | null
  launched_at?: string | null
  updated_at?: string | null
}

export interface DeviceExecutionPlanResponse {
  device: ManagedDevice
  items: DeviceExecutionPlanRecord[]
  limit: number
}

export interface DeviceEvidenceTimelineItem {
  execution_status?: string | null
  execution_phase?: string | null
  task_id?: string | null
  execution_started_at?: string | null
  execution_completed_at?: string | null
  execution_error?: string | null
  execution_steps?: Array<{ phase: string; label: string; status: string; error?: string | null }>
  workflow_run_id?: string | null
  workflow_status?: string | null
  workflow_created_at?: string | null
  workflow_completed_at?: string | null
  execution_plan_id?: string | null
  execution_plan?: DeviceExecutionPlanRecord | null
  patrol_batch_id?: string | null
  snapshot_run_id?: string | null
  snapshot_count: number
  observation_count: number
  change_count: number
  event_count: number
  no_event_count?: number
  policy_blocking_count?: number
  policy_blocking_reason?: string | null
  policy_blocking_reasons?: string[]
  no_event_reason?: string | null
  no_event_reasons?: string[]
  evidence_fingerprint?: string | null
  parse_counts: Record<string, number>
  steps: Array<{
    step_name: string
    skill_name: string
    status: string
    started_at?: string | null
    completed_at?: string | null
    error?: string | null
  }>
  snapshots: Array<{
    snapshot_id: string
    command: string
    parse_status: string
    parser_template_id?: string | null
    observation_contract_id?: string | null
    output_status?: string | null
    execution_status?: string | null
    fact_status?: string | null
    normalization_status?: string | null
    raw_text_hash: string
    evidence_fingerprint?: string | null
    observed_at?: string | null
  }>
  observations: Array<{
    observation_id: string
    snapshot_id: string
    entity_type: string
    entity_key: string
    field: string
    observation_type?: string | null
    semantic_type?: string | null
    unit?: string | null
    value?: unknown
    value_text?: string | null
    value_number?: number | null
    confidence?: number | null
    parser_template_id?: string | null
    observation_contract_id?: string | null
    contract_version?: number | null
    observed_at?: string | null
  }>
  changes: Array<{
    change_id: string
    entity_type: string
    entity_key: string
    field: string
    change_type: string
    observation_type?: string | null
    old_value?: unknown
    new_value?: unknown
    old_number?: number | null
    new_number?: number | null
    event_status: string
    collision_reason?: string | null
    change_policy_id?: string | null
    change_policy_version_id?: string | null
    decision_reason?: string | null
    no_event_reason?: string | null
    comparison_type?: string | null
    policy_snapshot?: Record<string, unknown> | null
    rule_bundle_digest?: string | null
    detected_at?: string | null
  }>
  events: Array<{
    event_id: string
    event_type: string
    severity: string
    entity_type: string
    entity_key: string
    status: string
    source_change_id?: string | null
    old_value?: unknown
    new_value?: unknown
    old_number?: number | null
    new_number?: number | null
    change_policy_id?: string | null
    change_policy_version_id?: string | null
    policy_snapshot?: Record<string, unknown> | null
    event_rule_id?: string | null
    event_rule_version?: number | string | null
    occurrence_count?: number
    occurred_at?: string | null
  }>
  event_occurrence_count?: number
  event_occurrences?: Array<{
    occurrence_id: string
    event_id: string
    event_type: string
    occurrence_kind: string
    source_change_id?: string | null
    severity: string
    observed_at?: string | null
    event_rule_id?: string | null
    event_rule_version?: number | string | null
  }>
}

export interface DeviceEvidenceTimelineResponse {
  device: ManagedDevice
  items: DeviceEvidenceTimelineItem[]
  limit: number
}

export type SkillRolloutStatus = 'draft' | 'canary' | 'stable' | 'deprecated'

export interface SkillCatalogStats {
  total: number
  enabled: number
  indexed: number
  memory_cached: number
}

export interface SkillRolloutUpdate {
  rollout_status?: SkillRolloutStatus
  enabled_ratio?: number
  min_platform_version?: string | null
  enabled?: boolean
}

export interface SkillCatalogEntry {
  skill_name: string
  rollout_status: SkillRolloutStatus
  enabled_ratio: number
  min_platform_version?: string | null
  enabled: boolean
  domain?: string
  celery_queue?: string | null
}

export interface SkillArchiveResult {
  success?: boolean
  archived: number
  cutoff?: string
  object_key?: string
  archive_id?: string
  skipped?: boolean
  reason?: string
  error?: string
}

export interface SkillStats {
  total_skills: number
  enabled_skills: number
  disabled_skills: number
  categories: Record<string, number>
  skills?: Array<{ name: string; category: string; enabled: boolean }>
}

export const chatApi = {
  sendMessage: async (data: ChatRequest): Promise<ChatResponse> => {
    const response = await api.post<ChatResponse>('/chat/', data)
    return response.data
  },
  getHealth: async () => {
    const response = await api.get('/health/')
    return response.data
  },
  getDiagnostics: async (): Promise<DiagnosticsResponse> => {
    const response = await api.get<DiagnosticsResponse>('/health/diagnostics/')
    return response.data
  },
  getTaskStatus: async (taskId: string) => {
    const response = await api.get(`/tasks/${taskId}/`)
    return response.data
  }
}

export const conversationApi = {
  createConversation: async (data: { title?: string; user_id?: string } = {}): Promise<Conversation> => {
    const response = await api.post<Conversation>('/conversations/', data)
    return response.data
  },
  getConversations: async (params: { user_id?: string; limit?: number; offset?: number } = {}): Promise<Conversation[]> => {
    const response = await api.get<Conversation[]>('/conversations/', { params })
    return response.data
  },
  getConversation: async (id: string): Promise<ConversationDetail> => {
    const response = await api.get<ConversationDetail>(`/conversations/${id}/`)
    return response.data
  },
  updateConversation: async (id: string, data: { title?: string; status?: string; summary?: string }): Promise<Conversation> => {
    const response = await api.put<Conversation>(`/conversations/${id}/`, data)
    return response.data
  },
  deleteConversation: async (id: string): Promise<void> => {
    await api.delete(`/conversations/${id}/`)
  },
  addMessage: async (conversationId: string, data: {
    role: string
    content: string
    agent_type?: string
    celery_task_id?: string
    download_url?: string
    references?: any[]
  }): Promise<Message> => {
    const response = await api.post<Message>(`/conversations/${conversationId}/messages/`, data)
    return response.data
  },
  summarizeConversation: async (conversationId: string): Promise<{ conversation_id: string; title: string; summary: string }> => {
    const response = await api.post(`/conversations/${conversationId}/summarize/`)
    return response.data
  }
}

export interface AuthUser {
  app_ids?: string[]
  permissions?: string[]
  id: number
  username: string
  email: string
  role: string
  thread_id?: string
}

export interface ManagedUser {
  permissions?: string[]
  netops_configure?: boolean
  id: number
  username: string
  email: string
  role: string
  is_active: boolean
  last_login: string | null
  date_joined: string | null
}

export const authApi = {
  login: async (username: string, password: string) => {
    const response = await api.post('/auth/login/', { username, password })
    return response.data as AuthSession & { user: AuthUser }
  },
  refresh: async (refresh: string) => {
    const response = await api.post('/auth/refresh/', { refresh })
    return response.data as { access: string; refresh?: string }
  },
  logout: async () => {
    const refresh = localStorage.getItem('refresh_token')
    await api.post('/auth/logout/', refresh ? { refresh } : {})
  },
  changePassword: async (old_password: string, new_password: string) => {
    const response = await api.post('/auth/change-password/', { old_password, new_password })
    return response.data as { message: string }
  },
  me: async () => {
    const response = await api.get('/auth/me/')
    return response.data as AuthUser
  },
}

export const userAdminApi = {
  list: async (): Promise<ManagedUser[]> => {
    const response = await api.get<ManagedUser[]>('/auth/users/')
    return response.data
  },
  create: async (data: { username: string; password: string; role: string; email?: string; netops_configure?: boolean }) => {
    const response = await api.post<ManagedUser>('/auth/users/', data)
    return response.data
  },
  update: async (
    id: number,
    data: Partial<Pick<ManagedUser, 'email' | 'role' | 'is_active' | 'netops_configure'>>
  ) => {
    const response = await api.patch<ManagedUser>(`/auth/users/${id}/`, data)
    return response.data
  },
  resetPassword: async (id: number, new_password: string) => {
    const response = await api.post(`/auth/users/${id}/reset-password/`, { new_password })
    return response.data as { message: string }
  },
  delete: async (id: number) => {
    const response = await api.delete(`/auth/users/${id}/`)
    return response.data as { message: string }
  },
}

export const skillApi = {
  list: async (): Promise<SkillItem[]> => {
    const response = await api.get<SkillItem[]>('/skills/')
    return response.data
  },
  getStats: async (): Promise<SkillStats> => {
    const response = await api.get<SkillStats>('/skills/stats/')
    return response.data
  },
  create: async (data: Record<string, unknown>) => {
    const response = await api.post('/skills/', data)
    return response.data
  },
  toggle: async (name: string, enabled: boolean) => {
    const response = await api.patch(`/skills/${name}/toggle/`, { enabled })
    return response.data
  },
  reload: async (name: string) => {
    const response = await api.post(`/skills/${name}/reload/`)
    return response.data
  },
  getContent: async (name: string): Promise<{ name: string; content: string }> => {
    const response = await api.get<{ name: string; content: string }>(`/skills/${name}/content/`)
    return response.data
  },
  saveContent: async (name: string, content: string) => {
    const response = await api.put(`/skills/${name}/content/`, { content })
    return response.data
  },
  listFiles: async (name: string): Promise<{ files: Record<string, string[]> }> => {
    const response = await api.get<{ files: Record<string, string[]> }>(`/skills/${name}/files/`)
    return response.data
  },
  uploadFile: async (
    name: string,
    data: { folder: string; filename: string; file_content: string }
  ) => {
    const response = await api.post(`/skills/${name}/files/`, data)
    return response.data
  },
  delete: async (name: string) => {
    const response = await api.delete(`/skills/${name}/`)
    return response.data
  },
  testRun: async (name: string, params: Record<string, unknown> = {}) => {
    const response = await api.post(`/skills/${name}/test-run/`, { params })
    return response.data
  },
  getSchema: async (name: string): Promise<import('../types/workflowDsl').SkillSchema> => {
    const response = await api.get<import('../types/workflowDsl').SkillSchema>(`/skills/${name}/schema/`)
    return response.data
  },
}

export interface ParserValueMappingRule {
  check_item: string
  vendor: string
  model: string
  command: string
  canonical: string
  aliases: string[]
}

export interface ParserValueMappingConfig {
  version: number
  config_path: string
  scoped_value_mappings: ParserValueMappingRule[]
}

// Compatibility export: implementation is owned by the rule governance package.
export { parserApi, ruleAssetSourceApi } from '../../../../src/apps/rule_governance/frontend/services/api'



export { offlinePatrolApi } from '../../../../src/apps/inspection/frontend/api'

export { deviceApi } from '../../../../src/apps/device_management/frontend/api'

export const maintenanceApi = {
  previewDatabaseReset: async () => {
    const response = await api.get<DatabaseResetResult>('/maintenance/database-reset/preview/')
    return response.data
  },
  resetDatabase: async (data: {
    dry_run?: boolean
    confirmation?: string
    include_configuration?: boolean
  }) => {
    const response = await api.post<DatabaseResetResult>('/maintenance/database-reset/', data)
    return response.data
  },
}

export const skillCatalogApi = {
  getStats: async (): Promise<SkillCatalogStats> => {
    const response = await api.get<SkillCatalogStats>('/skills/catalog/stats/')
    return response.data
  },
  reindex: async (force = false): Promise<{ success: boolean; [key: string]: unknown }> => {
    const response = await api.post(`/skills/catalog/reindex/?force=${force}`)
    return response.data
  },
  updateRollout: async (
    skillName: string,
    data: SkillRolloutUpdate
  ): Promise<{ success: boolean; catalog: SkillCatalogEntry }> => {
    const response = await api.patch<{ success: boolean; catalog: SkillCatalogEntry }>(
      `/skills/catalog/${skillName}/rollout/`,
      data
    )
    return response.data
  },
  archiveExecutions: async (
    beforeDays?: number,
    batchSize?: number
  ): Promise<SkillArchiveResult> => {
    const response = await api.post<SkillArchiveResult>('/skills/governance/archive-executions/', {
      before_days: beforeDays,
      batch_size: batchSize,
    })
    return response.data
  },
}

export interface StorageFolder {
  id: string
  name: string
  parent_id: string | null
  visibility: string
  team_id: string | null
  owner_id: string | null
  created_at?: string
  updated_at?: string
}

export interface StorageFile {
  id: string
  name: string
  folder_id: string | null
  visibility: string
  team_id: string | null
  owner_id: string | null
  content_type: string | null
  size_bytes: number
  created_at?: string
  updated_at?: string
}

export interface StorageListResult {
  folder: StorageFolder | null
  folders: StorageFolder[]
  files: StorageFile[]
  breadcrumb: StorageFolder[]
}

export interface StorageTeam {
  id: string
  name: string
  description: string | null
  role: string | null
  member_count: number
}

export interface StorageTeamMember {
  id: string
  user_id: string
  role: string
  created_at?: string
}

export interface UploadInitResult {
  file_id: string
  object_key: string
  upload_url: string
  expires_in: number
}

export const storageApi = {
  listTeams: async (): Promise<StorageTeam[]> => {
    const response = await api.get<StorageTeam[]>('/storage/teams/')
    return response.data
  },
  createTeam: async (data: { name: string; description?: string }) => {
    const response = await api.post('/storage/teams/', data)
    return response.data
  },
  deleteTeam: async (teamId: string) => {
    const response = await api.delete(`/storage/teams/${teamId}/`)
    return response.data
  },
  listTeamMembers: async (teamId: string): Promise<StorageTeamMember[]> => {
    const response = await api.get<StorageTeamMember[]>(`/storage/teams/${teamId}/members/`)
    return response.data
  },
  addTeamMember: async (teamId: string, data: { user_id: string; role?: string }) => {
    const response = await api.post(`/storage/teams/${teamId}/members/`, data)
    return response.data
  },
  removeTeamMember: async (teamId: string, userId: string) => {
    const response = await api.delete(`/storage/teams/${teamId}/members/${userId}/`)
    return response.data
  },
  updateTeamMemberRole: async (teamId: string, userId: string, role: string) => {
    const response = await api.patch(`/storage/teams/${teamId}/members/${userId}/`, { role })
    return response.data
  },
  list: async (params: { folder_id?: string; visibility?: string; team_id?: string }): Promise<StorageListResult> => {
    const response = await api.get<StorageListResult>('/storage/list/', { params })
    return response.data
  },
  folderTree: async (params: { visibility?: string; team_id?: string }) => {
    const response = await api.get('/storage/folders/tree/', { params })
    return response.data
  },
  createFolder: async (data: {
    name: string
    parent_id?: string | null
    visibility?: string
    team_id?: string | null
  }) => {
    const response = await api.post('/storage/folders/', data)
    return response.data
  },
  deleteFolder: async (folderId: string) => {
    const response = await api.delete(`/storage/folders/${folderId}/`)
    return response.data
  },
  renameFolder: async (folderId: string, name: string) => {
    const response = await api.patch(`/storage/folders/${folderId}/`, { name })
    return response.data
  },
  moveFolder: async (folderId: string, targetFolderId: string) => {
    const response = await api.post(`/storage/folders/${folderId}/move/`, { target_folder_id: targetFolderId })
    return response.data
  },
  uploadInit: async (data: {
    filename: string
    folder_id?: string | null
    visibility?: string
    team_id?: string | null
    content_type?: string
    size_bytes?: number
  }): Promise<UploadInitResult> => {
    const response = await api.post<UploadInitResult>('/storage/upload/init/', data)
    return response.data
  },
  uploadComplete: async (data: { file_id: string; size_bytes?: number }) => {
    const response = await api.post('/storage/upload/complete/', data)
    return response.data
  },
  download: async (fileId: string): Promise<{ download_url: string; filename: string }> => {
    const response = await api.get<{ download_url: string; filename: string }>(`/storage/files/${fileId}/download/`)
    return response.data
  },
  /** 经 BFF 代理读取文件内容，避免浏览器直连 MinIO（CORS / HEAD 403） */
  fetchContent: async (fileId: string, disposition: 'inline' | 'attachment' = 'inline'): Promise<Blob> => {
    const response = await api.get(`/storage/files/${fileId}/content/`, {
      responseType: 'blob',
      params: { disposition },
      timeout: 300000,
    })
    return response.data as Blob
  },
  deleteFile: async (fileId: string) => {
    const response = await api.delete(`/storage/files/${fileId}/`)
    return response.data
  },
  renameFile: async (fileId: string, name: string) => {
    const response = await api.patch(`/storage/files/${fileId}/`, { name })
    return response.data
  },
  moveFile: async (fileId: string, targetFolderId: string) => {
    const response = await api.post(`/storage/files/${fileId}/move/`, { target_folder_id: targetFolderId })
    return response.data
  },
  copyFile: async (fileId: string, targetFolderId: string, name?: string) => {
    const response = await api.post(`/storage/files/${fileId}/copy/`, {
      target_folder_id: targetFolderId,
      name,
    })
    return response.data
  },
  share: async (data: { file_id: string; team_id: string; target_folder_id?: string }) => {
    const response = await api.post('/storage/share/', data)
    return response.data
  },
  shareFolder: async (data: { folder_id: string; team_id: string; target_folder_id?: string }) => {
    const response = await api.post('/storage/share/folder/', data)
    return response.data
  },
}

export interface AppNotification {
  id: string
  title: string
  body?: string
  level?: string
  payload?: Record<string, unknown>
  workflow_run_id?: string
  thread_id?: string
  read_at?: string | null
  created_at?: string
}

export interface NotificationListResult {
  unread_count: number
  items: AppNotification[]
}

export const notificationApi = {
  list: async (): Promise<NotificationListResult> => {
    const response = await api.get('/notifications/')
    return response.data
  },
  markRead: async (id: string) => {
    const response = await api.post(`/notifications/${id}/read/`)
    return response.data
  },
  clearAll: async () => {
    const response = await api.post('/notifications/clear/')
    return response.data
  },
}

export const wsApi = {
  connectChat: (threadId?: string): WebSocket => {
    return new WebSocket(getChatWebSocketUrl(threadId))
  }
}

// ---------------------------------------------------------------------------
// Workflow API
// ---------------------------------------------------------------------------

export interface WorkflowStepSummary {
  name: string
  label: string
  skill: string
  when?: string | null
}

export interface WorkflowTemplateSummary {
  name: string
  description: string
  version: string
  step_count: number
  steps: WorkflowStepSummary[]
  plugin_dir: string
  has_chat_intent: boolean
  has_webhook: boolean
  test_context?: Record<string, unknown>
  test_required_context?: string[]
}

export type WorkflowPluginStatus = 'draft' | 'review' | 'published' | 'archived'

export interface WorkflowPluginSummary extends WorkflowTemplateSummary {
  status: WorkflowPluginStatus
  current_version: number
  category?: string
  plugin_path?: string
  created_by?: string | null
  updated_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  published_at?: string | null
}

export interface WorkflowPluginVersion {
  id: number
  plugin_name: string
  version: number
  status: string
  change_summary?: string | null
  created_by?: string | null
  created_at?: string | null
  file_keys?: string[]
}

export interface WorkflowVersionDiff {
  plugin_name: string
  version_a: number
  version_b: number
  file_key: string
  diff: string
  has_diff: boolean
}

export interface WorkflowImportBundle {
  format: string
  format_version: string
  name: string
  category: string
  metadata?: Record<string, unknown>
  files: Record<string, string>
}

export interface MarketTemplateSummary {
  id: string
  title: string
  description: string
  category: string
  tags: string[]
  source_plugin_name?: string | null
  featured: boolean
  use_count: number
  created_by?: string | null
  created_at?: string | null
  file_keys: string[]
}

export interface MarketTemplateDetail extends MarketTemplateSummary {
  files: Record<string, string>
}

export interface WorkflowTemplateDetail extends WorkflowTemplateSummary {
  files: Record<string, string | null>
  on_complete?: {
    message: string
    notification?: { title?: string; body?: string; level?: string }
  }
}

export interface WorkflowRunSummary {
  run_id: string
  template_name: string
  ticket_id?: string | null
  source?: string | null
  status: string
  current_step_index: number
  error_message?: string | null
  created_at?: string | null
  completed_at?: string | null
  device_id?: string | null
  device_name?: string | null
  patrol_batch_id?: string | null
  retry_of_run_id?: string | null
  rerun_of_batch_id?: string | null
  rerun_of_run_id?: string | null
}

export interface PatrolBatchSummary {
  patrol_batch_id: string
  workflow_name?: string
  report_purpose?: string
  source?: string
  ticket_id?: string | null
  total: number
  status_counts: Record<string, number>
  failed_count: number
  running_count: number
  completed_count: number
  pending_count: number
  cancelled_count: number
  aggregate_status?: 'active' | 'failed' | 'cancelled' | 'completed' | 'pending' | string
  created_at?: string | null
  completed_at?: string | null
  sample_devices: string[]
  target_type?: 'selected' | 'group' | 'dynamic_group' | 'filters' | string
  target_label?: string
  selector?: Record<string, unknown>
  target_count?: number
  launched_count?: number
  skipped_count?: number
  dispatch_status?: string
  dispatch_status_counts?: Record<string, number>
  dispatch_error?: string | null
  connection_succeeded_count?: number
  connection_failed_count?: number
  connection_pending_count?: number
  connection_unknown_count?: number
  manifest_available?: boolean
  archived?: boolean
  archived_count?: number
  archived_at?: string | null
  archive_reason?: string | null
  archived_by?: string | null
  keep_until?: string | null
}

export interface PatrolBatchPage {
  items: PatrolBatchSummary[]
  total: number
  limit: number
  offset: number
  page: number
  page_size: number
  page_count: number
  legacy_total?: number
}

export interface PatrolBatchGovernanceResult {
  success: boolean
  patrol_batch_id: string
  archived: boolean
  affected_runs: number
  active_run_count?: number
  archived_at?: string | null
  archived_by?: string | null
  restored_at?: string | null
  restored_by?: string | null
  reason?: string | null
  keep_until?: string | null
}

export interface PatrolBatchCancellationResult {
  success: boolean
  patrol_batch_id: string
  status: 'cancelled'
  idempotent: boolean
  cancelled_at: string
  cancelled_by: string
  reason: string
  affected_runs: number
  cancelled_run_count: number
  revoked_task_count: number
  cancelled_device_count: number
  finalization_errors: Array<{ device_id: string; error: string }>
}

export interface PatrolConclusionDimension {
  code: string
  label: string
  basis: string
}

export interface PatrolConclusionDimensions {
  risk_conclusion: PatrolConclusionDimension
  assessment_completeness: PatrolConclusionDimension
  comparison_readiness: PatrolConclusionDimension
  authority: PatrolConclusionDimension
}

export interface PatrolBatchResultSummary {
  patrol_batch_id: string
  run_count: number
  device_count: number
  change_count: number
  event_count: number
  current_finding_count?: number
  check_item_summary?: {
    schema_version: string
    patrol_batch_id: string
    report_status: string
    report_manifest_id?: string | null
    report_manifest_digest?: string | null
    source_digest?: string
    runtime_manifest_id?: string | null
    runtime_manifest_digest?: string | null
    target_manifest_digest?: string | null
    conclusion: {
      code: string
      text: string
      basis: string
      trust: string
      formal: boolean
    }
    conclusion_dimensions?: PatrolConclusionDimensions | null
    coverage: {
      target_device_count: number
      expected_check_count: number
      executed_check_count: number
      assessed_check_count: number
      complete_check_count: number
      partial_check_count: number
      insufficient_check_count: number
    }
    execution_counts: Record<string, number>
    assessment_counts: Record<string, number>
    completeness_counts: Record<string, number>
    comparison_counts: Record<string, number>
    blocking_reason_counts: Record<string, number>
  }
  assessment?: {
    code: string
    status: string
    trust: string
    basis: string
    conclusion_allowed: boolean
  }
  risk_summary?: {
    total: number
    by_severity: Record<string, number>
    by_type: Record<string, number>
  }
  current_state_rule_coverage?: {
    available?: boolean
    active_fault_rule_count?: number
    latest_observation_count?: number
    evaluated_observation_count?: number
    evaluation_rate?: number
    finding_count?: number
    sources?: Record<string, number>
    bundle_digests?: string[]
    errors?: string[]
  }
  status_counts: Record<string, number>
  pipeline?: {
    total_devices: number
    stage_count: number
    bottleneck_stage_key?: string | null
    bottleneck_stage_label?: string | null
    stages: Array<{
      key: string
      step_index: number
      step_name: string
      skill_name?: string
      label: string
      description: string
      status: string
      total: number
      status_counts: Record<string, number>
      completed_count: number
      failed_count: number
      active_count: number
      cancelled_count?: number
      pending_count: number
      completion_rate: number
      started_at?: string | null
      completed_at?: string | null
      duration: {
        sample_count: number
        average_seconds?: number | null
        p95_seconds?: number | null
        max_seconds?: number | null
      }
      error_examples: Array<{
        device_id: string
        device_name: string
        message: string
      }>
    }>
  }
  event_types: Record<string, number>
  severities: Record<string, number>
  quality?: {
    status?: string
    command_count?: number
    execution_success_count?: number
    parser_eligible_count?: number
    parser_ready_count?: number
    parser_ready_rate?: number
    observation_count?: number
  }
  no_event_summary?: {
    total?: number
    by_reason?: Record<string, number>
    missing_rule_count?: number
    evaluated_no_match_count?: number
    unmatched_rule_count?: number
  }
  runs: Array<{
    run_id: string | null
    task_id?: string | null
    execution_kind?: 'inspection' | 'workflow'
    execution_phase?: string | null
    device_id?: string | null
    device_name?: string | null
    status: string
    snapshot_run_id?: string | null
    change_count: number
    event_count: number
    error_message?: string | null
  }>
  sample_events: Array<Record<string, unknown>>
  sample_changes: Array<Record<string, unknown>>
  sample_current_findings?: Array<Record<string, unknown>>
  deliverables: {
    csv_url: string
    report_url: string
    report_json_url?: string
    technical_report_url?: string
    technical_workbook_url?: string
    execution_ledger_url?: string
    raw_cli_url?: string
    raw_data_url?: string
    events_url?: string
    evidence_package_url?: string
  }
}

export interface PatrolBatchCheckResult {
  execution_id: string
  assessment_id?: string | null
  device_id: string
  device_name?: string | null
  management_ip?: string | null
  check_item_id: string
  check_item_name: string
  check_item_category: string
  check_item_version_id: string
  result_mode: string
  assessment_mode: string
  report_projection: string
  implementation_version_id?: string | null
  execution_status: string
  assessment_status: string
  completeness_status: string
  comparison_status: string
  evidence_qualification_id?: string | null
  coverage_decision_id?: string | null
  source_identity_status: string
  output_quality_status: string
  freshness_status: string
  empty_result_classification: string
  coverage_status: string
  coverage_basis: string
  risk_verification_status: string
  expected_entity_count?: number | null
  blocking_reason_codes: string[]
  observation_count: number
  observed_entity_count: number
  evaluated_entity_count: number
  change_count: number
  event_count: number
  current_finding_count: number
  runtime_bundle_version_id?: string | null
  runtime_bundle_digest?: string | null
  assessment_policy_snapshot_digest?: string | null
  assessment_basis_digest?: string | null
  assessment_basis_summary?: {
    entry_count: number
    source_counts: Record<string, number>
    status_counts: Record<string, number>
  }
  comparison_context_id?: string | null
  observation_min_at?: string | null
  observation_max_at?: string | null
  evidence_digest?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface PatrolBatchCheckResultPage {
  patrol_batch_id: string
  total: number
  limit: number
  offset: number
  pagination_mode?: 'offset' | 'cursor'
  next_cursor?: string | null
  has_more?: boolean
  items: PatrolBatchCheckResult[]
}

export interface PatrolBatchCheckResultDetail extends PatrolBatchCheckResult {
  schema_version: 'patrol-check-result-detail/v1'
  assessment_policy_snapshot: Record<string, any>
  assessment_basis: Record<string, any>
  assessment_basis_entries: {
    total: number
    limit: number
    offset: number
    has_more: boolean
  }
  evidence_refs: string[]
  integrity: {
    policy_snapshot_present: boolean
    policy_snapshot_valid: boolean
    assessment_basis_present: boolean
    assessment_basis_valid: boolean
    policy_snapshot_digest_matches: boolean
    assessment_basis_digest_matches: boolean
  }
}

export type PatrolReportReleaseStatus =
  | 'facts_not_final'
  | 'pending_content_acceptance'
  | 'pending_signoff'
  | 'released'

export type PatrolReportContentReviewStatus =
  | 'not_started'
  | 'in_review'
  | 'revision_required'
  | 'accepted'

export type PatrolReportContentReviewItemStatus =
  | 'pending'
  | 'accepted'
  | 'rejected'

export interface PatrolReportContentMachineCheck {
  id: string
  passed: boolean
  detail: Record<string, unknown>
}

export interface PatrolReportContentMachineValidation {
  schema_version: string
  passed: boolean
  checks: PatrolReportContentMachineCheck[]
  failed_check_ids: string[]
  digest: string
}

export interface PatrolReportContentReview {
  id: string
  report_manifest_id: string
  patrol_batch_id: string
  schema_version: string
  status: Exclude<PatrolReportContentReviewStatus, 'not_started'>
  source_facts_digest: string
  claim_ledger_digest: string
  artifact_sha256_json: Record<string, string>
  review_plan_digest: string
  machine_validation_json: PatrolReportContentMachineValidation
  machine_validation_digest: string
  required_item_count: number
  accepted_item_count: number
  rejected_item_count: number
  pending_item_count: number
  can_accept: boolean
  required_confirmations_json: string[]
  accepted_confirmations_json?: string[] | null
  created_by: string
  accepted_by?: string | null
  accepted_at?: string | null
  acceptance_comment?: string | null
  acceptance_digest?: string | null
  created_at: string
  updated_at: string
}

export interface PatrolReportContentReviewPreview {
  review_status: PatrolReportContentReviewStatus
  review: PatrolReportContentReview | null
  machine_validation: PatrolReportContentMachineValidation
  review_plan_digest?: string
  required_item_count?: number
  category_counts?: Record<string, number>
  sampling_rule?: {
    ordinary_fact_sample_size_per_stratum: number
    stratum_dimensions: string[]
    selection: string
  }
  can_start?: boolean
}

export interface PatrolReportContentReviewItem {
  id: string
  review_id: string
  review_item_id: string
  category: string
  review_reasons_json: string[]
  stratum_key?: string | null
  fact_type: string
  fact_id: string
  device_id?: string | null
  check_item_id?: string | null
  severity?: string | null
  conclusion_status?: string | null
  evidence_refs_json: string[]
  summary_json: Record<string, unknown>
  review_status: PatrolReportContentReviewItemStatus
  reviewed_by?: string | null
  reviewed_at?: string | null
  review_comment?: string | null
}

export interface PatrolReportContentReviewItemPage {
  review: PatrolReportContentReview
  total: number
  page: number
  page_size: number
  items: PatrolReportContentReviewItem[]
}

export interface PatrolReportReleaseView {
  success?: boolean
  report_manifest_id: string
  patrol_batch_id: string
  version: number
  superseded?: boolean
  report_purpose?: 'production_current'
    | 'offline_historical'
    | 'shadow_validation'
    | 'parser_validation'
    | 'rule_asset_replay'
    | string
  report_purpose_label?: string
  report_purpose_disclosure?: string
  current_state_authoritative?: boolean
  report_authority_context?: {
    schema_version?: string
    report_purpose?: string
    source_kind?: string
    patrol_mode?: string | null
    launch_mode?: string | null
    workflow_name?: string | null
    target_type?: string | null
    observation_min_at?: string | null
    observation_max_at?: string | null
    current_state_eligible?: boolean
    reason_codes?: string[]
    digest?: string
  }
  conclusion_permission?: {
    schema_version?: string
    terminal_truth_reconciled?: boolean
    current_conclusion_ready?: boolean
    evaluated_scope_no_abnormality_allowed?: boolean
    overall_healthy_allowed?: boolean
    counts?: {
      health_target_pair_count?: number
      evaluated_health_target_pair_count?: number
      healthy?: number
      at_risk?: number
      unable_to_determine?: number
      not_applicable?: number
      informational?: number
      unresolved_in_scope_risk_case_count?: number
      unresolved_evaluated_scope_risk_case_count?: number
    }
    blocker_reason_codes?: string[]
    blocker_ref_digest?: string
    input_fact_digest?: string
    decision_digest?: string
  }
  facts_status: 'draft' | 'partial' | 'final' | 'superseded' | string
  release_status: PatrolReportReleaseStatus
  report_cutoff_at?: string | null
  fact_ingestion_high_watermark?: string | null
  late_fact_count?: number
  source_facts_digest: string
  claim_ledger_digest?: string
  claim_count?: number
  raw_evidence_set_digest?: string
  qualification_fact_digest: string
  coverage_fact_digest: string
  artifact_sha256: Record<string, string>
  artifact_object_keys: Record<string, string>
  required_confirmations: Array<
    'facts_reviewed'
    | 'unknown_scope_disclosed'
    | 'artifact_digests_verified'
  >
  content_review_status: PatrolReportContentReviewStatus
  content_review?: PatrolReportContentReview | null
  required_content_confirmations: Array<
    'machine_reconciliation_reviewed'
    | 'critical_unknown_and_lifecycle_reviewed'
    | 'sampled_facts_reviewed'
    | 'traceability_verified'
    | 'narrative_readability_accepted'
    | 'actions_actionable'
  >
  conclusion?: {
    claim_id?: string | null
    code?: string
    text?: string
    basis?: string
    trust?: string
    formal?: boolean
  }
  conclusion_dimensions?: PatrolConclusionDimensions
  coverage?: {
    claim_id?: string | null
    target_device_count?: number
    expected_check_count?: number
    executed_check_count?: number
    assessed_check_count?: number
    complete_check_count?: number
    partial_check_count?: number
    insufficient_check_count?: number
  }
  risk_verification_counts?: {
    confirmed_current?: number
    not_reverified?: number
    current_confirmed?: number
    historical_not_reverified?: number
    recovery_pending?: number
    resolved?: number
    [key: string]: number | undefined
  }
  blocking_reason_counts?: Record<string, number>
  release?: {
    id: string
    status: 'released'
    release_digest: string
    signed_by: string
    signed_at: string
    signoff_comment: string
    disclosure_confirmations_json: string[]
    artifact_sha256_json: Record<string, string>
    content_review_id?: string | null
    content_acceptance_digest?: string | null
  } | null
}

export interface PatrolReportClaim {
  id: string
  report_manifest_id: string
  patrol_batch_id: string
  claim_code: string
  claim_scope: 'network' | 'site' | 'device_group' | 'device' | 'check_item' | 'entity'
  assertion_level: 'observed' | 'assessed' | 'current' | 'historical' | 'limited'
  allowed_phrase_code: string
  subject_json: Record<string, unknown>
  fact_refs_json: string[]
  evidence_refs_json: string[]
  limitation_refs_json: string[]
  authority_context_digest: string
  source_facts_digest: string
  claim_digest: string
  created_at: string
}

export interface PatrolReportClaimPage {
  report_manifest_id: string
  patrol_batch_id: string
  claim_ledger_digest: string
  count: number
  total: number
  page: number
  page_size: number
  items: PatrolReportClaim[]
}

export interface PatrolReportDownload {
  blob: Blob
  filename: string
  status?: PatrolReportReleaseStatus
}

export type ProductionFeedbackType =
  | 'false_positive'
  | 'missed_detection'
  | 'wrong_severity'
  | 'wrong_scope'
  | 'expected_behavior'
  | 'evidence_quality_issue'

export type ProductionFeedbackStatus =
  | 'submitted'
  | 'triaged'
  | 'work_item_created'
  | 'closed'
  | 'rejected'

export type ProductionFeedbackWorkItemKind =
  | 'rule_sample'
  | 'assessment_policy'
  | 'data_quality'

export interface ProductionFeedbackWorkItem {
  id: string
  feedback_id: string
  work_item_kind: ProductionFeedbackWorkItemKind
  status: 'open' | 'candidate_linked' | 'completed' | 'cancelled'
  action_payload_json: Record<string, unknown>
  action_digest: string
  candidate_ref_json?: Record<string, unknown> | null
  candidate_ref_digest?: string | null
  created_by: string
  linked_by?: string | null
  created_at: string
  linked_at?: string | null
}

export interface ProductionFeedback {
  id: string
  feedback_type: ProductionFeedbackType
  status: ProductionFeedbackStatus
  triage_outcome?: 'confirmed' | 'not_confirmed' | 'inconclusive' | null
  execution_id: string
  assessment_id: string
  event_occurrence_id?: string | null
  risk_occurrence_id?: string | null
  risk_episode_id?: string | null
  patrol_batch_id: string
  device_id: string
  check_item_version_id: string
  runtime_bundle_version_id?: string | null
  runtime_bundle_digest?: string | null
  evidence_digest: string
  source_context_json: Record<string, unknown>
  statement: string
  reported_by: string
  triaged_by?: string | null
  triage_reason?: string | null
  work_item?: ProductionFeedbackWorkItem | null
  created_at: string
  triaged_at?: string | null
}

export interface ProductionFeedbackPage {
  items: ProductionFeedback[]
  total: number
  page: number
  page_size: number
}

export interface ProductionFeedbackContext {
  execution_id: string
  assessment_id?: string | null
  patrol_batch_id: string
  device_id: string
  check_item_version_id: string
  runtime_bundle_version_id?: string | null
  runtime_bundle_digest?: string | null
  evidence_digest?: string | null
  feedback_ready: boolean
  events: Array<{
    occurrence_id: string
    event_id: string
    event_type: string
    severity: string
    occurrence_kind: string
    observed_at: string
  }>
  risks: Array<{
    occurrence_id: string
    risk_case_id: string
    risk_episode_id: string
    occurrence_kind: string
    status_after: string
    observed_at: string
  }>
}

export type RiskCaseStatus =
  | 'open'
  | 'acknowledged'
  | 'recovering'
  | 'resolved'
  | 'suppressed'

export type RiskSeverity =
  | 'info'
  | 'warning'
  | 'minor'
  | 'major'
  | 'critical'

export type RiskRemediationStatus =
  | 'not_started'
  | 'planned'
  | 'in_progress'
  | 'completed'
  | 'accepted'
  | 'cancelled'

export type RiskClosureStatus =
  | 'open'
  | 'technical_recovered'
  | 'pending_remediation'
  | 'pending_acceptance'
  | 'ready_to_close'
  | 'closed'
  | 'suppressed'

export type RiskVerificationStatus =
  | 'confirmed_current'
  | 'recovery_pending'
  | 'not_reverified'
  | 'resolved'

export interface RiskCase {
  id: string
  correlation_key: string
  check_item_id: string
  check_item_name?: string | null
  device_id: string
  device_name?: string | null
  management_ip?: string | null
  entity_type: string
  entity_key: string
  correlation_dimensions_json?: Record<string, unknown> | null
  risk_type: string
  severity: RiskSeverity
  status: RiskCaseStatus
  episode_id: string
  first_seen_at: string
  last_seen_at: string
  last_successful_evaluation_at?: string | null
  verification_status: RiskVerificationStatus
  last_verified_at?: string | null
  last_verification_execution_id?: string | null
  resolved_at?: string | null
  closed_at?: string | null
  closed_by?: string | null
  closure_reason?: string | null
  closure_disposition?: 'remediation_accepted' | 'no_remediation_required' | null
  closure_evidence_refs_json?: string[] | null
  closure_status: RiskClosureStatus
  source_finding_ids_json: string[]
  source_event_ids_json: string[]
  owner?: string | null
  remediation_status: RiskRemediationStatus
  due_at?: string | null
  resolution_evidence_refs_json?: string[] | null
  age_days: number
  overdue: boolean
  created_at: string
  updated_at: string
}

export interface RiskOccurrence {
  id: string
  risk_case_id: string
  risk_episode_id?: string | null
  occurrence_kind: string
  source_finding_id?: string | null
  source_event_id?: string | null
  patrol_batch_id?: string | null
  observed_at: string
  status_after: RiskCaseStatus
  actor?: string | null
  reason?: string | null
  evidence_refs_json?: string[] | null
}

export interface RiskRemediation {
  id: string
  risk_case_id: string
  status: Exclude<RiskRemediationStatus, 'not_started'>
  owner?: string | null
  action_text: string
  due_at?: string | null
  evidence_refs_json?: string[] | null
  created_by?: string | null
  completed_by?: string | null
  accepted_by?: string | null
  created_at: string
  completed_at?: string | null
  accepted_at?: string | null
  acceptance_reason?: string | null
  acceptance_evidence_refs_json?: string[] | null
  updated_at: string
}

export interface RiskRecoveryCandidate {
  id: string
  kind: 'recovery_event' | 'healthy_assessment'
  eligible: boolean
  blocker_codes: string[]
  execution_id?: string | null
  assessment_id?: string | null
  patrol_batch_id?: string | null
  source_change_id?: string | null
  source_event_id?: string | null
  event_occurrence_id?: string | null
  event_type?: string | null
  recovery_rule_version_ids: string[]
  recovered_at?: string | null
  evidence_refs: string[]
  source_event_status?: string | null
}

export interface RiskCaseDetail extends RiskCase {
  occurrences: RiskOccurrence[]
  occurrence_pagination: {
    page: number
    page_size: number
    total: number
    page_count: number
  }
  remediations: RiskRemediation[]
  findings: Array<Record<string, unknown>>
  recovery_candidates: RiskRecoveryCandidate[]
  recovery_confirmation: {
    state: 'ready' | 'blocked' | 'not_pending' | 'not_applicable'
    eligible_candidate_count: number
    candidate_count: number
    blocker_codes: string[]
  }
  closure: {
    state: RiskClosureStatus | 'blocked'
    eligible: boolean
    allowed_dispositions: Array<
      'remediation_accepted' | 'no_remediation_required'
    >
    blocker_codes: string[]
    current_episode_id: string
    current_remediation_count: number
    accepted_remediation_count: number
    closed_at?: string | null
    closed_by?: string | null
    closure_reason?: string | null
    closure_disposition?:
      | 'remediation_accepted'
      | 'no_remediation_required'
      | null
    closure_evidence_refs: string[]
  }
}

export interface RiskCasePage {
  items: RiskCase[]
  page: number
  page_size: number
  total: number
  page_count: number
  summary: {
    total: number
    active: number
    resolved: number
    suppressed: number
    overdue: number
    by_status: Record<string, number>
    by_severity: Record<string, number>
    by_remediation_status: Record<string, number>
  }
}

export type RiskEpisodeAssociationStatus =
  | 'pending_review'
  | 'approved'
  | 'rejected'
  | 'revoked'

export type RiskEpisodeAssociationRelation =
  | 'same_root_cause'
  | 'causal_symptom'
  | 'shared_incident'

export interface RiskEpisodeAssociationRiskSummary {
  id: string
  episode_id: string
  check_item_id: string
  device_id: string
  entity_type: string
  entity_key: string
  risk_type: string
  severity: RiskSeverity
  status: RiskCaseStatus
}

export interface RiskEpisodeAssociation {
  id: string
  primary_risk_case_id: string
  primary_risk_episode_id: string
  related_risk_case_id: string
  related_risk_episode_id: string
  relation_type: RiskEpisodeAssociationRelation
  status: RiskEpisodeAssociationStatus
  source_type: 'manual' | 'approved_policy'
  policy_ref_json?: Record<string, unknown> | null
  evidence_refs_json: string[]
  reason: string
  proposed_by: string
  reviewed_by?: string | null
  review_reason?: string | null
  reviewed_at?: string | null
  revoked_by?: string | null
  revoke_reason?: string | null
  revoked_at?: string | null
  created_at: string
  updated_at: string
  effective: boolean
  stale: boolean
  primary_risk?: RiskEpisodeAssociationRiskSummary | null
  related_risk?: RiskEpisodeAssociationRiskSummary | null
}

export interface RiskEpisodeAssociationPage {
  items: RiskEpisodeAssociation[]
  page: number
  page_size: number
  total: number
  page_count: number
}

export interface PatrolReportVersionPage {
  patrol_batch_id: string
  total: number
  page: number
  page_size: number
  items: PatrolReportReleaseView[]
}

export interface PatrolReportMaterializationStatus {
  success?: boolean
  accepted?: boolean
  reason?: 'already_in_progress' | 'idempotent_replay' | string
  request_id?: string
  idempotency_key?: string
  task_id?: string
  requested_by?: string
  patrol_batch_id: string
  status:
    | 'not_requested'
    | 'queued'
    | 'generating'
    | 'completed'
    | 'failed'
    | string
  requested_at?: string | null
  completed_at?: string | null
  error?: string | null
  report_manifest_id?: string | null
  report_manifest_digest?: string | null
}

export interface PatrolBatchDeviceItem {
  device_id: string
  device_name?: string | null
  management_ip?: string | null
  selection_status: 'launched' | 'skipped' | string
  preflight_status: 'ready' | 'failed' | string
  connection_status: 'succeeded' | 'failed' | 'pending' | 'not_attempted' | 'unknown' | string
  workflow_run_id?: string | null
  workflow_status?: string | null
  celery_task_id?: string | null
  snapshot_run_id?: string | null
  execution_status?: string | null
  execution_phase?: string | null
  failure_code?: string | null
  error_message?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export interface PatrolBatchDevicePage {
  patrol_batch_id: string
  target_type: string
  target_label: string
  selector: Record<string, unknown>
  target_count: number
  launched_count: number
  skipped_count: number
  total: number
  limit: number
  offset: number
  items: PatrolBatchDeviceItem[]
}

export interface WorkflowStepDetail {
  step_index: number
  step_name: string
  skill_name: string
  status: string
  celery_task_id?: string | null
  output_artifacts?: Record<string, unknown> | null
  error_message?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface WorkflowTimelineEvent {
  run_id: string
  step_name?: string | null
  skill_name?: string | null
  status: string
  message?: string
  timestamp?: string
  [key: string]: unknown
}

export interface WorkflowChildRunSummary {
  run_id: string
  template_name: string
  status: string
  error_message?: string | null
}

export interface WorkflowRunDetail {
  run_id: string
  template_name: string
  ticket_id?: string | null
  source?: string | null
  status: string
  current_step_index: number
  error_message?: string | null
  context?: Record<string, unknown> | null
  steps: WorkflowStepDetail[]
  timeline?: WorkflowTimelineEvent[]
  child_runs?: WorkflowChildRunSummary[]
  langfuse_trace_id?: string | null
  langfuse_url?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export interface ValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

export interface ChatIntentPreviewResult {
  matched: boolean
  reason?: string
  workflow?: string
  ticket_id?: string | null
  active_steps?: string
  description?: string
  candidates?: Array<{ workflow: string; score: [number, number, number, string] }>
}

export const workflowApi = {
  listTemplates: async (): Promise<WorkflowTemplateSummary[]> => {
    const response = await api.get<WorkflowTemplateSummary[]>('/workflows/templates/')
    return response.data
  },
  getTemplate: async (name: string): Promise<WorkflowTemplateDetail> => {
    const response = await api.get<WorkflowTemplateDetail>(`/workflows/templates/${name}/`)
    return response.data
  },
  getTemplateDsl: async (name: string): Promise<{
    success: boolean
    dsl: import('../types/workflowDsl').WorkflowDSL
    chat_intent_yaml: string
    webhook_yaml: string
  }> => {
    const response = await api.get(`/workflows/templates/${name}/dsl/`)
    return response.data
  },
  reload: async () => {
    const response = await api.post('/workflows/reload/')
    return response.data
  },
  validate: async (data: { workflow_yaml: string; chat_intent_yaml?: string }): Promise<ValidationResult> => {
    const response = await api.post<ValidationResult>('/workflows/validate/', data)
    return response.data
  },
  saveTemplate: async (data: { name: string; category: string; files: Record<string, string> }) => {
    const response = await api.post('/workflows/templates/', data)
    return response.data
  },
  updateTemplate: async (name: string, data: { name: string; category: string; files: Record<string, string> }) => {
    const response = await api.put(`/workflows/templates/${name}/`, data)
    return response.data
  },
  previewChatIntent: async (data: {
    query: string
    workflow_name?: string
    chat_intent_yaml?: string
    context?: Record<string, unknown>
  }): Promise<ChatIntentPreviewResult> => {
    const response = await api.post<ChatIntentPreviewResult>('/workflows/chat-intent/preview/', data)
    return response.data
  },
  listRuns: async (params?: {
    limit?: number
    template_name?: string
    ticket_id?: string
    source?: string
    status?: string
    device_id?: string
    patrol_batch_id?: string
  }): Promise<WorkflowRunSummary[]> => {
    const response = await api.get<WorkflowRunSummary[]>('/workflows/runs/', { params })
    return response.data
  },
  listPatrolBatches: inspectionBatchApi.listPatrolBatches,
  archivePatrolBatch: inspectionBatchApi.archivePatrolBatch,
  restorePatrolBatch: inspectionBatchApi.restorePatrolBatch,
  cancelPatrolBatch: inspectionBatchApi.cancelPatrolBatch,
  getPatrolBatchSummary: inspectionBatchApi.getPatrolBatchSummary,
  listPatrolBatchCheckResults: inspectionBatchApi.listPatrolBatchCheckResults,
  getPatrolBatchCheckResultDetail: inspectionBatchApi.getPatrolBatchCheckResultDetail,
  listPatrolBatchDevices: inspectionBatchApi.listPatrolBatchDevices,
  retryPatrolBatch: inspectionBatchApi.retryPatrolBatch,
  rerunPatrolBatch: inspectionBatchApi.rerunPatrolBatch,
  exportPatrolBatch: inspectionBatchApi.exportPatrolBatch,
  exportPatrolBatchReport: inspectionBatchApi.exportPatrolBatchReport,
  exportPatrolBatchTechnicalWorkbook: inspectionBatchApi.exportPatrolBatchTechnicalWorkbook,
  exportPatrolBatchEvidencePackage: inspectionBatchApi.exportPatrolBatchEvidencePackage,
  exportPatrolBatchRawData: inspectionBatchApi.exportPatrolBatchRawData,
  exportPatrolBatchRawCli: inspectionBatchApi.exportPatrolBatchRawCli,
  exportPatrolBatchEvents: inspectionBatchApi.exportPatrolBatchEvents,
  getRun: async (runId: string): Promise<WorkflowRunDetail> => {
    const response = await api.get<WorkflowRunDetail>(`/workflows/${runId}/`)
    return response.data
  },
  retryRun: async (runId: string): Promise<{ success: boolean; run_id: string; retry_of_run_id: string; run?: WorkflowRunDetail }> => {
    const response = await api.post(`/workflows/${runId}/retry/`, {})
    return response.data
  },
  getRunTimeline: async (runId: string) => {
    const response = await api.get<{
      run_id: string
      status: string
      events: WorkflowTimelineEvent[]
      langfuse_trace_id?: string
      langfuse_url?: string
    }>(`/workflows/${runId}/timeline/`)
    return response.data
  },
  getRunEventsStreamUrl: (runId: string) => `${API_BASE_URL}/workflows/${runId}/events/stream/`,
  testRun: async (data: { template_name: string; context: Record<string, unknown> }) => {
    const response = await api.post('/workflows/runs/test/', data)
    return response.data
  },
  preview: async (data: { dsl: import('../types/workflowDsl').WorkflowDSL; options?: import('../types/workflowDsl').GenerateOptions }) => {
    const response = await api.post<import('../types/workflowDsl').WorkflowPreviewResult>('/workflows/preview/', data)
    return response.data
  },
  generate: async (data: {
    dsl: import('../types/workflowDsl').WorkflowDSL
    options?: import('../types/workflowDsl').GenerateOptions
  }) => {
    const response = await api.post<import('../types/workflowDsl').WorkflowPreviewResult>('/workflows/generate/', data)
    return response.data
  },
  inferMappings: async (dsl: import('../types/workflowDsl').WorkflowDSL) => {
    const response = await api.post<{ suggestions: Array<{ step_name: string; skill: string; suggested_inputs: Record<string, string> }> }>(
      '/workflows/infer-mappings/',
      { dsl },
    )
    return response.data
  },
  listCategories: async (): Promise<string[]> => {
    const response = await api.get<{ categories: string[] }>('/workflows/categories/')
    return response.data.categories
  },
  previewExpressionHints: async (data: {
    dsl: import('../types/workflowDsl').WorkflowDSL
    step_name?: string
    skill?: string
  }) => {
    const response = await api.post('/workflows/expression-hints/preview/', data)
    return response.data
  },
  listPlugins: async (): Promise<WorkflowPluginSummary[]> => {
    const response = await api.get<WorkflowPluginSummary[]>('/workflows/plugins/')
    return response.data
  },
  listPluginVersions: async (name: string, limit = 50): Promise<WorkflowPluginVersion[]> => {
    const response = await api.get<WorkflowPluginVersion[]>(`/workflows/plugins/${name}/versions/`, {
      params: { limit },
    })
    return response.data
  },
  diffPluginVersions: async (
    name: string,
    v1: number,
    v2: number,
    fileKey = 'WORKFLOW.yaml',
  ): Promise<WorkflowVersionDiff> => {
    const response = await api.get<WorkflowVersionDiff>(`/workflows/plugins/${name}/versions/diff/`, {
      params: { v1, v2, file_key: fileKey },
    })
    return response.data
  },
  exportPlugin: async (name: string): Promise<WorkflowImportBundle> => {
    const response = await api.get<WorkflowImportBundle>(`/workflows/plugins/${name}/export/`, {
      params: { format: 'json' },
    })
    return response.data
  },
  importPlugin: async (data: { bundle: WorkflowImportBundle; overwrite?: boolean }) => {
    const response = await api.post('/workflows/import/', data)
    return response.data
  },
  submitPluginReview: async (name: string) => {
    const response = await api.post(`/workflows/plugins/${name}/submit-review/`)
    return response.data
  },
  publishPlugin: async (name: string, changeSummary?: string) => {
    const response = await api.post(`/workflows/plugins/${name}/publish/`, {
      change_summary: changeSummary,
    })
    return response.data
  },
  rejectPlugin: async (name: string) => {
    const response = await api.post(`/workflows/plugins/${name}/reject/`)
    return response.data
  },
  deletePlugin: async (name: string) => {
    const response = await api.delete(`/workflows/plugins/${name}/`)
    return response.data
  },
  publishPluginToMarket: async (name: string, title?: string) => {
    const response = await api.post(`/workflows/plugins/${name}/publish-to-market/`, { title })
    return response.data
  },
  listMarketTemplates: async (params?: { category?: string; featured_only?: boolean }): Promise<MarketTemplateSummary[]> => {
    const response = await api.get<MarketTemplateSummary[]>('/workflows/market/templates/', { params })
    return response.data
  },
  getMarketTemplate: async (templateId: string): Promise<MarketTemplateDetail> => {
    const response = await api.get<MarketTemplateDetail>(`/workflows/market/templates/${templateId}/`)
    return response.data
  },
  dryRun: async (data: {
    dsl: import('../types/workflowDsl').WorkflowDSL
    context?: Record<string, unknown>
    auto_map_inputs?: boolean
  }): Promise<WorkflowDryRunResult> => {
    const response = await api.post<WorkflowDryRunResult>('/workflows/dry-run/', data)
    return response.data
  },
  suggestChatIntentFromNl: async (data: {
    description: string
    workflow_name: string
    use_llm?: boolean
  }) => {
    const response = await api.post<{
      success: boolean
      source: string
      chat_intent_yaml: string
      tips?: string[]
    }>('/workflows/chat-intent/suggest-nl/', data)
    return response.data
  },
}

export { patrolApi } from '../../../../src/apps/inspection/frontend/api'

export interface WorkflowDryRunStep {
  index: number
  name: string
  label: string
  skill: string
  enabled: boolean
  when?: string | null
  parallel_group?: string | null
  depends_on?: string[]
  resolved_inputs: Record<string, unknown>
  mock_result?: Record<string, unknown> | null
}

export interface WorkflowDryRunResult {
  success: boolean
  run_id: string
  template_name: string
  active_step_count: number
  flow_description: string
  steps: WorkflowDryRunStep[]
  skipped_steps: string[]
  parallel_batches: Array<{ parallel_group: string; step_names: string[] }>
  validation: { valid: boolean; errors: string[]; warnings: string[] }
}
