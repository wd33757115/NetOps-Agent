import { api } from '@netops/config/api'

const root = '/apps/rule-governance/rule-packages'
export const packageKinds: Record<string, string> = {
  check_version: '检查项', parser: 'TextFSM', policy_version: 'Change 策略', event_version: '状态 / 事件规则',
  implementation_version: '采集实现', comparison_version: '文本比较', identity_version: '实体标识', assessment_version: '评估策略',
}
export interface PackageAsset { ref: string; kind: string; name: string; version: string | number; status: string; scope: string }
export interface PlanItem {
  name?: string;
  ref: string; kind: string; action: 'create' | 'reuse' | 'new_version' | 'derived'; local_id: string | null; version?: string | number;
  same_version_conflict: boolean; metadata_preserved?: boolean; before: Record<string, unknown> | null; after: Record<string, unknown>
}
export interface PackagePreview {
  revision: string; sha256: string; items: PlanItem[]; counts: Record<string, number>; sample_count: number;
  recipe_count: number; requires_confirmation: boolean; reports: { ref: string; check: string; status: string }[];
  samples: { text: string; expected: unknown; parser: unknown }[]
}
export interface ImportResult {
  record_id: string; sha256: string; counts: Record<string, number>; items: PlanItem[]; replayed?: boolean; notice: string;
  changes: { change_id: string; asset_id: string; kind: string }[];
  recipes: unknown[]; reports: { ref: string; check: string; status: string }[]
}
export const packageApi = {
  assets: async (params: Record<string, unknown>) => (await api.get<{ items: PackageAsset[]; total: number }>(`${root}/assets`, { params })).data,
  export: async (data: { selections?: string[]; all_active?: boolean; include_samples?: boolean; include_drafts?: boolean }) =>
    (await api.post<{ content_base64: string; filename: string; asset_count: number; sample_count: number }>(`${root}/export`, data, { timeout: 120000 })).data,
  preview: async (content_base64: string) => (await api.post<PackagePreview>(`${root}/preview`, { content_base64 }, { timeout: 120000 })).data,
  import: async (content_base64: string, revision: string, confirm_conflicts: boolean) =>
    (await api.post<ImportResult>(`${root}/import`, { content_base64, revision, confirm_conflicts }, { timeout: 120000 })).data,
  history: async (page: number) => (await api.get<{ items: { id: string; action: string; actor: string; created_at: string; sha256: string; counts?: Record<string, number>; asset_count?: number }[]; total: number }>(`${root}/history`, { params: { offset: (page - 1) * 20, limit: 20 } })).data,
  detail: async (id: string) => (await api.get<ImportResult>(`${root}/history/${id}`)).data,
  validate: async (id: string) => (await api.post<{ checks: { name: string; status: string; blockers: string[]; warnings?: string[] }[] }>(`${root}/history/${id}/validate`, {}, { timeout: 120000 })).data,
}
export function packageError(error: unknown) {
  const err = error as { response?: { data?: { detail?: unknown; error?: unknown } }; message?: string }
  const detail = err.response?.data?.detail || err.response?.data?.error
  return typeof detail === 'string' ? detail : err.message || '操作失败，请重试'
}
export function downloadPackage(result: { content_base64: string; filename: string }) {
  const bytes = Uint8Array.from(atob(result.content_base64), c => c.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
  const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function fileBase64(file: File): Promise<string> {
  if (file.size > 12 * 1024 * 1024) return Promise.reject(new Error('规则包不能超过 12 MB'))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}
export const transferHref = (refs: string[] = []) => `/apps/rule-governance?workspace=transfer${refs.map(r => `&asset=${encodeURIComponent(r)}`).join('')}`
