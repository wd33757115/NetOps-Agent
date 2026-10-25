import { api } from '@netops/config/api'
import type { ParserPageParams, ParserPage, ParserCommandProfile, ParserCommandProfileImpactPreview } from '@netops/services/api'
export const commandApi = {
  commandProfiles: async (params: ParserPageParams = {}): Promise<ParserPage<ParserCommandProfile>> => {
    const response = await api.get<ParserPage<ParserCommandProfile>>('/apps/device-management/command-profiles/', { params })
    return response.data
  },
  upsertCommandProfile: async (data: {
    name: string
    action?: string
    commands?: string[]
    check_item_ids?: string[]
    status?: string
  }) => (await api.post('/apps/device-management/command-profiles/', data)).data,
  activateCommandProfile: async (profileVersionId: string, reason: string) =>
    (await api.post(`/apps/device-management/command-profiles/${profileVersionId}/activate/`, { reason })).data,
  upsertCommandProfileBinding: async (data: {
    profile_version_id: string
    selector: Record<string, unknown>
    priority?: number
    status?: string
  }) => (await api.post('/apps/device-management/command-profile-bindings/', data)).data,
  enableCommandProfileBinding: async (bindingId: string, reason: string) =>
    (await api.post(`/apps/device-management/command-profile-bindings/${bindingId}/enable/`, { reason })).data,
  disableCommandProfileBinding: async (bindingId: string, reason: string) =>
    (await api.post(`/apps/device-management/command-profile-bindings/${bindingId}/disable/`, { reason })).data,
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
      '/apps/device-management/command-profiles/impact-preview/',
      data,
    )
    return response.data
  },
}
