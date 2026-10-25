import { api } from '@netops/config/api'
import { inspectionDeviceApi } from '../../inspection/frontend/api'
import type { DeviceBulkApplyResult, DeviceBulkDeletePreview, DeviceBulkDeleteResult, DeviceBulkPreview, DeviceBulkRequest, DeviceConnectionHealthResponse, DeviceConnectivityTestResult, DeviceCredentialInspection, DeviceCredentialReference, DeviceDynamicGroup, DeviceDynamicGroupPreview, DeviceEffectivePlanPreview, DeviceEvidenceTimelineResponse, DeviceExecutionPlanResponse, DeviceIdentityDiscoveryResult, DeviceImportApplyResult, DeviceImportPayload, DeviceImportPreview, DeviceImportTemplate, DeviceListResponse, DeviceMutationResponse, DeviceUpsertPayload, ManagedDevice } from '@netops/services/api'

export const deviceApi = {
  list: async (params: Record<string, string | number | undefined> = {}) => {
    const response = await api.get<DeviceListResponse>('/apps/device-management/devices/', { params })
    return response.data
  },
  upsert: async (data: DeviceUpsertPayload) => {
    const response = await api.post<DeviceMutationResponse>('/apps/device-management/devices/', data)
    return response.data
  },
  detail: async (deviceId: string) => {
    const response = await api.get<ManagedDevice>(`/apps/device-management/devices/${deviceId}/`)
    return response.data
  },
  update: async (deviceId: string, data: Partial<DeviceUpsertPayload>) => {
    const response = await api.patch<DeviceMutationResponse>(`/apps/device-management/devices/${deviceId}/`, data)
    return response.data
  },
  delete: async (deviceId: string) => {
    const response = await api.delete<DeviceMutationResponse>(`/apps/device-management/devices/${deviceId}/`)
    return response.data
  },
  setStatus: async (deviceId: string, status: string) => {
    const response = await api.post<DeviceMutationResponse>(`/apps/device-management/devices/${deviceId}/status/`, { status })
    return response.data
  },
  setGroups: async (deviceId: string, groups: string[]) => {
    const response = await api.post<DeviceMutationResponse>(`/apps/device-management/devices/${deviceId}/groups/`, { groups })
    return response.data
  },
  previewImport: async (input: string | DeviceImportPayload) => {
    const payload = typeof input === 'string'
      ? { content: input, format: 'csv', dry_run: true }
      : { ...input, dry_run: true }
    const response = await api.post<DeviceImportPreview>('/apps/device-management/devices/import/preview/', {
      ...payload,
    })
    return response.data
  },
  applyImport: async (input: string | DeviceImportPayload) => {
    const payload = typeof input === 'string'
      ? { content: input, format: 'csv', dry_run: false }
      : { ...input, dry_run: false }
    const response = await api.post<DeviceImportApplyResult>('/apps/device-management/devices/import/apply/', {
      ...payload,
    })
    return response.data
  },
  importTemplate: async () => {
    const response = await api.get<DeviceImportTemplate>('/apps/device-management/devices/import/template/')
    return response.data
  },
  groups: async () => {
    const response = await api.get<{ items: string[] }>('/apps/device-management/devices/groups/')
    return response.data.items
  },
  previewBulk: async (data: DeviceBulkRequest) => {
    const response = await api.post<DeviceBulkPreview>('/apps/device-management/devices/bulk/preview/', data)
    return response.data
  },
  applyBulk: async (data: DeviceBulkRequest) => {
    const response = await api.post<DeviceBulkApplyResult>('/apps/device-management/devices/bulk/apply/', data)
    return response.data
  },
  previewBulkDelete: async (data: Omit<DeviceBulkRequest, 'update'>) => {
    const response = await api.post<DeviceBulkDeletePreview>('/apps/device-management/devices/bulk/delete/preview/', data)
    return response.data
  },
  bulkDelete: async (data: Omit<DeviceBulkRequest, 'update'>) => {
    const response = await api.post<DeviceBulkDeleteResult>('/apps/device-management/devices/bulk/delete/', data)
    return response.data
  },
  previewPatrol: inspectionDeviceApi.previewPatrol,
  patrolReadiness: inspectionDeviceApi.patrolReadiness,
  launchPatrol: inspectionDeviceApi.launchPatrol,
  getPatrolBatchLiveEventsUrl: inspectionDeviceApi.getPatrolBatchLiveEventsUrl,
  effectivePlan: async (
    deviceId: string,
    data: { action?: string; mode?: string } = {},
  ) => {
    const response = await api.post<DeviceEffectivePlanPreview>(
      `/apps/device-management/devices/${deviceId}/effective-plan/`,
      data,
    )
    return response.data
  },
  testConnectivity: async (deviceId: string) => {
    const response = await api.post<DeviceConnectivityTestResult>(
      `/apps/device-management/devices/${deviceId}/connectivity-test/`,
      {},
    )
    return response.data
  },
  discoverIdentity: async (deviceId: string) => {
    const response = await api.post<DeviceIdentityDiscoveryResult>(
      `/apps/device-management/devices/${deviceId}/identity-discovery/`,
      {},
    )
    return response.data
  },
  connectionHealth: async (deviceId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceConnectionHealthResponse>(
      `/apps/device-management/devices/${deviceId}/connection-health/`,
      { params },
    )
    return response.data
  },
  executionPlans: async (deviceId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceExecutionPlanResponse>(
      `/apps/device-management/devices/${deviceId}/execution-plans/`,
      { params },
    )
    return response.data
  },
  evidenceTimeline: async (
    deviceId: string,
    params: { limit?: number; workflow_run_id?: string; patrol_batch_id?: string } = {},
  ) => {
    const response = await api.get<DeviceEvidenceTimelineResponse>(
      `/apps/device-management/devices/${deviceId}/evidence-timeline/`,
      { params },
    )
    return response.data
  },
  dynamicGroups: async (params: { status?: string } = {}) => {
    const response = await api.get<{ items: DeviceDynamicGroup[] }>('/apps/device-management/devices/dynamic-groups/', { params })
    return response.data.items
  },
  saveDynamicGroup: async (data: {
    name: string
    description?: string
    selector: Record<string, unknown>
    status?: string
  }, groupId?: string) => {
    const response = groupId
      ? await api.patch<{ success: boolean; group: DeviceDynamicGroup }>(`/apps/device-management/devices/dynamic-groups/${groupId}/`, data)
      : await api.post<{ success: boolean; group: DeviceDynamicGroup }>('/apps/device-management/devices/dynamic-groups/', data)
    return response.data
  },
  setDynamicGroupStatus: async (groupId: string, status: string) => {
    const response = await api.post<{ success: boolean; group: DeviceDynamicGroup }>(
      `/apps/device-management/devices/dynamic-groups/${groupId}/status/`,
      { status },
    )
    return response.data
  },
  previewDynamicGroup: async (groupId: string, params: { limit?: number } = {}) => {
    const response = await api.get<DeviceDynamicGroupPreview>(
      `/apps/device-management/devices/dynamic-groups/${groupId}/preview/`,
      { params },
    )
    return response.data
  },
  credentials: async () => {
    const response = await api.get<{ items: DeviceCredentialReference[] }>('/apps/device-management/devices/credentials/')
    return response.data.items
  },
  inspectCredential: async (credential_ref?: string) => {
    const response = await api.post<DeviceCredentialInspection>('/apps/device-management/devices/credentials/inspect/', { credential_ref })
    return response.data
  },
}
