import { api } from '@netops/config/api'

export interface CommandAutopilot {
  status: 'idle' | 'queued' | 'running' | 'paused' | 'needs_attention' | 'needs_authorization' | 'completed'
  stage: string
  stage_label: string
  can_edit: boolean
  same_name_values?: boolean
  error?: string | null
  updated_at?: string
  publish_grant?: { by: string; expires_at: string } | null
  outputs: {
    parser?: { template_id?: string; sample_count?: number }
    projection?: { proposal?: Record<string, unknown>; sample_reports?: unknown[] }
    implementation?: { id: string; asset_id?: string }
    rules?: { bundle_id?: string; change_policies?: unknown[]; event_rules?: unknown[] }
    runtime?: { version_id: string; status: string }
  }
  history: Array<{ action: string; stage: string; actor: string; at: string; error?: string }>
}

const path = (jobId: string, unitId: string) => `/parser/asset-source-jobs/${encodeURIComponent(jobId)}/command-asset-units/${encodeURIComponent(unitId)}/autopilot/`
export const commandAutopilotApi = {
  get: async (jobId: string, unitId: string): Promise<CommandAutopilot> => (await api.get(path(jobId, unitId))).data,
  control: async (jobId: string, unitId: string, action: 'start' | 'pause' | 'resume' | 'authorize'): Promise<CommandAutopilot> => (
    await api.post(path(jobId, unitId), { action })
  ).data,
  editProjection: async (jobId: string, unitId: string, proposal: Record<string, unknown>): Promise<CommandAutopilot> => (
    await api.put(path(jobId, unitId) + 'projection/', { proposal })
  ).data,
}
