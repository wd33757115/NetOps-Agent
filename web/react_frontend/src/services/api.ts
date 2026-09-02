// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { api, getChatWebSocketUrl, type AuthSession, API_BASE_URL } from '../config/api'

const responseFilename = (
  contentDisposition: unknown,
  fallback: string,
) => {
  const value = String(contentDisposition || '')
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded.replace(/^["']|["']$/g, ''))
    } catch {
      return encoded
    }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] || fallback
}

export interface ChatRequest {
  query: string
  thread_id?: string
  uploaded_file_path?: string
}

export interface ChatUploadRequest {
  thread_id: string
  filename: string
  file_content: string
  user_id?: string
}

export interface ChatUploadResponse {
  thread_id: string
  filename: string
  file_path: string
  status: string
  message: string
  next_steps?: string
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
  attempt_number: number
  progress: { phase?: string; percent?: number }
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

export interface ParserObservationContract {
  id: string
  source_type: string
  schema_id: string
  schema_ref?: string | null
  source_template_id?: string | null
  entity_type: string
  primary_keys_json: string[]
  required_fields_json: string[]
  optional_fields_json?: string[] | null
  validators_json?: Record<string, unknown> | null
  ignore_fields_json?: string[] | null
  allow_empty: boolean
  empty_patterns_json?: string[] | null
  state_semantics_json?: Record<string, unknown> | null
  version: number
  contract_sha256: string
  status: string
  source_job_id?: string | null
  approval_json?: Record<string, unknown> | null
  created_at?: string
  updated_at?: string
  activated_at?: string | null
  deprecated_at?: string | null
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
  module_violations?: Array<Record<string, unknown>>
  rows: DevicePatrolPreviewRow[]
  errors: Array<{
    device_id?: string
    device_name?: string
    error: string
    failure_code?: string
    module_violations?: Array<Record<string, unknown>>
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

export interface DeviceAgentModuleHealthCheck {
  name: string
  status: 'ok' | 'warning' | 'blocked' | string
  message: string
  value?: string | number | null
  target?: string | null
}

export interface DeviceAgentModuleMetric {
  label: string
  value: string | number
  status?: 'ok' | 'warning' | 'blocked' | string
}

export interface DeviceAgentModuleEvidence {
  kind: string
  status: 'ok' | 'warning' | 'blocked' | 'failed' | string
  title: string
  object_id?: string | null
  occurred_at?: string | null
  summary?: string | null
  detail?: Record<string, unknown>
  target_url?: string | null
}

export interface DeviceAgentModuleControl {
  module_id: string
  status: 'enabled' | 'disabled' | 'maintenance' | string
  maintenance_reason?: string | null
  maintenance_starts_at?: string | null
  maintenance_ends_at?: string | null
  updated_by?: string | null
  updated_at?: string | null
}

export interface DeviceAgentModuleHealth {
  module_id: string
  design_status: string
  runtime_status: 'healthy' | 'degraded' | 'blocked' | string
  effective_status?: 'healthy' | 'degraded' | 'blocked' | 'disabled' | 'maintenance' | string
  control?: DeviceAgentModuleControl
  metrics: DeviceAgentModuleMetric[]
  checks: DeviceAgentModuleHealthCheck[]
  evidence?: DeviceAgentModuleEvidence[]
}

export interface DeviceAgentModuleHealthResponse {
  items: DeviceAgentModuleHealth[]
  generated_at: string
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
    contract_status: string
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
  workflow_run_id?: string | null
  workflow_status?: string
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

export interface KnowledgeDocument {
  id: string
  file_name: string
  relative_path: string
  size_bytes: number
  updated_at: string
  doc_type: string
  category: string
  indexed: boolean
  chunk_count: number
}

export interface KnowledgeStats {
  document_count: number
  indexed_document_count: number
  indexed_chunks: number
  kb_path: string
  vector_store: string
  collection: string
  supported_extensions?: string[]
}

export interface KnowledgeReindexResponse {
  success: boolean
  document_count?: number
  chunk_count?: number
  message?: string
}

export interface KnowledgeDocumentPreview {
  success: boolean
  id: string
  file_name: string
  relative_path: string
  size_bytes: number
  preview_type: 'text' | 'extracted' | 'binary'
  content_type: string
  content: string
  download_base64?: string
  truncated?: boolean
  message?: string
}

export interface KnowledgeUploadRequest {
  filename: string
  file_content: string
  folder?: string
  relative_path?: string
  auto_reindex?: boolean
}

export const chatApi = {
  sendMessage: async (data: ChatRequest): Promise<ChatResponse> => {
    const response = await api.post<ChatResponse>('/chat/', data)
    return response.data
  },
  uploadFile: async (data: ChatUploadRequest): Promise<ChatUploadResponse> => {
    const response = await api.post<ChatUploadResponse>('/chat/upload/', data)
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
  id: number
  username: string
  email: string
  role: string
  thread_id?: string
}

export interface ManagedUser {
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
  create: async (data: { username: string; password: string; role: string; email?: string }) => {
    const response = await api.post<ManagedUser>('/auth/users/', data)
    return response.data
  },
  update: async (
    id: number,
    data: Partial<Pick<ManagedUser, 'email' | 'role' | 'is_active'>>
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

export const parserApi = {
  getValueMappings: async (): Promise<ParserValueMappingConfig> => {
    const response = await api.get<ParserValueMappingConfig>('/parser/value-mappings/')
    return response.data
  },
  updateValueMappings: async (data: {
    scoped_value_mappings: ParserValueMappingRule[]
  }): Promise<ParserValueMappingConfig> => {
    const response = await api.put<ParserValueMappingConfig>('/parser/value-mappings/', data)
    return response.data
  },
  workItems: async (params: ParserPageParams = {}): Promise<ParserPage<ParserWorkItem>> => {
    const response = await api.get<ParserPage<ParserWorkItem>>('/parser/work-items/', { params })
    return response.data
  },
  templates: async (params: ParserPageParams = {}): Promise<ParserPage<ParserTemplateItem>> => {
    const response = await api.get<ParserPage<ParserTemplateItem>>('/parser/templates/', { params })
    return response.data
  },
  jobs: async (params: ParserPageParams = {}): Promise<ParserPage<ParserJobItem>> => {
    const response = await api.get<ParserPage<ParserJobItem>>('/parser/jobs/', { params })
    return response.data
  },
  backfills: async (params: ParserPageParams = {}): Promise<ParserPage<ParserBackfillJob>> => {
    const response = await api.get<ParserPage<ParserBackfillJob>>('/parser/backfills/', { params })
    return response.data
  },
  createBackfill: async (data: {
    template_id: string
    filters?: Record<string, unknown>
  }) => (await api.post<ParserBackfillJob>('/parser/backfills/', data)).data,
  dryRunBackfill: async (id: string) => (
    await api.post(`/parser/backfills/${id}/dry-run/`)
  ).data,
  commitBackfill: async (id: string, data: { reason: string }) => (
    await api.post(`/parser/backfills/${id}/commit/`, data)
  ).data,
  sanitizeJob: async (id: string) => (
    await api.post(`/parser/jobs/${id}/sanitize/`)
  ).data,
  ruleGenerationJobs: async (params: ParserPageParams = {}): Promise<ParserPage<ParserRuleGenerationJob>> => {
    const response = await api.get<ParserPage<ParserRuleGenerationJob>>('/parser/rule-generation-jobs/', { params })
    return response.data
  },
  ruleAssetBundles: async (params: {
    status?: string
    search?: string
    limit?: number
    offset?: number
  } = {}): Promise<ParserRuleAssetBundlePage> => {
    const response = await api.get<ParserRuleAssetBundlePage>('/parser/rule-asset-bundles/', { params })
    return response.data
  },
  ruleAssetBundle: async (id: string): Promise<ParserRuleAssetBundle> => {
    const response = await api.get<ParserRuleAssetBundle>(`/parser/rule-asset-bundles/${id}/`)
    return response.data
  },
  ruleAssetEngineeringDraft: async (id: string): Promise<ParserRuleAssetEngineeringDraft> => {
    const response = await api.get<ParserRuleAssetEngineeringDraft>(
      `/parser/rule-asset-bundles/${id}/engineering-draft/`,
    )
    return response.data
  },
  reviewRuleAssetBundle: async (
    id: string,
    data: { decision: 'accepted' | 'revision_requested'; reason?: string },
  ) => (await api.post(`/parser/rule-asset-bundles/${id}/engineering-review/`, data)).data,
  reviseRuleAssetBundle: async (
    id: string,
    data: {
      contract: Record<string, unknown>
      change_policies: Array<Record<string, unknown>>
      event_rules: Array<Record<string, unknown>>
      reason: string
      idempotency_key: string
    },
    options: { asyncMode?: boolean } = {},
  ) => (await api.post(
    `/parser/rule-asset-bundles/${id}/engineering-revision/`,
    data,
    { params: options.asyncMode ? { async_mode: true } : undefined },
  )).data,
  engineeringRevisionTask: async (taskId: string) => (
    await api.get<{
      task_id: string
      status: string
      ready: boolean
      successful?: boolean | null
      result?: Record<string, unknown> | null
      error?: string | null
    }>(`/parser/engineering-revision-tasks/${taskId}/`)
  ).data,
  autoBuildRuleGenerationContext: async (jobId: string, datasetIds: string[]) => (
    await api.post(`/parser/rule-generation-jobs/${jobId}/auto-context/`, {
      dataset_ids: datasetIds,
    })
  ).data,
  assetGaps: async (params: ParserPageParams = {}): Promise<ParserPage<ParserAssetGap>> => (
    await api.get<ParserPage<ParserAssetGap>>('/parser/asset-gaps/', { params })
  ).data,
  resolveAssetGap: async (id: string, reason: string) => (
    await api.post(`/parser/asset-gaps/${id}/resolve/`, { reason })
  ).data,
  ruleGapsFromChanges: async (params: { days?: number; limit?: number } = {}) => (
    await api.get<{
      days: number
      total_no_event?: number
      by_reason?: Record<string, number>
      by_field?: Record<string, number>
      by_command?: Record<string, number>
      gaps?: Array<Record<string, unknown>>
      items?: Array<Record<string, unknown>>
      [key: string]: unknown
    }>('/parser/rule-gaps/from-changes/', { params })
  ).data,
  observationContracts: async (params: ParserPageParams = {}): Promise<ParserPage<ParserObservationContract>> => {
    const response = await api.get<ParserPage<ParserObservationContract>>('/parser/observation-contracts/', { params })
    return response.data
  },
  changePolicies: async (params: ParserPageParams = {}): Promise<ParserPage<ParserChangePolicy>> => {
    const response = await api.get<ParserPage<ParserChangePolicy>>('/parser/change-policies/', { params })
    return response.data
  },
  resolveRuntimeAssets: async (data: {
    observation_schemas?: Array<[string, string]>
    observation_types?: string[]
    include_event_rules?: boolean
  }): Promise<ParserRuntimeRuleAssetBundle> => {
    const response = await api.post<ParserRuntimeRuleAssetBundle>('/parser/runtime-assets/resolve/', data)
    return response.data
  },
  runtimeAssetHealth: async (): Promise<ParserRuntimeRuleAssetBundle> => {
    const response = await api.get<ParserRuntimeRuleAssetBundle>('/parser/runtime-assets/health/')
    return response.data
  },
  checkItemRuntimeBundleHealth: async (params: {
    page?: number
    page_size?: number
    search?: string
    status?: CheckItemRuntimeBundleHealthStatus
  } = {}): Promise<CheckItemRuntimeBundleHealthPage> => {
    const response = await api.get<CheckItemRuntimeBundleHealthPage>(
      '/parser/check-item-runtime-bundles/health/',
      { params },
    )
    return response.data
  },
  previewCheckItemRuntimeBundle: async (
    data: RuntimeBundleAssemblyPayload,
  ): Promise<RuntimeBundleAssemblyPreview> => {
    const response = await api.post<RuntimeBundleAssemblyPreview>(
      '/parser/check-item-runtime-bundles/assembly-preview/',
      data,
    )
    return response.data
  },
  assembleCheckItemRuntimeBundle: async (
    data: RuntimeBundleAssemblyPayload & { preview_digest: string },
    idempotencyKey: string,
  ): Promise<CheckItemRuntimeBundleVersion> => {
    const response = await api.post<CheckItemRuntimeBundleVersion>(
      '/parser/check-item-runtime-bundles/assemble/',
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  getCheckItemRuntimeBundle: async (
    versionId: string,
  ): Promise<CheckItemRuntimeBundleVersion> => {
    const response = await api.get<CheckItemRuntimeBundleVersion>(
      `/parser/check-item-runtime-bundles/${versionId}/`,
    )
    return response.data
  },
  activateCheckItemRuntimeBundle: async (
    versionId: string,
    reason: string,
    idempotencyKey: string,
  ): Promise<CheckItemRuntimeBundleVersion> => {
    const response = await api.post<CheckItemRuntimeBundleVersion>(
      `/parser/check-item-runtime-bundles/${versionId}/activate/`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  rollbackCheckItemRuntimeBundle: async (
    versionId: string,
    reason: string,
    idempotencyKey: string,
  ): Promise<CheckItemRuntimeBundleVersion> => {
    const response = await api.post<CheckItemRuntimeBundleVersion>(
      `/parser/check-item-runtime-bundles/${versionId}/rollback/`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  rolloutCampaigns: async (params: {
    page?: number
    page_size?: number
    status?: RolloutCampaignStatus
    search?: string
  } = {}): Promise<RolloutCampaignPage> => {
    const response = await api.get<RolloutCampaignPage>(
      '/parser/check-item-rollout-campaigns/',
      { params },
    )
    return response.data
  },
  getRolloutCampaign: async (campaignId: string): Promise<RolloutCampaign> => {
    const response = await api.get<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/`,
    )
    return response.data
  },
  createRolloutCampaign: async (
    data: { name: string; description?: string },
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      '/parser/check-item-rollout-campaigns/',
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  freezeRolloutCampaign: async (
    campaignId: string,
    data: RolloutCampaignFreezePayload,
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/freeze/`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  startRolloutStage: async (
    campaignId: string,
    reason: string,
    idempotencyKey: string,
    executionMode: 'snapshot_replay' | 'live_capture' = 'snapshot_replay',
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/start/`,
      { reason, execution_mode: executionMode },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  rolloutStageExecutions: async (
    campaignId: string,
  ): Promise<{ items: RolloutStageExecution[] }> => {
    const response = await api.get<{ items: RolloutStageExecution[] }>(
      `/parser/check-item-rollout-campaigns/${campaignId}/executions/`,
    )
    return response.data
  },
  getRolloutStageExecution: async (
    campaignId: string,
    executionId: string,
  ): Promise<RolloutStageExecution & Record<string, unknown>> => {
    const response = await api.get<RolloutStageExecution & Record<string, unknown>>(
      `/parser/check-item-rollout-campaigns/${campaignId}/executions/${executionId}/`,
    )
    return response.data
  },
  approveRolloutStage: async (
    campaignId: string,
    comment: string,
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/approve/`,
      { comment },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  pauseRolloutCampaign: async (
    campaignId: string,
    reason: string,
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/pause/`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  resumeRolloutCampaign: async (
    campaignId: string,
    data: { observed_digests: Record<string, string>; reason: string },
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/resume/`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  rollbackRolloutCampaign: async (
    campaignId: string,
    data: {
      evidence_refs: Array<Record<string, unknown> | string>
      reason: string
    },
    idempotencyKey: string,
  ): Promise<RolloutCampaign> => {
    const response = await api.post<RolloutCampaign>(
      `/parser/check-item-rollout-campaigns/${campaignId}/rollback/`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  replayReports: async (params: ParserPageParams = {}): Promise<ParserPage<ParserReplayReport>> => {
    const response = await api.get<ParserPage<ParserReplayReport>>('/parser/replay-reports/', { params })
    return response.data
  },
  approvalAudit: async (params: ParserPageParams = {}): Promise<ParserApprovalAuditPage> => {
    const response = await api.get<ParserApprovalAuditPage>('/parser/approval-audit/', { params })
    return response.data
  },
  agentLoopAttempts: async (params: ParserPageParams = {}): Promise<ParserPage<ParserAgentLoopAttempt>> => {
    const response = await api.get<ParserPage<ParserAgentLoopAttempt>>('/parser/agent-loop-attempts/', { params })
    return response.data
  },
  approveRuleGenerationJob: async (id: string, data: { reason: string }) => (
    await api.post(`/parser/rule-generation-jobs/${id}/approve/`, data)
  ).data,
  rejectRuleGenerationJob: async (id: string, data: { reason: string }) => (
    await api.post(`/parser/rule-generation-jobs/${id}/reject/`, data)
  ).data,
  deprecateRuleGenerationJob: async (id: string, data: { reason: string }) => (
    await api.post(`/parser/rule-generation-jobs/${id}/deprecate/`, data)
  ).data,
  reviseRuleGenerationJob: async (id: string) => (
    await api.post(`/parser/rule-generation-jobs/${id}/revise/`, {})
  ).data,
  executeRuleGenerationRevisionJob: async (id: string) => (
    await api.post(`/parser/rule-generation-jobs/${id}/execute-revision/`, {})
  ).data,
  submitRuleGenerationJobCandidate: async (id: string) => (
    await api.post(`/parser/rule-generation-jobs/${id}/submit/`, {})
  ).data,
  previewRuleGenerationContext: async (
    id: string,
    data: {
      context_overrides?: Record<string, unknown>
      context_id?: string
      dataset_ids?: string[]
      max_records?: number
      field_name?: string
      observation_type?: string
    } = {},
  ): Promise<ParserRuleGenerationContextPreview> => {
    const response = await api.post<ParserRuleGenerationContextPreview>(
      `/parser/rule-generation-jobs/${id}/generation-context/`,
      data,
    )
    return response.data
  },
  startRuleAssetGeneration: async (
    id: string,
    data: {
      context_overrides?: Record<string, unknown>
      context_id?: string
      dataset_ids?: string[]
    } = {},
  ) => (
    await api.post(`/parser/rule-generation-jobs/${id}/start-generation/`, data)
  ).data,
  previewRuleGenerationJob: async (id: string) => {
    const response = await api.get<ParserRuleGenerationJobPreview>(`/parser/rule-generation-jobs/${id}/preview/`)
    return response.data
  },
  job: async (id: string) => {
    const response = await api.get<ParserJobItem>(`/parser/jobs/${id}/`)
    return response.data
  },
  jobAttempts: async (id: string, includeTemplate = false) => {
    const response = await api.get<{ items: ParserGenerationAttempt[] }>(
      `/parser/jobs/${id}/attempts/`,
      { params: { include_template: includeTemplate } },
    )
    return response.data.items
  },
  submitManualCandidate: async (
    id: string,
    data: {
      template_text: string
      based_on_template_id?: string
      reason?: string
    },
  ) => (await api.post(`/parser/jobs/${id}/manual-candidate/`, data)).data,
  updateJobSpec: async (
    id: string,
    data: { parser_spec: Record<string, unknown>; confirmed: boolean; reason: string },
  ) => (await api.put(`/parser/jobs/${id}/spec/`, data)).data,
  suggestJobSpec: async (id: string, revisionInstruction = '', checkItemId = '') => (
    await api.post(`/parser/jobs/${id}/suggest-spec/`, {
      reason: revisionInstruction.trim()
        ? 'Engineer requested a natural-language ParserSpec revision'
        : 'Generate ParserSpec draft from sanitized samples',
      revision_instruction: revisionInstruction.trim() || undefined,
      check_item_id: checkItemId.trim() || undefined,
    })
  ).data,
  updateJobSampleRole: async (
    jobId: string,
    sampleId: string,
    data: {
      role: 'unassigned' | 'generation' | 'acceptance' | 'negative'
      expectation?: { record_count: number; primary_keys: string[][] }
      reason: string
    },
  ) => (await api.put(`/parser/jobs/${jobId}/samples/${sampleId}/role/`, data)).data,
  aliases: async (params: ParserPageParams = {}): Promise<ParserPage<ParserCommandAlias>> => {
    const response = await api.get<ParserPage<ParserCommandAlias>>('/parser/aliases/', { params })
    return response.data
  },
  deviceProfiles: async (params: ParserPageParams = {}): Promise<ParserPage<ParserDeviceProfile>> => {
    const response = await api.get<ParserPage<ParserDeviceProfile>>('/parser/device-profiles/', { params })
    return response.data
  },
  identitySignatures: async (params: ParserPageParams = {}): Promise<ParserPage<ParserIdentitySignature>> => {
    const response = await api.get<ParserPage<ParserIdentitySignature>>('/parser/identity-signatures/', { params })
    return response.data
  },
  coverage: async (params: { by_effective_plan?: boolean } = {}) => {
    const response = await api.get<ParserCoverage>('/parser/coverage/', { params })
    return response.data
  },
  preview: async (id: string) => {
    const response = await api.get<ParserTemplatePreview>(`/parser/templates/${id}/preview/`)
    return response.data
  },
  comparison: async (id: string) => {
    const response = await api.get<ParserTemplateComparison>(`/parser/templates/${id}/comparison/`)
    return response.data
  },
  exportTemplates: async (status = 'active') => {
    const response = await api.get<ParserTemplateExport>('/parser/templates/export/', { params: { status } })
    return response.data
  },
  datasets: async (datasetIds: string[] = []) => {
    const response = await api.get<{ items: ParserDatasetCoverage[] }>('/parser/datasets/', {
      params: datasetIds.length ? { dataset_ids: datasetIds.join(',') } : undefined,
    })
    return response.data.items
  },
  previewImport: async (data: { source_path: string; dataset_id?: string }) => (
    await api.post<ParserImportPreview>('/parser/import-preview/', data)
  ).data,
  executeImport: async (data: { source_path: string; dataset_id?: string }) => (
    await api.post('/parser/import-execute/', data)
  ).data,
  semanticReview: async (
    id: string,
    data: {
      reason: string
      result_confirmed: boolean
      primary_key_confirmed: boolean
    },
  ) => (await api.post(`/parser/templates/${id}/semantic-review/`, data)).data,
  activate: async (id: string, reason: string) => (
    await api.post(`/parser/templates/${id}/activate/`, { reason })
  ).data,
  reject: async (id: string, reason: string) => (await api.post(`/parser/templates/${id}/reject/`, { reason })).data,
  deprecate: async (id: string, reason: string) => (await api.post(`/parser/templates/${id}/deprecate/`, { reason })).data,
  retryJob: async (id: string) => (await api.post(`/parser/jobs/${id}/retry/`)).data,
  retryAndDispatchJob: async (id: string) => (
    await api.post(`/parser/jobs/${id}/retry-dispatch/`)
  ).data,
  cancelJob: async (id: string) => (await api.post(`/parser/jobs/${id}/cancel/`)).data,
  recoverJob: async (id: string, reason?: string) => (
    await api.post(`/parser/jobs/${id}/recover/`, reason ? { reason } : {})
  ).data,
  deleteJob: async (id: string, reason: string) => (
    await api.delete(`/parser/jobs/${id}/`, { data: { reason } })
  ).data,
  bulkDeleteJobs: async (job_ids: string[], reason: string) => (
    await api.post('/parser/jobs/bulk-delete/', { job_ids, reason })
  ).data,
  dispatchJob: async (id: string) => (await api.post(`/parser/jobs/${id}/dispatch/`)).data,
  dispatch: async () => (await api.post('/parser/dispatch/')).data,
  dispatchControl: async () => {
    const response = await api.get<ParserDispatchControl>('/parser/dispatch-control/')
    return response.data
  },
  setDispatchControl: async (enabled: boolean, reason: string) => (
    await api.post<ParserDispatchControl>('/parser/dispatch-control/', { enabled, reason })
  ).data,
  bootstrap: async () => (await api.post('/parser/bootstrap/')).data,
  eventRules: async (params: ParserPageParams = {}): Promise<ParserPage<ParserEventRule>> => {
    const response = await api.get<ParserPage<ParserEventRule>>('/parser/event-rules/', { params })
    return response.data
  },
  bootstrapEventRules: async (reason: string) => (
    await api.post('/parser/event-rules/bootstrap/', { reason })
  ).data,
  upsertEventRule: async (data: {
    name: string
    event_type: string
    severity: string
    condition: Record<string, unknown>
    description?: string
    status?: string
  }) => (await api.post('/parser/event-rules/', data)).data,
  deprecateEventRule: async (versionId: string, reason: string) =>
    (await api.post(`/parser/event-rules/${versionId}/deprecate/`, { reason })).data,
  testEventRule: async (data: {
    condition: Record<string, unknown>
    samples: Array<Record<string, unknown>>
  }): Promise<ParserEventRuleTestResult> => {
    const response = await api.post<ParserEventRuleTestResult>('/parser/event-rules/test/', data)
    return response.data
  },
  replayEventRules: async (data: {
    candidate_rules: Array<{
      name?: string
      rule_id?: string
      version?: number
      event_type: string
      severity?: string
      condition: Record<string, unknown>
      description?: string
    }>
    limit?: number
    include_active_collisions?: boolean
  }): Promise<ParserEventRuleReplayResult> => {
    const response = await api.post<ParserEventRuleReplayResult>('/parser/event-rules/replay/', data)
    return response.data
  },
  commandProfiles: async (params: ParserPageParams = {}): Promise<ParserPage<ParserCommandProfile>> => {
    const response = await api.get<ParserPage<ParserCommandProfile>>('/parser/command-profiles/', { params })
    return response.data
  },
  upsertCommandProfile: async (data: {
    name: string
    action?: string
    commands?: string[]
    check_item_ids?: string[]
    status?: string
  }) => (await api.post('/parser/command-profiles/', data)).data,
  checkItems: async (params: ParserPageParams & { category?: string } = {}): Promise<ParserPage<ParserCheckItem>> => {
    const response = await api.get<ParserPage<ParserCheckItem>>('/parser/check-items/', { params })
    return response.data
  },
  catalogChanges: async (params: ParserPageParams = {}): Promise<ParserPage<CheckItemCatalogChange>> => {
    const response = await api.get<ParserPage<CheckItemCatalogChange>>('/parser/catalog/changes/', { params })
    return response.data
  },
  getCatalogChange: async (changeId: string): Promise<CheckItemCatalogChange> => (
    await api.get<CheckItemCatalogChange>(`/parser/catalog/changes/${changeId}/`)
  ).data,
  canonicalObservations: async (): Promise<CanonicalObservationCatalog> => (
    await api.get<CanonicalObservationCatalog>('/parser/catalog/canonical-observations/')
  ).data,
  checkItemRuleContext: async (checkItemVersionId: string): Promise<CanonicalRuleContext> => (
    await api.get<CanonicalRuleContext>(
      `/parser/catalog/check-item-versions/${checkItemVersionId}/rule-context/`,
    )
  ).data,
  generateCheckItemRuleCandidates: async (
    checkItemVersionId: string,
  ): Promise<CanonicalRuleCandidates> => (
    await api.post<CanonicalRuleCandidates>(
      `/parser/catalog/check-item-versions/${checkItemVersionId}/rule-candidates/`,
    )
  ).data,
  createCheckItemRuleCandidateDrafts: async (
    checkItemVersionId: string,
  ): Promise<CanonicalRuleCandidates> => (
    await api.post<CanonicalRuleCandidates>(
      `/parser/catalog/check-item-versions/${checkItemVersionId}/rule-candidates/drafts/`,
    )
  ).data,
  createCheckItemDraft: async (
    data: CheckItemCatalogCheckItemDraftInput,
    idempotencyKey: string,
  ): Promise<CheckItemCatalogChange> => (
    await api.post<CheckItemCatalogChange>('/parser/catalog/check-items/drafts/', data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data,
  deleteCheckItem: async (
    checkItemId: string,
    reason: string | undefined,
    idempotencyKey: string,
  ): Promise<CheckItemCatalogCheckItemDeleteResult> => (
    await api.delete<CheckItemCatalogCheckItemDeleteResult>(
      `/parser/catalog/check-items/${checkItemId}/`,
      {
        data: { reason: reason?.trim() || '' },
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    )
  ).data,
  createCollectionImplementationDraft: async (
    data: CheckItemCatalogImplementationDraftInput,
    idempotencyKey: string,
  ): Promise<CheckItemCatalogChange> => (
    await api.post<CheckItemCatalogChange>('/parser/catalog/implementations/drafts/', data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data,
  previewCommandAdapter: async (data: {
    adapter_spec: Record<string, unknown>
    record_sets: Record<string, Array<Record<string, unknown>>>
    device_id?: string
    adapter_version_id?: string
  }): Promise<AdapterProjectionPreviewResult> => (
    await api.post<AdapterProjectionPreviewResult>('/parser/catalog/command-adapters/preview/', data)
  ).data,
  generateAdapterProjectionCandidate: async (
    data: AdapterProjectionCandidateInput,
  ): Promise<AdapterProjectionCandidateResult> => (
    await api.post<AdapterProjectionCandidateResult>(
      '/parser/catalog/adapter-projections/candidates/',
      data,
    )
  ).data,
  validateAdapterProjection: async (
    data: Omit<AdapterProjectionCandidateInput, 'generation_provider' | 'max_agent_attempts'> & {
      proposal: Record<string, unknown>
    },
  ): Promise<AdapterProjectionCandidateResult> => (
    await api.post<AdapterProjectionCandidateResult>(
      '/parser/catalog/adapter-projections/validate/',
      data,
    )
  ).data,
  adapterProjectionCutoverReadiness: async (): Promise<AdapterProjectionCutoverReadiness> => (
    await api.get<AdapterProjectionCutoverReadiness>(
      '/parser/catalog/adapter-projections/cutover-readiness/',
    )
  ).data,
  verifyAdapterProjectionCutoverReadiness: async (
    replayByImplementationVersion: Record<string, AdapterProjectionReplayAttestation>,
  ): Promise<AdapterProjectionCutoverReadiness> => (
    await api.post<AdapterProjectionCutoverReadiness>(
      '/parser/catalog/adapter-projections/cutover-readiness/',
      { replay_by_implementation_version: replayByImplementationVersion },
    )
  ).data,
  recordAdapterProjectionReplayAttestations: async (
    replayByImplementationVersion: Record<string, AdapterProjectionReplayAttestation>,
    idempotencyKey: string,
  ): Promise<AdapterProjectionCutoverReadiness> => (
    await api.post<AdapterProjectionCutoverReadiness>(
      '/parser/catalog/adapter-projections/replay-attestations/',
      { replay_by_implementation_version: replayByImplementationVersion },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
  ).data,
  authorizeAdapterProjectionCutover: async (
    data: { expected_readiness_digest: string; reason: string },
    idempotencyKey: string,
  ): Promise<AdapterProjectionCutoverAuthorizationResult> => (
    await api.post<AdapterProjectionCutoverAuthorizationResult>(
      '/parser/catalog/adapter-projections/cutover-authorization/',
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
  ).data,
  deleteCollectionImplementation: async (
    implementationId: string,
    reason: string | undefined,
    idempotencyKey: string,
  ): Promise<CheckItemCatalogImplementationDeleteResult> => (
    await api.delete<CheckItemCatalogImplementationDeleteResult>(
      `/parser/catalog/implementations/${implementationId}/`,
      {
        data: { reason: reason?.trim() || '' },
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    )
  ).data,
  previewCatalogChange: async (changeId: string): Promise<CheckItemCatalogImpact> => (
    await api.post<CheckItemCatalogImpact>(`/parser/catalog/changes/${changeId}/impact-preview/`)
  ).data,
  catalogPublicationOptions: async (
    changeId: string,
  ): Promise<CheckItemCatalogPublicationOptions> => (
    await api.get<CheckItemCatalogPublicationOptions>(
      `/parser/catalog/changes/${changeId}/publication-options/`,
    )
  ).data,
  submitCatalogChange: async (
    changeId: string,
    data: { reason?: string; expected_impact_digest: string },
    idempotencyKey: string,
  ): Promise<CheckItemCatalogChange> => (
    await api.post<CheckItemCatalogChange>(`/parser/catalog/changes/${changeId}/submit/`, data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data,
  decideCatalogChange: async (
    changeId: string,
    decision: 'approve' | 'reject',
    data: {
      reason?: string
      expected_impact_digest: string
      rollout_campaign_id?: string
    },
    idempotencyKey: string,
  ): Promise<CheckItemCatalogChange> => (
    await api.post<CheckItemCatalogChange>(`/parser/catalog/changes/${changeId}/${decision}/`, data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data,
  getPatrolProfileCheckItems: async (profileVersionId: string) => (
    await api.get(`/parser/command-profiles/${profileVersionId}/check-items/`)
  ).data,
  setPatrolProfileCheckItems: async (profileVersionId: string, checkItemIds: string[]) => (
    await api.put(`/parser/command-profiles/${profileVersionId}/check-items/`, {
      check_item_ids: checkItemIds,
    })
  ).data,
  activateCommandProfile: async (profileVersionId: string, reason: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/activate/`, { reason })).data,
  deprecateCommandProfile: async (profileVersionId: string, reason: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/deprecate/`, { reason })).data,
  rollbackCommandProfile: async (profileVersionId: string, reason: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/rollback/`, { reason })).data,
  deleteCommandProfile: async (profileVersionId: string, reason: string) =>
    (await api.delete(`/parser/command-profiles/${profileVersionId}/`, { data: { reason } })).data,
  commandProfileBindings: async (params: ParserPageParams = {}): Promise<ParserPage<ParserCommandProfileBinding>> => {
    const response = await api.get<ParserPage<ParserCommandProfileBinding>>('/parser/command-profile-bindings/', { params })
    return response.data
  },
  upsertCommandProfileBinding: async (data: {
    profile_version_id: string
    selector: Record<string, unknown>
    priority?: number
    status?: string
  }) => (await api.post('/parser/command-profile-bindings/', data)).data,
  enableCommandProfileBinding: async (bindingId: string, reason: string) =>
    (await api.post(`/parser/command-profile-bindings/${bindingId}/enable/`, { reason })).data,
  disableCommandProfileBinding: async (bindingId: string, reason: string) =>
    (await api.post(`/parser/command-profile-bindings/${bindingId}/disable/`, { reason })).data,
  commandProfileImpactPreview: async (data: {
    profile_version_id?: string
    profile_name?: string
    action?: string
    commands?: string[]
    selector?: Record<string, unknown>
    mode?: string
    limit?: number
    save_evidence?: boolean
  }) => {
    const response = await api.post<ParserCommandProfileImpactPreview>(
      '/parser/command-profiles/impact-preview/',
      data,
    )
    return response.data
  },
  effectivePlanPreview: async (data: {
    device: Record<string, unknown>
    action?: string
    mode?: string
  }) => {
    const response = await api.post<ParserEffectivePlanPreview>('/parser/effective-plan-preview/', data)
    return response.data
  },
}

const RULE_ASSET_SOURCE_JOBS_BASE = '/parser/asset-source-jobs'
const OFFLINE_PATROL_JOBS_BASE = '/patrol/offline-jobs'

const createOfflineJobApi = (base: string) => ({
  list: async (params: {
    page?: number
    page_size?: number
    search?: string
    status?: string
    job_type?: OfflinePatrolJobType
    archived?: boolean
  } = {}): Promise<OfflinePatrolJobPage> => (
    await api.get<OfflinePatrolJobPage>(`${base}/`, { params })
  ).data,
  get: async (id: string): Promise<OfflinePatrolJob> => {
    const data = (await api.get<OfflinePatrolJob>(`${base}/${id}/`)).data
    const preview = (data.preview || {}) as {
      output_review_items?: OfflineOutputReviewItem[]
      output_dispositions?: OfflineOutputDisposition[]
    }
    return {
      ...data,
      output_review_items: data.output_review_items || preview.output_review_items || [],
      output_dispositions: data.output_dispositions || preview.output_dispositions || [],
    }
  },
  replay: async (id: string): Promise<OfflinePatrolAnalysisSummary> => (
    await api.get<OfflinePatrolAnalysisSummary>(`${base}/${id}/replay-summary/`, {
      timeout: 300000,
    })
  ).data,
  comparisonEvidence: async (
    id: string,
    changeId: string,
  ): Promise<OfflinePatrolComparisonEvidence> => (
    await api.get<OfflinePatrolComparisonEvidence>(
      base + '/' + id + '/comparison-evidence/' + encodeURIComponent(changeId) + '/',
      { timeout: 300000 },
    )
  ).data,
  create: async (data: {
    job_type: OfflinePatrolJobType
    name: string
    periods: Array<{ role: string; label: string; observed_at: string }>
    vendor?: string
    model?: string
    publish_events?: boolean
    report_intent?: OfflinePatrolReportIntent
    idempotency_key: string
  }): Promise<OfflinePatrolJob> => (
    await api.post<OfflinePatrolJob>(`${base}/`, data)
  ).data,
  attachSource: async (id: string, data: {
    period_role: string
    file_id: string
    relative_path: string
    sha256?: string
    size_bytes: number
  }): Promise<OfflinePatrolSourceFile> => (
    await api.post<OfflinePatrolSourceFile>(`${base}/${id}/sources/`, data)
  ).data,
  removeSource: async (id: string, sourceId: string) => (
    await api.delete(`${base}/${id}/sources/${sourceId}/`)
  ).data,
  preview: async (id: string) => (
    await api.post(`${base}/${id}/preview/`, {})
  ).data,
  confirmBindings: async (id: string, bindings: Array<{ binding_id: string; inventory_device_id?: string | null }>) => (
    await api.post(`${base}/${id}/device-bindings/confirm/`, { bindings })
  ).data,
  confirmOutputDispositions: async (id: string, reviewItemIds: string[], reason: string) => (
    await api.post(`${base}/${id}/output-dispositions/confirm/`, {
      review_item_ids: reviewItemIds,
      reason,
    })
  ).data,
  execute: async (id: string, idempotencyKey: string) => (
    await api.post(`${base}/${id}/execute/`, { idempotency_key: idempotencyKey })
  ).data,
  cancel: async (id: string) => (await api.post(`${base}/${id}/cancel/`, {})).data,
  retry: async (id: string) => (await api.post(`${base}/${id}/retry/`, {})).data,
  archive: async (id: string) => (await api.post(`${base}/${id}/archive/`, {})).data,
  restore: async (id: string) => (await api.post(`${base}/${id}/restore/`, {})).data,
  artifact: async (id: string, kind: 'raw' | 'changes' | 'events' | 'report'): Promise<Blob> => (
    await api.get(`${base}/${id}/artifacts/${kind}/`, {
      responseType: 'blob',
      timeout: 300000,
    })
  ).data as Blob,
})

export const ruleAssetSourceApi = {
  ...createOfflineJobApi(RULE_ASSET_SOURCE_JOBS_BASE),
  assetPipeline: async (id: string): Promise<OfflineRuleAssetPipeline> => (
    await api.get<OfflineRuleAssetPipeline>(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/asset-production-pipeline/`,
    )
  ).data,
  refreshCommandAssetUnits: async (id: string): Promise<{ unit_count: number }> => (
    await api.post(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/refresh/`,
      {},
    )
  ).data,
  commandAssetUnits: async (id: string, params: {
    page?: number
    page_size?: number
    search?: string
    status?: string
    stage?: string
    vendor?: string
    model?: string
    asset_mode?: string
  } = {}): Promise<OfflineCommandAssetUnitPage> => (
    await api.get<OfflineCommandAssetUnitPage>(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/`,
      { params },
    )
  ).data,
  checkItemProductionGroups: async (params: {
    page?: number
    page_size?: number
    search?: string
    bucket?: string
    governance_status?: string
    stage?: string
    vendor?: string
    model?: string
    asset_mode?: string
    offline_job_id?: string
  } = {}): Promise<CheckItemProductionGroupPage> => (
    await api.get<CheckItemProductionGroupPage>(
      '/parser/asset-production/check-items/',
      { params },
    )
  ).data,
  commandAssetUnit: async (id: string, unitId: string, params: {
    evidence_page?: number
    evidence_page_size?: number
  } = {}): Promise<OfflineCommandAssetUnitDetail> => (
    await api.get<OfflineCommandAssetUnitDetail>(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/${unitId}/`,
      { params },
    )
  ).data,
  confirmCommandAssetNotApplicable: async (
    id: string,
    unitId: string,
    payload: { reason: string },
  ): Promise<{ success: boolean }> => (
    await api.post(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/${unitId}/confirm-not-applicable/`,
      payload,
    )
  ).data,
  associateUnclassifiedCommandAsset: async (
    id: string,
    unitId: string,
    payload: { check_item_id: string },
  ): Promise<{
    success: boolean
    association: {
      check_item_id: string
      check_item_version_id: string
    }
    parser_job_id?: string | null
  }> => (
    await api.post(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/${unitId}/check-item-association/`,
      payload,
    )
  ).data,
  deleteUnclassifiedCommandAsset: async (
    id: string,
    unitId: string,
  ): Promise<{ success: boolean; unit_id: string; asset_key: string }> => (
    await api.delete(
      `${RULE_ASSET_SOURCE_JOBS_BASE}/${id}/command-asset-units/${unitId}/`,
    )
  ).data,
}
export const offlinePatrolApi = {
  ...createOfflineJobApi(OFFLINE_PATROL_JOBS_BASE),
  currentAuthority: async (id: string): Promise<PatrolCurrentAuthority> => (
    await api.get<PatrolCurrentAuthority>(
      `${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/`,
    )
  ).data,
  prepareCurrentAuthority: async (id: string, data: {
    completeness_statement: string
    completeness_evidence_refs: string[]
    observed_at_evidence_refs: string[]
    collection_tool: string
    collector_identity: string
    scope_contract?: Record<string, unknown>
  }, idempotencyKey: string): Promise<PatrolCurrentAuthority> => (
    await api.post<PatrolCurrentAuthority>(
      `${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/prepare/`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
  ).data,
  attestCurrentAuthority: async (id: string, data: {
    request_digest: string
    reason: string
  }, idempotencyKey: string): Promise<Record<string, unknown>> => (
    await api.post(
      `${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/attest/`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
  ).data,
  revokeCurrentAuthority: async (id: string, reason: string, idempotencyKey: string) => (
    await api.post(
      `${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/revoke/`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
  ).data,
}

export const deviceApi = {
  list: async (params: Record<string, string | number | undefined> = {}) => {
    const response = await api.get<DeviceListResponse>('/devices/', { params })
    return response.data
  },
  upsert: async (data: DeviceUpsertPayload) => {
    const response = await api.post<DeviceMutationResponse>('/devices/', data)
    return response.data
  },
  detail: async (deviceId: string) => {
    const response = await api.get<ManagedDevice>(`/devices/${deviceId}/`)
    return response.data
  },
  update: async (deviceId: string, data: Partial<DeviceUpsertPayload>) => {
    const response = await api.patch<DeviceMutationResponse>(`/devices/${deviceId}/`, data)
    return response.data
  },
  delete: async (deviceId: string) => {
    const response = await api.delete<DeviceMutationResponse>(`/devices/${deviceId}/`)
    return response.data
  },
  setStatus: async (deviceId: string, status: string) => {
    const response = await api.post<DeviceMutationResponse>(`/devices/${deviceId}/status/`, { status })
    return response.data
  },
  setGroups: async (deviceId: string, groups: string[]) => {
    const response = await api.post<DeviceMutationResponse>(`/devices/${deviceId}/groups/`, { groups })
    return response.data
  },
  previewImport: async (input: string | DeviceImportPayload) => {
    const payload = typeof input === 'string'
      ? { content: input, format: 'csv', dry_run: true }
      : { ...input, dry_run: true }
    const response = await api.post<DeviceImportPreview>('/devices/import/preview/', {
      ...payload,
    })
    return response.data
  },
  applyImport: async (input: string | DeviceImportPayload) => {
    const payload = typeof input === 'string'
      ? { content: input, format: 'csv', dry_run: false }
      : { ...input, dry_run: false }
    const response = await api.post<DeviceImportApplyResult>('/devices/import/apply/', {
      ...payload,
    })
    return response.data
  },
  importTemplate: async () => {
    const response = await api.get<DeviceImportTemplate>('/devices/import/template/')
    return response.data
  },
  groups: async () => {
    const response = await api.get<{ items: string[] }>('/devices/groups/')
    return response.data.items
  },
  previewBulk: async (data: DeviceBulkRequest) => {
    const response = await api.post<DeviceBulkPreview>('/devices/bulk/preview/', data)
    return response.data
  },
  applyBulk: async (data: DeviceBulkRequest) => {
    const response = await api.post<DeviceBulkApplyResult>('/devices/bulk/apply/', data)
    return response.data
  },
  previewBulkDelete: async (data: Omit<DeviceBulkRequest, 'update'>) => {
    const response = await api.post<DeviceBulkDeletePreview>('/devices/bulk/delete/preview/', data)
    return response.data
  },
  bulkDelete: async (data: Omit<DeviceBulkRequest, 'update'>) => {
    const response = await api.post<DeviceBulkDeleteResult>('/devices/bulk/delete/', data)
    return response.data
  },
  previewPatrol: async (data: DevicePatrolLaunchRequest) => {
    const response = await api.post<DevicePatrolPreview>('/devices/patrol/preview/', data)
    return response.data
  },
  patrolReadiness: async (params: { workflow_name?: string; launch_mode?: string } = {}) => {
    const response = await api.get<PatrolProductionReadiness>('/devices/patrol/readiness/', {
      params,
    })
    return response.data
  },
  launchPatrol: async (data: DevicePatrolLaunchRequest) => {
    const response = await api.post<DevicePatrolLaunchResult>('/devices/patrol/launch/', data)
    return response.data
  },
  getPatrolBatchLiveEventsUrl: (batchId: string) =>
    `${API_BASE_URL}/devices/patrol/batches/${batchId}/events/`,
  effectivePlan: async (
    deviceId: string,
    data: { action?: string; mode?: string } = {},
  ) => {
    const response = await api.post<DeviceEffectivePlanPreview>(
      `/devices/${deviceId}/effective-plan/`,
      data,
    )
    return response.data
  },
  testConnectivity: async (deviceId: string) => {
    const response = await api.post<DeviceConnectivityTestResult>(
      `/devices/${deviceId}/connectivity-test/`,
      {},
    )
    return response.data
  },
  discoverIdentity: async (deviceId: string) => {
    const response = await api.post<DeviceIdentityDiscoveryResult>(
      `/devices/${deviceId}/identity-discovery/`,
      {},
    )
    return response.data
  },
  connectionHealth: async (deviceId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceConnectionHealthResponse>(
      `/devices/${deviceId}/connection-health/`,
      { params },
    )
    return response.data
  },
  executionPlans: async (deviceId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceExecutionPlanResponse>(
      `/devices/${deviceId}/execution-plans/`,
      { params },
    )
    return response.data
  },
  evidenceTimeline: async (
    deviceId: string,
    params: { limit?: number; workflow_run_id?: string; patrol_batch_id?: string } = {},
  ) => {
    const response = await api.get<DeviceEvidenceTimelineResponse>(
      `/devices/${deviceId}/evidence-timeline/`,
      { params },
    )
    return response.data
  },
  dynamicGroups: async (params: { status?: string } = {}) => {
    const response = await api.get<{ items: DeviceDynamicGroup[] }>('/devices/dynamic-groups/', { params })
    return response.data.items
  },
  saveDynamicGroup: async (data: {
    name: string
    description?: string
    selector: Record<string, unknown>
    status?: string
  }, groupId?: string) => {
    const response = groupId
      ? await api.patch<{ success: boolean; group: DeviceDynamicGroup }>(`/devices/dynamic-groups/${groupId}/`, data)
      : await api.post<{ success: boolean; group: DeviceDynamicGroup }>('/devices/dynamic-groups/', data)
    return response.data
  },
  setDynamicGroupStatus: async (groupId: string, status: string) => {
    const response = await api.post<{ success: boolean; group: DeviceDynamicGroup }>(
      `/devices/dynamic-groups/${groupId}/status/`,
      { status },
    )
    return response.data
  },
  previewDynamicGroup: async (groupId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceDynamicGroupPreview>(
      `/devices/dynamic-groups/${groupId}/preview/`,
      { params },
    )
    return response.data
  },
  credentials: async () => {
    const response = await api.get<{ items: DeviceCredentialReference[] }>('/devices/credentials/')
    return response.data.items
  },
  inspectCredential: async (credential_ref?: string) => {
    const response = await api.post<DeviceCredentialInspection>('/devices/credentials/inspect/', { credential_ref })
    return response.data
  },
  agentModuleHealth: async () => {
    const response = await api.get<DeviceAgentModuleHealthResponse>('/devices/agent-modules/health/')
    return response.data
  },
  updateAgentModuleControl: async (
    moduleId: string,
    payload: {
      status: string
      maintenance_reason?: string | null
      maintenance_starts_at?: string | null
      maintenance_ends_at?: string | null
    },
  ) => {
    const response = await api.patch<{ success: boolean; control: DeviceAgentModuleControl }>(
      `/devices/agent-modules/${moduleId}/control/`,
      payload,
    )
    return response.data
  },
}

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

export const knowledgeApi = {
  listDocuments: async (): Promise<KnowledgeDocument[]> => {
    const response = await api.get<KnowledgeDocument[]>('/knowledge/documents/')
    return response.data
  },
  getStats: async (): Promise<KnowledgeStats> => {
    const response = await api.get<KnowledgeStats>('/knowledge/stats/')
    return response.data
  },
  reindex: async (): Promise<KnowledgeReindexResponse> => {
    const response = await api.post<KnowledgeReindexResponse>('/knowledge/reindex/')
    return response.data
  },
  getPreview: async (relativePath: string): Promise<KnowledgeDocumentPreview> => {
    const path = encodeKnowledgePath(relativePath)
    const response = await api.get<KnowledgeDocumentPreview>(`/knowledge/documents/${path}/content/`)
    return response.data
  },
  upload: async (data: KnowledgeUploadRequest) => {
    const response = await api.post('/knowledge/documents/', data)
    return response.data
  },
  delete: async (relativePath: string, autoReindex = true) => {
    const path = encodeKnowledgePath(relativePath)
    const response = await api.delete(`/knowledge/documents/${path}/`, {
      params: { auto_reindex: autoReindex },
    })
    return response.data
  },
}

function encodeKnowledgePath(relativePath: string): string {
  return relativePath.split('/').map(encodeURIComponent).join('/')
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
      skill_name: string
      label: string
      description: string
      status: string
      total: number
      status_counts: Record<string, number>
      completed_count: number
      failed_count: number
      active_count: number
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
    run_id: string
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
  listPatrolBatches: async (params?: {
    limit?: number
    offset?: number
    include_archived?: boolean
    search?: string
    status?: string
  }): Promise<PatrolBatchPage> => {
    const response = await api.get<PatrolBatchPage>('/workflows/runs/patrol-batches/', { params })
    const items = response.data.items || []
    const limit = response.data.limit || params?.limit || 25
    const offset = response.data.offset || params?.offset || 0
    const total = response.data.total ?? items.length
    return {
      ...response.data,
      items,
      total,
      limit,
      offset,
      page: response.data.page || Math.floor(offset / limit) + 1,
      page_size: response.data.page_size || limit,
      page_count: response.data.page_count ?? (total ? Math.ceil(total / limit) : 0),
    }
  },
  archivePatrolBatch: async (batchId: string, data?: { reason?: string; keep_until?: string; force?: boolean }): Promise<PatrolBatchGovernanceResult> => {
    const response = await api.post<PatrolBatchGovernanceResult>(`/workflows/runs/patrol-batches/${batchId}/archive/`, data || {})
    return response.data
  },
  restorePatrolBatch: async (batchId: string, data?: { reason?: string; keep_until?: string }): Promise<PatrolBatchGovernanceResult> => {
    const response = await api.post<PatrolBatchGovernanceResult>(`/workflows/runs/patrol-batches/${batchId}/restore/`, data || {})
    return response.data
  },
  cancelPatrolBatch: async (
    batchId: string,
    data: { reason: string },
  ): Promise<PatrolBatchCancellationResult> => {
    const response = await api.post<PatrolBatchCancellationResult>(
      `/workflows/runs/patrol-batches/${batchId}/cancel/`,
      data,
    )
    return response.data
  },
  getPatrolBatchSummary: async (batchId: string): Promise<PatrolBatchResultSummary> => {
    const response = await api.get<PatrolBatchResultSummary>(`/workflows/runs/patrol-batches/${batchId}/summary/`)
    return response.data
  },
  listPatrolBatchCheckResults: async (
    batchId: string,
    params?: {
      limit?: number
      offset?: number
      cursor?: string
      search?: string
      assessment_status?: string
      completeness_status?: string
      source_identity_status?: string
      output_quality_status?: string
      freshness_status?: string
      coverage_status?: string
    },
  ): Promise<PatrolBatchCheckResultPage> => {
    const response = await api.get<PatrolBatchCheckResultPage>(
      `/workflows/runs/patrol-batches/${batchId}/check-results`,
      { params },
    )
    return response.data
  },
  getPatrolBatchCheckResultDetail: async (
    batchId: string,
    executionId: string,
    params: { entry_limit?: number; entry_offset?: number } = {},
  ): Promise<PatrolBatchCheckResultDetail> => {
    const response = await api.get<PatrolBatchCheckResultDetail>(
      `/workflows/runs/patrol-batches/${batchId}/check-results/${executionId}`,
      { params },
    )
    return response.data
  },
  listPatrolBatchDevices: async (
    batchId: string,
    params?: { limit?: number; offset?: number; search?: string; selection_status?: string },
  ): Promise<PatrolBatchDevicePage> => {
    const response = await api.get<PatrolBatchDevicePage>(
      `/workflows/runs/patrol-batches/${batchId}/devices`,
      { params },
    )
    return response.data
  },
  retryPatrolBatch: async (batchId: string): Promise<{
    success: boolean
    patrol_batch_id: string
    retryable_count: number
    retried_count: number
    runs: Array<{ run_id: string; retry_of_run_id: string; device_id?: string; device_name?: string }>
  }> => {
    const response = await api.post(`/workflows/runs/patrol-batches/${batchId}/retry/`, {})
    return response.data
  },
  rerunPatrolBatch: async (batchId: string): Promise<{
    success: boolean
    patrol_batch_id: string
    new_patrol_batch_id: string
    source_run_count: number
    deduplicated_device_count: number
    rerun_count: number
    runs: Array<{ run_id: string; rerun_of_run_id: string; device_id?: string; device_name?: string }>
  }> => {
    const response = await api.post(`/workflows/runs/patrol-batches/${batchId}/rerun/`, {})
    return response.data
  },
  exportPatrolBatch: async (batchId: string): Promise<Blob> => {
    const response = await api.get(`/workflows/runs/patrol-batches/${batchId}/export.csv`, {
      responseType: 'blob',
    })
    return response.data
  },
  exportPatrolBatchReport: async (
    batchId: string,
    reportManifestId?: string,
  ): Promise<PatrolReportDownload> => {
    const response = await api.get(`/workflows/runs/patrol-batches/${batchId}/report.docx`, {
      responseType: 'blob',
      params: reportManifestId ? { report_manifest_id: reportManifestId } : undefined,
    })
    return {
      blob: response.data,
      filename: responseFilename(
        response.headers['content-disposition'],
        `${batchId}-leadership-report.docx`,
      ),
      status: response.headers['x-netops-report-status'],
    }
  },
  exportPatrolBatchTechnicalWorkbook: async (
    batchId: string,
    reportManifestId?: string,
  ): Promise<PatrolReportDownload> => {
    const response = await api.get(`/workflows/runs/patrol-batches/${batchId}/report.xlsx`, {
      responseType: 'blob',
      params: reportManifestId ? { report_manifest_id: reportManifestId } : undefined,
    })
    return {
      blob: response.data,
      filename: responseFilename(
        response.headers['content-disposition'],
        `${batchId}-technical-report.xlsx`,
      ),
      status: response.headers['x-netops-report-status'],
    }
  },
  exportPatrolBatchEvidencePackage: async (
    batchId: string,
    reportManifestId?: string,
  ): Promise<PatrolReportDownload> => {
    const response = await api.get(`/workflows/runs/patrol-batches/${batchId}/evidence.zip`, {
      responseType: 'blob',
      params: reportManifestId ? { report_manifest_id: reportManifestId } : undefined,
    })
    return {
      blob: response.data,
      filename: responseFilename(
        response.headers['content-disposition'],
        `${batchId}-evidence.zip`,
      ),
      status: response.headers['x-netops-report-status'],
    }
  },
  exportPatrolBatchRawData: async (batchId: string): Promise<Blob> => {
    const response = await api.get(
      `/workflows/runs/patrol-batches/${batchId}/raw-data.jsonl`,
      { responseType: 'blob' },
    )
    return response.data
  },
  exportPatrolBatchRawCli: async (
    batchId: string,
    reportManifestId?: string,
  ): Promise<Blob> => {
    const response = await api.get(
      `/workflows/runs/patrol-batches/${batchId}/raw-cli.zip`,
      {
        responseType: 'blob',
        params: reportManifestId ? { report_manifest_id: reportManifestId } : undefined,
      },
    )
    return response.data
  },
  exportPatrolBatchEvents: async (batchId: string): Promise<Blob> => {
    const response = await api.get(
      `/workflows/runs/patrol-batches/${batchId}/events.csv`,
      { responseType: 'blob' },
    )
    return response.data
  },
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

export const patrolApi = {
  listProductionFeedback: async (params: {
    page?: number
    page_size?: number
    status?: ProductionFeedbackStatus
    feedback_type?: ProductionFeedbackType
    runtime_bundle_version_id?: string
    patrol_batch_id?: string
  } = {}): Promise<ProductionFeedbackPage> => {
    const response = await api.get<ProductionFeedbackPage>(
      '/patrol/production-feedback',
      { params },
    )
    return response.data
  },
  getProductionFeedbackContext: async (
    executionId: string,
  ): Promise<ProductionFeedbackContext> => {
    const response = await api.get<ProductionFeedbackContext>(
      `/patrol/production-feedback/context/${executionId}`,
    )
    return response.data
  },
  submitProductionFeedback: async (
    data: {
      execution_id: string
      assessment_id: string
      feedback_type: ProductionFeedbackType
      statement: string
      event_occurrence_id?: string
      risk_occurrence_id?: string
    },
    idempotencyKey: string,
  ): Promise<ProductionFeedback> => {
    const response = await api.post<{ success: boolean; feedback: ProductionFeedback }>(
      '/patrol/production-feedback',
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.feedback
  },
  triageProductionFeedback: async (
    feedbackId: string,
    data: {
      outcome: 'confirmed' | 'not_confirmed' | 'inconclusive'
      reason: string
      work_item_kind?: ProductionFeedbackWorkItemKind
    },
    idempotencyKey: string,
  ): Promise<ProductionFeedback> => {
    const response = await api.post<{ success: boolean; feedback: ProductionFeedback }>(
      `/patrol/production-feedback/${feedbackId}/triage`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.feedback
  },
  linkProductionFeedbackCandidate: async (
    workItemId: string,
    data: {
      candidate_type: 'offline_job' | 'assessment_policy_version' | 'asset_gap'
      candidate_id: string
    },
    idempotencyKey: string,
  ): Promise<ProductionFeedbackWorkItem> => {
    const response = await api.post<{
      success: boolean
      work_item: ProductionFeedbackWorkItem
    }>(
      `/patrol/production-feedback/work-items/${workItemId}/candidate`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.work_item
  },
  getBatchReportManifest: async (
    batchId: string,
  ): Promise<PatrolReportReleaseView> => {
    const response = await api.get<PatrolReportReleaseView>(
      `/patrol/batches/${batchId}/report-manifest`,
    )
    return response.data
  },
  getReportContentReview: async (
    batchId: string,
    reportManifestId: string,
  ): Promise<PatrolReportContentReviewPreview> => {
    const response = await api.get<PatrolReportContentReviewPreview>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/content-review`,
    )
    return response.data
  },
  startReportContentReview: async (
    batchId: string,
    reportManifestId: string,
    idempotencyKey: string,
  ): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean
      review: PatrolReportContentReview
    }>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/start`,
      {},
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.review
  },
  listReportContentReviewItems: async (
    batchId: string,
    reportManifestId: string,
    params: {
      page?: number
      page_size?: number
      review_status?: PatrolReportContentReviewItemStatus
      category?: string
    } = {},
  ): Promise<PatrolReportContentReviewItemPage> => {
    const response = await api.get<PatrolReportContentReviewItemPage>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/items`,
      { params },
    )
    return response.data
  },
  decideReportContentReviewItems: async (
    batchId: string,
    reportManifestId: string,
    data: {
      review_item_ids: string[]
      decision: 'accepted' | 'rejected'
      review_comment: string
    },
    idempotencyKey: string,
  ): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean
      review: PatrolReportContentReview
    }>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/items/decision`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.review
  },
  acceptReportContent: async (
    batchId: string,
    reportManifestId: string,
    data: {
      acceptance_comment: string
      confirmations: PatrolReportReleaseView['required_content_confirmations']
      verified_artifact_sha256: Record<string, string>
    },
    idempotencyKey: string,
  ): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean
      review: PatrolReportContentReview
    }>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/accept`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data.review
  },
  listBatchReportClaims: async (
    batchId: string,
    params: {
      page?: number
      page_size?: number
      search?: string
      claim_scope?: PatrolReportClaim['claim_scope']
      assertion_level?: PatrolReportClaim['assertion_level']
    } = {},
  ): Promise<PatrolReportClaimPage> => {
    const response = await api.get<PatrolReportClaimPage>(
      `/patrol/batches/${batchId}/report-claims`,
      { params },
    )
    return response.data
  },
  listBatchReportVersions: async (
    batchId: string,
    page = 1,
    pageSize = 20,
  ): Promise<PatrolReportVersionPage> => {
    const response = await api.get<PatrolReportVersionPage>(
      `/patrol/batches/${batchId}/reports`,
      { params: { page, page_size: pageSize } },
    )
    return response.data
  },
  materializeBatchReport: async (
    batchId: string,
    idempotencyKey: string,
  ): Promise<PatrolReportMaterializationStatus> => {
    const response = await api.post<PatrolReportMaterializationStatus>(
      `/patrol/batches/${batchId}/reports/materialize`,
      {},
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  getBatchReportMaterializationStatus: async (
    batchId: string,
  ): Promise<PatrolReportMaterializationStatus> => {
    const response = await api.get<PatrolReportMaterializationStatus>(
      `/patrol/batches/${batchId}/reports/materialization-status`,
    )
    return response.data
  },
  releaseBatchReport: async (
    batchId: string,
    reportManifestId: string,
    data: {
      signoff_comment: string
      disclosure_confirmations: PatrolReportReleaseView['required_confirmations']
      verified_artifact_sha256: Record<string, string>
    },
    idempotencyKey: string,
  ): Promise<PatrolReportReleaseView> => {
    const response = await api.post<PatrolReportReleaseView>(
      `/patrol/batches/${batchId}/reports/${reportManifestId}/release`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  listRiskCases: async (params: {
    page?: number
    page_size?: number
    search?: string
    status?: RiskCaseStatus
    severity?: RiskSeverity
    remediation_status?: RiskRemediationStatus
    check_item_id?: string
    device_id?: string
    owner?: string
    overdue_only?: boolean
  } = {}): Promise<RiskCasePage> => {
    const response = await api.get<RiskCasePage>('/patrol/risk-cases', { params })
    return response.data
  },
  getRiskCase: async (
    riskCaseId: string,
    params: { occurrence_page?: number; occurrence_page_size?: number } = {},
  ): Promise<RiskCaseDetail> => {
    const response = await api.get<RiskCaseDetail>(
      `/patrol/risk-cases/${riskCaseId}`,
      { params },
    )
    return response.data
  },
  listRiskEpisodeAssociations: async (
    riskCaseId: string,
    params: {
      page?: number
      page_size?: number
      status?: RiskEpisodeAssociationStatus
    } = {},
  ): Promise<RiskEpisodeAssociationPage> => {
    const response = await api.get<RiskEpisodeAssociationPage>(
      `/patrol/risk-cases/${riskCaseId}/associations`,
      { params },
    )
    return response.data
  },
  proposeRiskEpisodeAssociation: async (
    riskCaseId: string,
    data: {
      related_risk_case_id: string
      relation_type: RiskEpisodeAssociationRelation
      reason: string
      evidence_refs: string[]
    },
    idempotencyKey: string,
  ): Promise<{ success: boolean; association: RiskEpisodeAssociation }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/associations`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  reviewRiskEpisodeAssociation: async (
    associationId: string,
    data: { decision: 'approved' | 'rejected'; reason: string },
    idempotencyKey: string,
  ): Promise<{ success: boolean; association: RiskEpisodeAssociation }> => {
    const response = await api.post(
      `/patrol/risk-episode-associations/${associationId}/review`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  revokeRiskEpisodeAssociation: async (
    associationId: string,
    data: { reason: string },
    idempotencyKey: string,
  ): Promise<{ success: boolean; association: RiskEpisodeAssociation }> => {
    const response = await api.post(
      `/patrol/risk-episode-associations/${associationId}/revoke`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  acknowledgeRiskCase: async (
    riskCaseId: string,
    data: { reason: string },
    idempotencyKey: string,
  ): Promise<{ success: boolean; risk_case: RiskCase }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/acknowledge`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  confirmRiskRecovery: async (
    riskCaseId: string,
    data: { recovery_candidate_id: string; reason: string },
    idempotencyKey: string,
  ): Promise<{
    success: boolean
    risk_case: RiskCase
    confirmation: RiskRecoveryCandidate & {
      confirmed_at?: string
      confirmed_by?: string
      idempotent_replay?: boolean
    }
  }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/confirm-recovery`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  createRiskRemediation: async (
    riskCaseId: string,
    data: { action_text: string; owner?: string; due_at?: string },
    idempotencyKey: string,
  ): Promise<{ success: boolean; remediation: RiskRemediation }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/remediation`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  updateRiskRemediation: async (
    riskCaseId: string,
    remediationId: string,
    data: {
      status: 'in_progress' | 'completed' | 'cancelled'
      reason: string
      evidence_refs?: string[]
    },
    idempotencyKey: string,
  ): Promise<{ success: boolean; remediation: RiskRemediation }> => {
    const response = await api.patch(
      `/patrol/risk-cases/${riskCaseId}/remediations/${remediationId}`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  acceptRiskRemediation: async (
    riskCaseId: string,
    remediationId: string,
    data: { reason: string; evidence_refs: string[] },
    idempotencyKey: string,
  ): Promise<{ success: boolean; remediation: RiskRemediation }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/remediations/${remediationId}/accept`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  closeRiskCase: async (
    riskCaseId: string,
    data: {
      disposition: 'remediation_accepted' | 'no_remediation_required'
      reason: string
      evidence_refs: string[]
    },
    idempotencyKey: string,
  ): Promise<{ success: boolean; risk_case: RiskCase }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/close`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
  suppressRiskCase: async (
    riskCaseId: string,
    data: { reason: string },
    idempotencyKey: string,
  ): Promise<{ success: boolean; risk_case: RiskCase }> => {
    const response = await api.post(
      `/patrol/risk-cases/${riskCaseId}/suppress`,
      data,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return response.data
  },
}

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
