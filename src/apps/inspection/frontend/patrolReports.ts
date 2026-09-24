import { api } from '@netops/config/api'

const root = '/apps/inspection/patrol'
const job = (id: string) => `${root}/offline-jobs/${encodeURIComponent(id)}/report`
export type ReportSection = 'narrative.summary' | 'changes.summary'
export interface ReportTemplate { id: string; name: string; digest: string; builtin?: boolean }
export interface ReportPreview { text: Record<ReportSection, string>; conclusion: string; limitations: string; evidence_count: number }
export interface DraftPreview { id: string; digest: string; request: unknown; provider: { provider: string; model: string; endpoint: string }; bytes: number; notice: string }
export interface DraftResult { id: string; state: string; error?: string; output?: { sections: { id: ReportSection; paragraphs: { text: string; fact_ids: string[] }[] }[] } }
export interface EvidenceLine { line: number; text: string; anomaly_span?: number[]; anomaly_spans?: number[][] }
export interface ReportEvidence {
  lookup_reference?: string
  id: string; device: string; command: string; entity: string; note: string; references: string[]
  baseline?: { snapshot_id?: string | null; observed_at?: string | null }
  current?: { snapshot_id?: string | null; observed_at?: string | null }
  rows: { baseline?: EvidenceLine | null; current?: EvidenceLine | null; change: string }[]
}
export const patrolReports = {
  templates: async () => (await api.get<ReportTemplate[]>(`${root}/report-templates`)).data,
  templateFile: async (id: string) => (await api.get(`${root}/report-templates/${encodeURIComponent(id)}/file`, { responseType: 'blob' })).data as Blob,
  importTemplate: async (file: File) => { const body = new FormData(); body.append('file', file); return (await api.post<ReportTemplate>(`${root}/report-templates`, body, { headers: { 'Content-Type': 'multipart/form-data' } })).data },
  preview: async (id: string) => (await api.get<ReportPreview>(job(id))).data,
  details: async (id: string) => (await api.get(`${job(id)}/details`, { responseType: 'blob', timeout: 120000 })).data as Blob,
  evidence: async (id: string, offset: number, reference?: string, signal?: AbortSignal) => (await api.get<{ total: number; source_digest: string; items: ReportEvidence[] }>(`${job(id)}/evidence`, { params: { offset, reference }, signal, timeout: 20000 })).data,
  fullEvidence: async (id: string, entry: string, source_digest: string, reference?: string, signal?: AbortSignal) => (await api.get<{ baseline: string | null; current: string | null }>(`${job(id)}/evidence/${encodeURIComponent(entry)}/full`, { params: { source_digest, reference }, signal, timeout: 20000 })).data,
  export: async (id: string, options: { template_id: string; metadata: Record<string, string>; draft_id?: string; bundle: boolean }) => (await api.post(`${job(id)}/export`, options, { responseType: 'blob', timeout: 120000 })).data as Blob,
  prepare: async (id: string, template_id: string, sections: ReportSection[]) => (await api.post<DraftPreview>(`${job(id)}/drafts/preview`, { template_id, sections })).data,
  generate: async (id: string, preview: DraftPreview, template_id: string) => (await api.post<DraftResult>(`${job(id)}/drafts/${preview.id}/generate`, { digest: preview.digest, template_id }, { timeout: 45000 })).data,
  accept: async (id: string, draft: string, template_id: string) => (await api.post(`${job(id)}/drafts/${draft}/accept`, { template_id })).data,
}
