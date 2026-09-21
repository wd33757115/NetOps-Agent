import { api, API_BASE_URL } from '@netops/config/api'
import { createOfflineJobApi } from '@netops/services/offlineJobApi'
import type { PatrolCurrentAuthority, ProductionFeedbackStatus, ProductionFeedbackType, ProductionFeedbackPage, ProductionFeedbackContext, ProductionFeedback, ProductionFeedbackWorkItemKind, ProductionFeedbackWorkItem, PatrolReportReleaseView, PatrolReportContentReviewPreview, PatrolReportContentReview, PatrolReportContentReviewItemStatus, PatrolReportContentReviewItemPage, PatrolReportClaim, PatrolReportClaimPage, PatrolReportVersionPage, PatrolReportMaterializationStatus, RiskCaseStatus, RiskSeverity, RiskRemediationStatus, RiskCasePage, RiskCaseDetail, RiskEpisodeAssociationStatus, RiskEpisodeAssociationPage, RiskEpisodeAssociationRelation, RiskEpisodeAssociation, RiskCase, RiskRecoveryCandidate, RiskRemediation, DeviceListResponse, DevicePatrolLaunchRequest, DevicePatrolPreview, PatrolProductionReadiness, DevicePatrolLaunchResult, DeviceConnectionHealthResponse, DeviceExecutionPlanResponse, DeviceEvidenceTimelineResponse, DeviceDynamicGroup, WorkflowRunSummary, PatrolBatchPage, PatrolBatchGovernanceResult, PatrolBatchCancellationResult, PatrolBatchResultSummary, PatrolBatchCheckResultPage, PatrolBatchCheckResultDetail, PatrolBatchDevicePage, PatrolReportDownload } from '@netops/services/api'
const responseFilename = (contentDisposition: unknown, fallback: string) => {
  const value = String(contentDisposition || '');
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.replace(/^["']|["']$/g, ''));
    } catch {
      return encoded;
    }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
};
const OFFLINE_PATROL_JOBS_BASE = "/apps/inspection/patrol/offline-jobs";
export const offlinePatrolApi = {
  ...createOfflineJobApi(OFFLINE_PATROL_JOBS_BASE),
  currentAuthority: async (id: string): Promise<PatrolCurrentAuthority> => (await api.get<PatrolCurrentAuthority>(`${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/`)).data,
  prepareCurrentAuthority: async (id: string, data: {
    completeness_statement: string;
    completeness_evidence_refs: string[];
    observed_at_evidence_refs: string[];
    collection_tool: string;
    collector_identity: string;
    scope_contract?: Record<string, unknown>;
  }, idempotencyKey: string): Promise<PatrolCurrentAuthority> => (await api.post<PatrolCurrentAuthority>(`${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/prepare/`, data, {
    headers: {
      'Idempotency-Key': idempotencyKey
    }
  })).data,
  attestCurrentAuthority: async (id: string, data: {
    request_digest: string;
    reason: string;
  }, idempotencyKey: string): Promise<Record<string, unknown>> => (await api.post(`${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/attest/`, data, {
    headers: {
      'Idempotency-Key': idempotencyKey
    }
  })).data,
  revokeCurrentAuthority: async (id: string, reason: string, idempotencyKey: string) => (await api.post(`${OFFLINE_PATROL_JOBS_BASE}/${id}/current-authority/revoke/`, {
    reason
  }, {
    headers: {
      'Idempotency-Key': idempotencyKey
    }
  })).data
};
export const patrolApi = {
  listProductionFeedback: async (params: {
    page?: number;
    page_size?: number;
    status?: ProductionFeedbackStatus;
    feedback_type?: ProductionFeedbackType;
    runtime_bundle_version_id?: string;
    patrol_batch_id?: string;
  } = {}): Promise<ProductionFeedbackPage> => {
    const response = await api.get<ProductionFeedbackPage>("/apps/inspection/patrol/production-feedback", {
      params
    });
    return response.data;
  },
  getProductionFeedbackContext: async (executionId: string): Promise<ProductionFeedbackContext> => {
    const response = await api.get<ProductionFeedbackContext>(`/apps/inspection/patrol/production-feedback/context/${executionId}`);
    return response.data;
  },
  submitProductionFeedback: async (data: {
    execution_id: string;
    assessment_id: string;
    feedback_type: ProductionFeedbackType;
    statement: string;
    event_occurrence_id?: string;
    risk_occurrence_id?: string;
  }, idempotencyKey: string): Promise<ProductionFeedback> => {
    const response = await api.post<{
      success: boolean;
      feedback: ProductionFeedback;
    }>("/apps/inspection/patrol/production-feedback", data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.feedback;
  },
  triageProductionFeedback: async (feedbackId: string, data: {
    outcome: 'confirmed' | 'not_confirmed' | 'inconclusive';
    reason: string;
    work_item_kind?: ProductionFeedbackWorkItemKind;
  }, idempotencyKey: string): Promise<ProductionFeedback> => {
    const response = await api.post<{
      success: boolean;
      feedback: ProductionFeedback;
    }>(`/apps/inspection/patrol/production-feedback/${feedbackId}/triage`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.feedback;
  },
  linkProductionFeedbackCandidate: async (workItemId: string, data: {
    candidate_type: 'offline_job' | 'assessment_policy_version' | 'asset_gap';
    candidate_id: string;
  }, idempotencyKey: string): Promise<ProductionFeedbackWorkItem> => {
    const response = await api.post<{
      success: boolean;
      work_item: ProductionFeedbackWorkItem;
    }>(`/apps/inspection/patrol/production-feedback/work-items/${workItemId}/candidate`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.work_item;
  },
  getBatchReportManifest: async (batchId: string): Promise<PatrolReportReleaseView> => {
    const response = await api.get<PatrolReportReleaseView>(`/apps/inspection/patrol/batches/${batchId}/report-manifest`);
    return response.data;
  },
  getReportContentReview: async (batchId: string, reportManifestId: string): Promise<PatrolReportContentReviewPreview> => {
    const response = await api.get<PatrolReportContentReviewPreview>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/content-review`);
    return response.data;
  },
  startReportContentReview: async (batchId: string, reportManifestId: string, idempotencyKey: string): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean;
      review: PatrolReportContentReview;
    }>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/start`, {}, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.review;
  },
  listReportContentReviewItems: async (batchId: string, reportManifestId: string, params: {
    page?: number;
    page_size?: number;
    review_status?: PatrolReportContentReviewItemStatus;
    category?: string;
  } = {}): Promise<PatrolReportContentReviewItemPage> => {
    const response = await api.get<PatrolReportContentReviewItemPage>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/items`, {
      params
    });
    return response.data;
  },
  decideReportContentReviewItems: async (batchId: string, reportManifestId: string, data: {
    review_item_ids: string[];
    decision: 'accepted' | 'rejected';
    review_comment: string;
  }, idempotencyKey: string): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean;
      review: PatrolReportContentReview;
    }>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/items/decision`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.review;
  },
  acceptReportContent: async (batchId: string, reportManifestId: string, data: {
    acceptance_comment: string;
    confirmations: PatrolReportReleaseView['required_content_confirmations'];
    verified_artifact_sha256: Record<string, string>;
  }, idempotencyKey: string): Promise<PatrolReportContentReview> => {
    const response = await api.post<{
      success: boolean;
      review: PatrolReportContentReview;
    }>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/content-review/accept`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data.review;
  },
  listBatchReportClaims: async (batchId: string, params: {
    page?: number;
    page_size?: number;
    search?: string;
    claim_scope?: PatrolReportClaim['claim_scope'];
    assertion_level?: PatrolReportClaim['assertion_level'];
  } = {}): Promise<PatrolReportClaimPage> => {
    const response = await api.get<PatrolReportClaimPage>(`/apps/inspection/patrol/batches/${batchId}/report-claims`, {
      params
    });
    return response.data;
  },
  listBatchReportVersions: async (batchId: string, page = 1, pageSize = 20): Promise<PatrolReportVersionPage> => {
    const response = await api.get<PatrolReportVersionPage>(`/apps/inspection/patrol/batches/${batchId}/reports`, {
      params: {
        page,
        page_size: pageSize
      }
    });
    return response.data;
  },
  materializeBatchReport: async (batchId: string, idempotencyKey: string): Promise<PatrolReportMaterializationStatus> => {
    const response = await api.post<PatrolReportMaterializationStatus>(`/apps/inspection/patrol/batches/${batchId}/reports/materialize`, {}, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  getBatchReportMaterializationStatus: async (batchId: string): Promise<PatrolReportMaterializationStatus> => {
    const response = await api.get<PatrolReportMaterializationStatus>(`/apps/inspection/patrol/batches/${batchId}/reports/materialization-status`);
    return response.data;
  },
  releaseBatchReport: async (batchId: string, reportManifestId: string, data: {
    signoff_comment: string;
    disclosure_confirmations: PatrolReportReleaseView['required_confirmations'];
    verified_artifact_sha256: Record<string, string>;
  }, idempotencyKey: string): Promise<PatrolReportReleaseView> => {
    const response = await api.post<PatrolReportReleaseView>(`/apps/inspection/patrol/batches/${batchId}/reports/${reportManifestId}/release`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  listRiskCases: async (params: {
    page?: number;
    page_size?: number;
    search?: string;
    status?: RiskCaseStatus;
    severity?: RiskSeverity;
    remediation_status?: RiskRemediationStatus;
    check_item_id?: string;
    device_id?: string;
    owner?: string;
    overdue_only?: boolean;
  } = {}): Promise<RiskCasePage> => {
    const response = await api.get<RiskCasePage>("/apps/inspection/patrol/risk-cases", {
      params
    });
    return response.data;
  },
  getRiskCase: async (riskCaseId: string, params: {
    occurrence_page?: number;
    occurrence_page_size?: number;
  } = {}): Promise<RiskCaseDetail> => {
    const response = await api.get<RiskCaseDetail>(`/apps/inspection/patrol/risk-cases/${riskCaseId}`, {
      params
    });
    return response.data;
  },
  listRiskEpisodeAssociations: async (riskCaseId: string, params: {
    page?: number;
    page_size?: number;
    status?: RiskEpisodeAssociationStatus;
  } = {}): Promise<RiskEpisodeAssociationPage> => {
    const response = await api.get<RiskEpisodeAssociationPage>(`/apps/inspection/patrol/risk-cases/${riskCaseId}/associations`, {
      params
    });
    return response.data;
  },
  proposeRiskEpisodeAssociation: async (riskCaseId: string, data: {
    related_risk_case_id: string;
    relation_type: RiskEpisodeAssociationRelation;
    reason: string;
    evidence_refs: string[];
  }, idempotencyKey: string): Promise<{
    success: boolean;
    association: RiskEpisodeAssociation;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/associations`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  reviewRiskEpisodeAssociation: async (associationId: string, data: {
    decision: 'approved' | 'rejected';
    reason: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    association: RiskEpisodeAssociation;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-episode-associations/${associationId}/review`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  revokeRiskEpisodeAssociation: async (associationId: string, data: {
    reason: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    association: RiskEpisodeAssociation;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-episode-associations/${associationId}/revoke`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  acknowledgeRiskCase: async (riskCaseId: string, data: {
    reason: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    risk_case: RiskCase;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/acknowledge`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  confirmRiskRecovery: async (riskCaseId: string, data: {
    recovery_candidate_id: string;
    reason: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    risk_case: RiskCase;
    confirmation: RiskRecoveryCandidate & {
      confirmed_at?: string;
      confirmed_by?: string;
      idempotent_replay?: boolean;
    };
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/confirm-recovery`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  createRiskRemediation: async (riskCaseId: string, data: {
    action_text: string;
    owner?: string;
    due_at?: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    remediation: RiskRemediation;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/remediation`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  updateRiskRemediation: async (riskCaseId: string, remediationId: string, data: {
    status: 'in_progress' | 'completed' | 'cancelled';
    reason: string;
    evidence_refs?: string[];
  }, idempotencyKey: string): Promise<{
    success: boolean;
    remediation: RiskRemediation;
  }> => {
    const response = await api.patch(`/apps/inspection/patrol/risk-cases/${riskCaseId}/remediations/${remediationId}`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  acceptRiskRemediation: async (riskCaseId: string, remediationId: string, data: {
    reason: string;
    evidence_refs: string[];
  }, idempotencyKey: string): Promise<{
    success: boolean;
    remediation: RiskRemediation;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/remediations/${remediationId}/accept`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  closeRiskCase: async (riskCaseId: string, data: {
    disposition: 'remediation_accepted' | 'no_remediation_required';
    reason: string;
    evidence_refs: string[];
  }, idempotencyKey: string): Promise<{
    success: boolean;
    risk_case: RiskCase;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/close`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  },
  suppressRiskCase: async (riskCaseId: string, data: {
    reason: string;
  }, idempotencyKey: string): Promise<{
    success: boolean;
    risk_case: RiskCase;
  }> => {
    const response = await api.post(`/apps/inspection/patrol/risk-cases/${riskCaseId}/suppress`, data, {
      headers: {
        'Idempotency-Key': idempotencyKey
      }
    });
    return response.data;
  }
};
export const inspectionDeviceApi = {
  list: async (params: Record<string, string | number | undefined> = {}) => {
    const response = await api.get<DeviceListResponse>("/apps/inspection/devices/", {
      params
    });
    return response.data;
  },
  groups: async () => {
    const response = await api.get<{
      items: string[];
    }>("/apps/inspection/devices/groups/");
    return response.data.items;
  },
  previewPatrol: async (data: DevicePatrolLaunchRequest) => {
    const response = await api.post<DevicePatrolPreview>("/apps/inspection/devices/patrol/preview/", data);
    return response.data;
  },
  patrolReadiness: async (params: {
    workflow_name?: string;
    launch_mode?: string;
  } = {}) => {
    const response = await api.get<PatrolProductionReadiness>("/apps/inspection/devices/patrol/readiness/", {
      params
    });
    return response.data;
  },
  launchPatrol: async (data: DevicePatrolLaunchRequest) => {
    const response = await api.post<DevicePatrolLaunchResult>("/apps/inspection/devices/patrol/launch/", data);
    return response.data;
  },
  getPatrolBatchLiveEventsUrl: (batchId: string) => `${API_BASE_URL}/apps/inspection/devices/patrol/batches/${batchId}/events/`,
  connectionHealth: async (deviceId: string, params: {
    limit?: number;
  } = {}) => {
    const response = await api.get<DeviceConnectionHealthResponse>(`/apps/inspection/devices/${deviceId}/connection-health/`, {
      params
    });
    return response.data;
  },
  executionPlans: async (deviceId: string, params: {
    limit?: number;
  } = {}) => {
    const response = await api.get<DeviceExecutionPlanResponse>(`/apps/inspection/devices/${deviceId}/execution-plans/`, {
      params
    });
    return response.data;
  },
  evidenceTimeline: async (deviceId: string, params: {
    limit?: number;
    workflow_run_id?: string;
    patrol_batch_id?: string;
  } = {}) => {
    const response = await api.get<DeviceEvidenceTimelineResponse>(`/apps/inspection/devices/${deviceId}/evidence-timeline/`, {
      params
    });
    return response.data;
  },
  dynamicGroups: async (params: {
    status?: string;
  } = {}) => {
    const response = await api.get<{
      items: DeviceDynamicGroup[];
    }>("/apps/inspection/devices/dynamic-groups/", {
      params
    });
    return response.data.items;
  }
};
export const inspectionBatchApi = {
  listRuns: async (params?: {
    limit?: number;
    template_name?: string;
    ticket_id?: string;
    source?: string;
    status?: string;
    device_id?: string;
    patrol_batch_id?: string;
  }): Promise<WorkflowRunSummary[]> => {
    const response = await api.get<WorkflowRunSummary[]>("/apps/inspection/legacy-runs", {
      params
    });
    return response.data;
  },
  listPatrolBatches: async (params?: {
    limit?: number;
    offset?: number;
    include_archived?: boolean;
    search?: string;
    status?: string;
  }): Promise<PatrolBatchPage> => {
    const response = await api.get<PatrolBatchPage>("/apps/inspection/batches/", {
      params
    });
    const items = response.data.items || [];
    const limit = response.data.limit || params?.limit || 25;
    const offset = response.data.offset || params?.offset || 0;
    const total = response.data.total ?? items.length;
    return {
      ...response.data,
      items,
      total,
      limit,
      offset,
      page: response.data.page || Math.floor(offset / limit) + 1,
      page_size: response.data.page_size || limit,
      page_count: response.data.page_count ?? (total ? Math.ceil(total / limit) : 0)
    };
  },
  archivePatrolBatch: async (batchId: string, data?: {
    reason?: string;
    keep_until?: string;
    force?: boolean;
  }): Promise<PatrolBatchGovernanceResult> => {
    const response = await api.post<PatrolBatchGovernanceResult>(`/apps/inspection/batches/${batchId}/archive/`, data || {});
    return response.data;
  },
  restorePatrolBatch: async (batchId: string, data?: {
    reason?: string;
    keep_until?: string;
  }): Promise<PatrolBatchGovernanceResult> => {
    const response = await api.post<PatrolBatchGovernanceResult>(`/apps/inspection/batches/${batchId}/restore/`, data || {});
    return response.data;
  },
  cancelPatrolBatch: async (batchId: string, data: {
    reason: string;
  }): Promise<PatrolBatchCancellationResult> => {
    const response = await api.post<PatrolBatchCancellationResult>(`/apps/inspection/batches/${batchId}/cancel/`, data);
    return response.data;
  },
  getPatrolBatchSummary: async (batchId: string): Promise<PatrolBatchResultSummary> => {
    const response = await api.get<PatrolBatchResultSummary>(`/apps/inspection/batches/${batchId}/summary/`);
    return response.data;
  },
  listPatrolBatchCheckResults: async (batchId: string, params?: {
    limit?: number;
    offset?: number;
    cursor?: string;
    search?: string;
    assessment_status?: string;
    completeness_status?: string;
    source_identity_status?: string;
    output_quality_status?: string;
    freshness_status?: string;
    coverage_status?: string;
  }): Promise<PatrolBatchCheckResultPage> => {
    const response = await api.get<PatrolBatchCheckResultPage>(`/apps/inspection/batches/${batchId}/check-results`, {
      params
    });
    return response.data;
  },
  getPatrolBatchCheckResultDetail: async (batchId: string, executionId: string, params: {
    entry_limit?: number;
    entry_offset?: number;
  } = {}): Promise<PatrolBatchCheckResultDetail> => {
    const response = await api.get<PatrolBatchCheckResultDetail>(`/apps/inspection/batches/${batchId}/check-results/${executionId}`, {
      params
    });
    return response.data;
  },
  listPatrolBatchDevices: async (batchId: string, params?: {
    limit?: number;
    offset?: number;
    search?: string;
    selection_status?: string;
  }): Promise<PatrolBatchDevicePage> => {
    const response = await api.get<PatrolBatchDevicePage>(`/apps/inspection/batches/${batchId}/apps/inspection/devices`, {
      params
    });
    return response.data;
  },
  retryPatrolBatch: async (batchId: string): Promise<{
    success: boolean;
    patrol_batch_id: string;
    retryable_count: number;
    retried_count: number;
    new_patrol_batch_id?: string;
    runs: Array<{
      run_id: string;
      retry_of_run_id: string;
      device_id?: string;
      device_name?: string;
    }>;
  }> => {
    const response = await api.post(`/apps/inspection/batches/${batchId}/retry/`, {
      request_id: crypto.randomUUID()
    });
    return response.data;
  },
  rerunPatrolBatch: async (batchId: string): Promise<{
    success: boolean;
    patrol_batch_id: string;
    new_patrol_batch_id: string;
    source_run_count: number;
    deduplicated_device_count: number;
    rerun_count: number;
    runs: Array<{
      run_id: string;
      rerun_of_run_id: string;
      device_id?: string;
      device_name?: string;
    }>;
  }> => {
    const response = await api.post(`/apps/inspection/batches/${batchId}/rerun/`, {
      request_id: crypto.randomUUID()
    });
    return response.data;
  },
  exportPatrolBatch: async (batchId: string): Promise<Blob> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/export.csv`, {
      responseType: 'blob'
    });
    return response.data;
  },
  exportPatrolBatchReport: async (batchId: string, reportManifestId?: string): Promise<PatrolReportDownload> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/report.docx`, {
      responseType: 'blob',
      params: reportManifestId ? {
        report_manifest_id: reportManifestId
      } : undefined
    });
    return {
      blob: response.data,
      filename: responseFilename(response.headers['content-disposition'], `${batchId}-patrol-report.docx`),
      status: response.headers['x-netops-report-status']
    };
  },
  exportPatrolBatchTechnicalWorkbook: async (batchId: string, reportManifestId?: string): Promise<PatrolReportDownload> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/report.xlsx`, {
      responseType: 'blob',
      params: reportManifestId ? {
        report_manifest_id: reportManifestId
      } : undefined
    });
    return {
      blob: response.data,
      filename: responseFilename(response.headers['content-disposition'], `${batchId}-technical-report.xlsx`),
      status: response.headers['x-netops-report-status']
    };
  },
  exportPatrolBatchEvidencePackage: async (batchId: string, reportManifestId?: string): Promise<PatrolReportDownload> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/evidence.zip`, {
      responseType: 'blob',
      params: reportManifestId ? {
        report_manifest_id: reportManifestId
      } : undefined
    });
    return {
      blob: response.data,
      filename: responseFilename(response.headers['content-disposition'], `${batchId}-evidence.zip`),
      status: response.headers['x-netops-report-status']
    };
  },
  exportPatrolBatchRawData: async (batchId: string): Promise<Blob> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/raw-data.jsonl`, {
      responseType: 'blob'
    });
    return response.data;
  },
  exportPatrolBatchRawCli: async (batchId: string, reportManifestId?: string): Promise<Blob> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/raw-cli.zip`, {
      responseType: 'blob',
      params: reportManifestId ? {
        report_manifest_id: reportManifestId
      } : undefined
    });
    return response.data;
  },
  exportPatrolBatchEvents: async (batchId: string): Promise<Blob> => {
    const response = await api.get(`/apps/inspection/batches/${batchId}/events.csv`, {
      responseType: 'blob'
    });
    return response.data;
  }
};
