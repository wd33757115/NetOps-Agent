import { api } from '../../config/api'

export interface GroupRecord { id: number; name: string; member_ids: number[]; app_ids: string[]; protected: boolean; revision: string }
export interface GroupData { items: GroupRecord[]; users: { id: number; username: string; is_active: boolean }[] }
export interface AppRecord { id: string; name: string; state: string; revision: string; grants: { user_id: number | null; group_id: number | null }[] }
export interface AccessUser { id: number; username: string; is_active: boolean; role: string; group_ids: number[] }
export interface AccessData { items: AppRecord[]; users: AccessUser[]; groups: { id: number; name: string; protected: boolean }[] }
export interface Principal { id: number; name: string; email?: string; is_active?: boolean; role?: string; protected?: boolean; member_count?: number; sources?: string[] }
export interface DirectoryRequest { kind: 'user' | 'group'; search?: string; page?: number; page_size?: number; ids?: number[]; exclude_ids?: number[]; access?: { user_ids: number[]; group_ids: number[] } }
export const systemApi = {
  directory: async (body: DirectoryRequest): Promise<{ items: Principal[]; total: number }> => (await api.post('/platform/principals/', body)).data,
  groups: async (): Promise<GroupData> => (await api.get('/platform/groups/?compact=1')).data,
  saveGroup: async (data: { name: string; member_ids: number[]; revision?: string }, id?: number) =>
    (id ? await api.patch(`/platform/groups/${id}/`, data) : await api.post('/platform/groups/', data)).data,
  deleteGroup: async (id: number, revision: string) => (await api.delete(`/platform/groups/${id}/`, { data: { revision } })).data,
  access: async (): Promise<AccessData> => (await api.get('/platform/apps/admin/?compact=1')).data,
  saveAccess: async (appId: string, user_ids: number[], group_ids: number[], revision: string) =>
    (await api.put(`/platform/apps/admin/${appId}/`, { user_ids, group_ids, revision })).data,
}
export const systemError = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'
export function accessSources(user: AccessUser, users: number[], groups: number[], availableGroups: AccessData['groups']): string[] {
  if (user.role === 'admin') return ['平台管理员默认权限']
  return [ ...(users.includes(user.id) ? ['直接授权'] : []),
    ...availableGroups.filter(g => groups.includes(g.id) && user.group_ids.includes(g.id)).map(g => `用户组：${g.name}`) ]
}
