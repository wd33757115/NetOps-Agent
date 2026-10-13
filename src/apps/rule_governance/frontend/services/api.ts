// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { api } from '@netops/config/api'
import { createOfflineJobApi } from '@netops/services/offlineJobApi'
import type {
  AdapterFieldsSaved,
  AdapterFieldsState,
  AdapterProjectionCandidateInput,
  AdapterProjectionCandidateResult,
  AdapterProjectionCutoverAuthorizationResult,
  AdapterProjectionCutoverReadiness,
  AdapterProjectionPreviewResult,
  AdapterProjectionReplayAttestation,
  CanonicalObservationCatalog,
  CanonicalRuleCandidates,
  CanonicalRuleContext,
  CheckItemCatalogChange,
  CheckItemCatalogCheckItemDeleteResult,
  CheckItemDeletePreview,
  CheckItemRollbackPreview,
  RuleGenerationGuide,
  CheckItemEventList,
  CheckItemEventTally,
  CheckItemCatalogCheckItemDraftInput,
  CheckItemCatalogImpact,
  CheckItemCatalogImplementationDeleteResult,
  CheckItemCatalogImplementationDraftInput,
  CheckItemCatalogPublicationOptions,
  CheckItemProductionGroupPage,
  CheckItemRulePreview,
  CheckItemOverview,
  CheckItemReleasePreview,
  CheckItemRulesInForce,
  CheckItemRuntimeBundleHealthPage,
  CheckItemRuntimeBundleHealthStatus,
  CheckItemRuntimeBundleVersion,
  OfflineCommandAssetUnitDetail,
  OfflineCommandAssetUnitPage,
  OfflineRuleAssetPipeline,
  ParserAgentLoopAttempt,
  ParserApprovalAuditPage,
  ParserAssetGap,
  ParserBackfillJob,
  ParserChangePolicy,
  ParserCheckItem,
  ParserCommandAlias,
  ParserCommandProfile,
  ParserCommandProfileBinding,
  ParserCommandProfileImpactPreview,
  ParserCoverage,
  ParserDatasetCoverage,
  ParserDeviceProfile,
  ParserDispatchControl,
  ParserEffectivePlanPreview,
  ParserEventRule,
  ParserEventRuleReplayResult,
  ParserEventRuleTestResult,
  ParserGenerationAttempt,
  ParserIdentitySignature,
  ParserImportPreview,
  ParserJobItem,
  ParserPage,
  ParserPageParams,
  ParserReplayReport,
  ParserRuleAssetBundle,
  ParserRuleAssetBundlePage,
  ParserRuleAssetEngineeringDraft,
  ParserRuleGenerationJob,
  ParserRuleGenerationJobPreview,
  ParserTemplateComparison,
  ParserTemplateExport,
  ParserTemplateItem,
  ParserTemplatePreview,
  ParserValueMappingConfig,
  ParserValueMappingRule,
  ParserWorkItem,
  RolloutCampaign,
  RolloutCampaignFreezePayload,
  RolloutCampaignPage,
  RolloutCampaignStatus,
  RolloutStageExecution,
  RuntimeBundleAssemblyPayload,
  RuntimeBundleAssemblyPreview
} from '@netops/services/api'

export const parserApi = {
  publishedAssetAction: async (data: {
    kind: 'parser' | 'policy' | 'event'
    action: 'deprecate' | 'delete'
    ids: string[]
    reason?: string
  }): Promise<{ succeeded: number; failed: number; results: Array<{ id: string; success: boolean; error?: string }> }> => (
    await api.post('/parser/published-assets/bulk-action/', data)
  ).data,
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
  changePolicies: async (params: ParserPageParams = {}): Promise<ParserPage<ParserChangePolicy>> => {
    const response = await api.get<ParserPage<ParserChangePolicy>>('/parser/change-policies/', { params })
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
  previewCheckItemRules: async (data: {
    id: string
    result_mode: CheckItemCatalogCheckItemDraftInput['result_mode']
    canonical_contract: Record<string, unknown>
  }): Promise<CheckItemRulePreview> => (
    await api.post<CheckItemRulePreview>('/parser/catalog/check-items/rule-preview/', data)
  ).data,
  checkItemOverview: async (checkItemId: string): Promise<CheckItemOverview> => (
    await api.get<CheckItemOverview>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/overview/`,
    )
  ).data,
  checkItemEvents: async (checkItemId: string, eventType: string): Promise<CheckItemEventList> => (
    await api.get<CheckItemEventList>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/events/`,
      { params: { event_type: eventType, limit: 10 } },
    )
  ).data,
  closeRetiredRuleEvents: async (checkItemId: string): Promise<{ events_closed: CheckItemEventTally[] }> => (
    await api.post<{ events_closed: CheckItemEventTally[] }>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/retired-events/close/`,
    )
  ).data,
  ruleGenerationGuide: async (): Promise<RuleGenerationGuide> => (
    await api.get<RuleGenerationGuide>('/parser/catalog/rule-generation-guide/')
  ).data,
  adapterFields: async (implementationId: string, checkItemId: string): Promise<AdapterFieldsState> => (
    await api.get<AdapterFieldsState>(
      `/parser/catalog/adapters/${encodeURIComponent(implementationId)}/fields/`,
      { params: { check_item_id: checkItemId } },
    )
  ).data,
  tryAdapterFields: async (
    implementationId: string, checkItemId: string, fields: string[],
  ): Promise<AdapterFieldsState> => (
    await api.post<AdapterFieldsState>(
      `/parser/catalog/adapters/${encodeURIComponent(implementationId)}/fields/trial/`,
      { check_item_id: checkItemId, fields },
    )
  ).data,
  saveAdapterFields: async (
    implementationId: string, checkItemId: string, fields: string[],
  ): Promise<AdapterFieldsSaved> => (
    await api.post<AdapterFieldsSaved>(
      `/parser/catalog/adapters/${encodeURIComponent(implementationId)}/fields/save/`,
      { check_item_id: checkItemId, fields },
    )
  ).data,
  previewCheckItemRollback: async (checkItemId: string): Promise<CheckItemRollbackPreview> => (
    await api.post<CheckItemRollbackPreview>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/rollback-preview/`,
    )
  ).data,
  discardCatalogDraft: async (changeId: string): Promise<CheckItemCatalogChange> => (
    await api.post<CheckItemCatalogChange>(`/parser/catalog/changes/${changeId}/discard/`)
  ).data,
  previewCheckItemDelete: async (checkItemId: string): Promise<CheckItemDeletePreview> => (
    await api.post<CheckItemDeletePreview>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/delete-preview/`,
    )
  ).data,
  checkItemRulesInForce: async (checkItemId: string): Promise<CheckItemRulesInForce> => (
    await api.get<CheckItemRulesInForce>(
      `/parser/catalog/check-items/${encodeURIComponent(checkItemId)}/active-rules/`,
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
  previewCheckItemRelease: async (changeId: string): Promise<CheckItemReleasePreview> => (
    await api.post<CheckItemReleasePreview>(`/parser/catalog/changes/${changeId}/release-preview/`)
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
    payload: { check_item_id: string; auto_continue?: boolean },
  ): Promise<{
    success: boolean
    association: {
      check_item_id: string
      check_item_version_id: string
    }
    parser_job_id?: string | null
    autopilot_error?: string
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
