// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { api, getChatWebSocketUrl, type AuthSession, API_BASE_URL } from '../config/api'

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
  candidate_contract: Record<string, unknown>
  active_contract: Record<string, unknown>
  rows: Array<{
    snapshot_id: string
    dataset_id: string
    device_id: string
    changed: boolean
    current?: { status: string; failure_code?: string; records?: unknown }
    proposed: { status: string; failure_code?: string; records?: unknown }
  }>
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

export interface ParserJobItem {
  id: string
  job_type: string
  vendor: string
  model?: string
  profile: string
  canonical_command: string
  status: string
  source_mode: 'parser_validation' | 'production'
  sample_count: number
  structure_variant_count?: number
  structure_signatures?: string[]
  compatibility_scope?: 'model' | 'family'
  attempts: number
  contract_revision_count?: number
  template_attempt_count?: number
  best_attempt_id?: string
  agent_state_json?: Record<string, unknown>
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
  sanitization_status: 'pending_sanitization' | 'sanitized' | 'failed'
  sanitization_version?: string
  sanitized_hash?: string
  redaction_summary_json?: Record<string, number>
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
  status: 'passed' | 'ready' | 'blocked'
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

export interface ParserRuleAssetReplay {
  success?: boolean
  sampled_change_count?: number
  matched_change_count?: number
  collision_count?: number
  event_count?: number
  event_types?: Record<string, number>
  matched_entity_types?: Record<string, number>
  top_devices?: Record<string, number>
  replacement_rule_ids?: string[]
  replacement_logical_keys?: string[]
  estimated_daily_event_count?: number
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
  scope_required?: boolean
  scope_mode?: string
  expected_run_ids?: string[]
  actual_run_ids?: string[]
  errors?: string[]
}

export interface ParserRuleAssetBundle {
  id: string
  status: string
  source_ref?: string | null
  observation_type?: string | null
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
  release_ready: boolean
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

export interface ParserRuleAssetBundlePage {
  items: ParserRuleAssetBundle[]
  total: number
  limit: number
  offset: number
}

export type OfflinePatrolJobType = 'parser_validation' | 'history_compare'

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
}

export interface OfflinePatrolJob {
  id: string
  job_type: OfflinePatrolJobType
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
  archived: boolean
  created_at: string
  updated_at: string
  completed_at?: string | null
  periods?: OfflinePatrolPeriod[]
  source_files?: OfflinePatrolSourceFile[]
  device_bindings?: OfflinePatrolDeviceBinding[]
  preview?: Record<string, unknown>
  result?: Record<string, unknown>
  blockers?: Array<Record<string, unknown>>
  error_message?: string | null
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
  digest: string
  activated_at?: string | null
  deprecated_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface ParserRuntimeRuleAssetBundle {
  ready: boolean
  observation_contracts: Record<string, Record<string, unknown>>
  change_policies: Record<string, ParserChangePolicy | Record<string, unknown>>
  event_rules: ParserEventRule[] | Array<Record<string, unknown>>
  missing_observation_contracts: string[]
  missing_change_policies: string[]
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

export interface ParserCoverage {
  templates_total: number
  templates_active: number
  jobs_total: number
  jobs_pending: number
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
  coverage?: Record<string, unknown>
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
  profile_version_id?: string | null
  selector: Record<string, unknown>
  command_count: number
  matched_device_count: number
  returned_device_count: number
  blocked_device_count: number
  ready_device_count: number
  eligible_command_count: number
  ready_command_count: number
  blocked_command_count: number
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
    eligible_count: number
    ready_count: number
    blocked_count: number
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

export const parserApi = {
  templates: async () => {
    const response = await api.get<{ items: ParserTemplateItem[] }>('/parser/templates/')
    return response.data.items
  },
  jobs: async () => {
    const response = await api.get<{ items: ParserJobItem[] }>('/parser/jobs/')
    return response.data.items
  },
  backfills: async () => {
    const response = await api.get<{ items: ParserBackfillJob[] }>('/parser/backfills/')
    return response.data.items
  },
  createBackfill: async (data: {
    template_id: string
    requested_by: string
    filters?: Record<string, unknown>
  }) => (await api.post<ParserBackfillJob>('/parser/backfills/', data)).data,
  dryRunBackfill: async (id: string) => (
    await api.post(`/parser/backfills/${id}/dry-run/`)
  ).data,
  commitBackfill: async (id: string, data: { actor: string; reason: string }) => (
    await api.post(`/parser/backfills/${id}/commit/`, data)
  ).data,
  sanitizeJob: async (id: string) => (
    await api.post(`/parser/jobs/${id}/sanitize/`)
  ).data,
  ruleGenerationJobs: async () => {
    const response = await api.get<{ items: ParserRuleGenerationJob[] }>('/parser/rule-generation-jobs/')
    return response.data.items
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
  observationContracts: async () => {
    const response = await api.get<{ items: ParserObservationContract[] }>('/parser/observation-contracts/')
    return response.data.items
  },
  changePolicies: async () => {
    const response = await api.get<{ items: ParserChangePolicy[] }>('/parser/change-policies/')
    return response.data.items
  },
  resolveRuntimeAssets: async (data: {
    observation_schemas?: Array<[string, string]>
    observation_types?: string[]
    include_event_rules?: boolean
  }): Promise<ParserRuntimeRuleAssetBundle> => {
    const response = await api.post<ParserRuntimeRuleAssetBundle>('/parser/runtime-assets/resolve/', data)
    return response.data
  },
  replayReports: async () => {
    const response = await api.get<{ items: ParserReplayReport[] }>('/parser/replay-reports/')
    return response.data.items
  },
  approvalAudit: async () => {
    const response = await api.get<{ items: ParserApprovalAudit[] }>('/parser/approval-audit/')
    return response.data.items
  },
  agentLoopAttempts: async () => {
    const response = await api.get<{ items: ParserAgentLoopAttempt[] }>('/parser/agent-loop-attempts/')
    return response.data.items
  },
  approveRuleGenerationJob: async (id: string, data: { actor?: string; reason?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/approve/`, data)
  ).data,
  rejectRuleGenerationJob: async (id: string, data: { actor?: string; reason?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/reject/`, data)
  ).data,
  deprecateRuleGenerationJob: async (id: string, data: { actor?: string; reason?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/deprecate/`, data)
  ).data,
  reviseRuleGenerationJob: async (id: string, data: { actor?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/revise/`, data)
  ).data,
  executeRuleGenerationRevisionJob: async (id: string, data: { actor?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/execute-revision/`, data)
  ).data,
  submitRuleGenerationJobCandidate: async (id: string, data: { actor?: string } = {}) => (
    await api.post(`/parser/rule-generation-jobs/${id}/submit/`, data)
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
      actor?: string
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
      actor?: string
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
  aliases: async () => {
    const response = await api.get<{ items: ParserCommandAlias[] }>('/parser/aliases/')
    return response.data.items
  },
  deviceProfiles: async () => {
    const response = await api.get<{ items: ParserDeviceProfile[] }>('/parser/device-profiles/')
    return response.data.items
  },
  identitySignatures: async () => {
    const response = await api.get<{ items: ParserIdentitySignature[] }>('/parser/identity-signatures/')
    return response.data.items
  },
  coverage: async () => {
    const response = await api.get<ParserCoverage>('/parser/coverage/')
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
  activate: async (id: string) => (await api.post(`/parser/templates/${id}/activate/`)).data,
  reject: async (id: string) => (await api.post(`/parser/templates/${id}/reject/`, { actor: 'admin-ui', reason: 'Rejected during manual review' })).data,
  deprecate: async (id: string) => (await api.post(`/parser/templates/${id}/deprecate/`, { actor: 'admin-ui', reason: 'Deprecated during manual review' })).data,
  retryJob: async (id: string) => (await api.post(`/parser/jobs/${id}/retry/`)).data,
  cancelJob: async (id: string) => (await api.post(`/parser/jobs/${id}/cancel/`)).data,
  deleteJob: async (id: string) => (await api.delete(`/parser/jobs/${id}/`)).data,
  bulkDeleteJobs: async (job_ids: string[]) => (
    await api.post('/parser/jobs/bulk-delete/', { job_ids })
  ).data,
  dispatchJob: async (id: string) => (await api.post(`/parser/jobs/${id}/dispatch/`)).data,
  dispatch: async () => (await api.post('/parser/dispatch/')).data,
  dispatchControl: async () => {
    const response = await api.get<ParserDispatchControl>('/parser/dispatch-control/')
    return response.data
  },
  setDispatchControl: async (enabled: boolean) => (
    await api.post<ParserDispatchControl>('/parser/dispatch-control/', { enabled })
  ).data,
  bootstrap: async () => (await api.post('/parser/bootstrap/')).data,
  eventRules: async () => {
    const response = await api.get<{ items: ParserEventRule[] }>('/parser/event-rules/')
    return response.data.items
  },
  bootstrapEventRules: async () => (await api.post('/parser/event-rules/bootstrap/')).data,
  upsertEventRule: async (data: {
    name: string
    event_type: string
    severity: string
    condition: Record<string, unknown>
    description?: string
    status?: string
  }) => (await api.post('/parser/event-rules/', data)).data,
  activateEventRule: async (versionId: string) =>
    (await api.post(`/parser/event-rules/${versionId}/activate/`, {})).data,
  deprecateEventRule: async (versionId: string) =>
    (await api.post(`/parser/event-rules/${versionId}/deprecate/`, {})).data,
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
  commandProfiles: async () => {
    const response = await api.get<{ items: ParserCommandProfile[] }>('/parser/command-profiles/')
    return response.data.items
  },
  upsertCommandProfile: async (data: {
    name: string
    action?: string
    commands: string[]
    status?: string
  }) => (await api.post('/parser/command-profiles/', data)).data,
  activateCommandProfile: async (profileVersionId: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/activate/`, {})).data,
  deprecateCommandProfile: async (profileVersionId: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/deprecate/`, {})).data,
  rollbackCommandProfile: async (profileVersionId: string) =>
    (await api.post(`/parser/command-profiles/${profileVersionId}/rollback/`, {})).data,
  deleteCommandProfile: async (profileVersionId: string) =>
    (await api.delete(`/parser/command-profiles/${profileVersionId}/`)).data,
  commandProfileBindings: async () => {
    const response = await api.get<{ items: ParserCommandProfileBinding[] }>('/parser/command-profile-bindings/')
    return response.data.items
  },
  upsertCommandProfileBinding: async (data: {
    profile_version_id: string
    selector: Record<string, unknown>
    priority?: number
    status?: string
  }) => (await api.post('/parser/command-profile-bindings/', data)).data,
  enableCommandProfileBinding: async (bindingId: string) =>
    (await api.post(`/parser/command-profile-bindings/${bindingId}/enable/`, {})).data,
  disableCommandProfileBinding: async (bindingId: string) =>
    (await api.post(`/parser/command-profile-bindings/${bindingId}/disable/`, {})).data,
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

export const offlinePatrolApi = {
  list: async (params: {
    page?: number
    page_size?: number
    search?: string
    status?: string
    job_type?: OfflinePatrolJobType
    archived?: boolean
  } = {}): Promise<OfflinePatrolJobPage> => (
    await api.get<OfflinePatrolJobPage>('/patrol/offline-jobs/', { params })
  ).data,
  get: async (id: string): Promise<OfflinePatrolJob> => (
    await api.get<OfflinePatrolJob>(`/patrol/offline-jobs/${id}/`)
  ).data,
  analysis: async (id: string): Promise<OfflinePatrolAnalysisSummary> => (
    await api.get<OfflinePatrolAnalysisSummary>(`/patrol/offline-jobs/${id}/analysis-summary/`, {
      timeout: 300000,
    })
  ).data,
  create: async (data: {
    job_type: OfflinePatrolJobType
    name: string
    periods: Array<{ role: string; label: string; observed_at: string }>
    vendor?: string
    model?: string
    publish_events?: boolean
    idempotency_key: string
  }): Promise<OfflinePatrolJob> => (
    await api.post<OfflinePatrolJob>('/patrol/offline-jobs/', data)
  ).data,
  attachSource: async (id: string, data: {
    period_role: string
    file_id: string
    relative_path: string
    sha256?: string
    size_bytes: number
  }): Promise<OfflinePatrolSourceFile> => (
    await api.post<OfflinePatrolSourceFile>(`/patrol/offline-jobs/${id}/sources/`, data)
  ).data,
  removeSource: async (id: string, sourceId: string) => (
    await api.delete(`/patrol/offline-jobs/${id}/sources/${sourceId}/`)
  ).data,
  preview: async (id: string) => (
    await api.post(`/patrol/offline-jobs/${id}/preview/`, {})
  ).data,
  confirmBindings: async (id: string, bindings: Array<{ binding_id: string; inventory_device_id?: string | null }>) => (
    await api.post(`/patrol/offline-jobs/${id}/device-bindings/confirm/`, { bindings })
  ).data,
  execute: async (id: string, idempotencyKey: string) => (
    await api.post(`/patrol/offline-jobs/${id}/execute/`, { idempotency_key: idempotencyKey })
  ).data,
  cancel: async (id: string) => (await api.post(`/patrol/offline-jobs/${id}/cancel/`, {})).data,
  retry: async (id: string) => (await api.post(`/patrol/offline-jobs/${id}/retry/`, {})).data,
  archive: async (id: string) => (await api.post(`/patrol/offline-jobs/${id}/archive/`, {})).data,
  restore: async (id: string) => (await api.post(`/patrol/offline-jobs/${id}/restore/`, {})).data,
  artifact: async (id: string, kind: 'raw' | 'changes' | 'events' | 'report'): Promise<Blob> => (
    await api.get(`/patrol/offline-jobs/${id}/artifacts/${kind}/`, {
      responseType: 'blob',
      timeout: 300000,
    })
  ).data as Blob,
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
  launchPatrol: async (data: DevicePatrolLaunchRequest) => {
    const response = await api.post<DevicePatrolLaunchResult>('/devices/patrol/launch/', data)
    return response.data
  },
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
  connection_succeeded_count?: number
  connection_failed_count?: number
  connection_pending_count?: number
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

export interface PatrolBatchResultSummary {
  patrol_batch_id: string
  run_count: number
  device_count: number
  change_count: number
  event_count: number
  current_finding_count?: number
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
    execution_ledger_url?: string
    raw_cli_url?: string
    raw_data_url?: string
    events_url?: string
  }
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
  getPatrolBatchSummary: async (batchId: string): Promise<PatrolBatchResultSummary> => {
    const response = await api.get<PatrolBatchResultSummary>(`/workflows/runs/patrol-batches/${batchId}/summary/`)
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
  exportPatrolBatchReport: async (batchId: string): Promise<Blob> => {
    const response = await api.get(`/workflows/runs/patrol-batches/${batchId}/report.docx`, {
      responseType: 'blob',
    })
    return response.data
  },
  exportPatrolBatchRawData: async (batchId: string): Promise<Blob> => {
    const response = await api.get(
      `/workflows/runs/patrol-batches/${batchId}/raw-data.jsonl`,
      { responseType: 'blob' },
    )
    return response.data
  },
  exportPatrolBatchRawCli: async (batchId: string): Promise<Blob> => {
    const response = await api.get(
      `/workflows/runs/patrol-batches/${batchId}/raw-cli.zip`,
      { responseType: 'blob' },
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
