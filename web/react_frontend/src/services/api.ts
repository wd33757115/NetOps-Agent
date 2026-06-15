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
  primary_key_stability_rate: number
  first_imported_at?: string
  last_imported_at?: string
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
  sample_count: number
  attempts: number
  failure_code?: string
  last_error?: string
  celery_task_id?: string
  dispatched_at?: string
  started_at?: string
  completed_at?: string
  created_at: string
  updated_at: string
  samples?: ParserJobSample[]
}

export interface ParserJobSample {
  id: string
  snapshot_id: string
  snapshot_source: 'postgresql'
  dataset_id?: string
  device_id: string
  output_signature: string
  created_at: string
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
  job: async (id: string) => {
    const response = await api.get<ParserJobItem>(`/parser/jobs/${id}/`)
    return response.data
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
  datasets: async () => {
    const response = await api.get<{ items: ParserDatasetCoverage[] }>('/parser/datasets/')
    return response.data.items
  },
  previewImport: async (data: { source_path: string; dataset_id?: string }) => (
    await api.post<ParserImportPreview>('/parser/import-preview/', data)
  ).data,
  executeImport: async (data: { source_path: string; dataset_id?: string }) => (
    await api.post('/parser/import-execute/', data)
  ).data,
  activate: async (id: string) => (await api.post(`/parser/templates/${id}/activate/`)).data,
  reject: async (id: string) => (await api.post(`/parser/templates/${id}/reject/`)).data,
  deprecate: async (id: string) => (await api.post(`/parser/templates/${id}/deprecate/`)).data,
  retryJob: async (id: string) => (await api.post(`/parser/jobs/${id}/retry/`)).data,
  cancelJob: async (id: string) => (await api.post(`/parser/jobs/${id}/cancel/`)).data,
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
  listRuns: async (params?: { limit?: number; template_name?: string; ticket_id?: string }): Promise<WorkflowRunSummary[]> => {
    const response = await api.get<WorkflowRunSummary[]>('/workflows/runs/', { params })
    return response.data
  },
  getRun: async (runId: string): Promise<WorkflowRunDetail> => {
    const response = await api.get<WorkflowRunDetail>(`/workflows/${runId}/`)
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
