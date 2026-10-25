// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { api } from '../config/api'
import type {
  OfflineOutputDisposition,
  OfflineOutputReviewItem,
  OfflinePatrolAnalysisSummary,
  OfflinePatrolComparisonEvidence,
  OfflinePatrolJob,
  OfflinePatrolJobPage,
  OfflinePatrolJobType,
  OfflinePatrolReportIntent,
  OfflinePatrolSourceFile
} from './api'

export const createOfflineJobApi = (base: string) => ({
  list: async (params: {
    page?: number
    page_size?: number
    search?: string
    status?: string
    job_type?: OfflinePatrolJobType
    archived?: boolean
  } = {}): Promise<OfflinePatrolJobPage> => (
    await api.get<OfflinePatrolJobPage>(`${base}/`, { params })
  ).data,
  get: async (id: string): Promise<OfflinePatrolJob> => {
    const data = (await api.get<OfflinePatrolJob>(`${base}/${id}/`)).data
    const preview = (data.preview || {}) as {
      output_review_items?: OfflineOutputReviewItem[]
      output_dispositions?: OfflineOutputDisposition[]
    }
    return {
      ...data,
      output_review_items: data.output_review_items || preview.output_review_items || [],
      output_dispositions: data.output_dispositions || preview.output_dispositions || [],
    }
  },
  replay: async (id: string): Promise<OfflinePatrolAnalysisSummary> => (
    await api.get<OfflinePatrolAnalysisSummary>(`${base}/${id}/replay-summary/`, {
      timeout: 300000,
    })
  ).data,
  comparisonEvidence: async (
    id: string,
    changeId: string,
  ): Promise<OfflinePatrolComparisonEvidence> => (
    await api.get<OfflinePatrolComparisonEvidence>(
      base + '/' + id + '/comparison-evidence/' + encodeURIComponent(changeId) + '/',
      { timeout: 300000 },
    )
  ).data,
  create: async (data: {
    job_type: OfflinePatrolJobType
    name: string
    periods: Array<{ role: string; label: string; observed_at: string }>
    vendor?: string
    model?: string
    publish_events?: boolean
    report_intent?: OfflinePatrolReportIntent
    idempotency_key: string
  }): Promise<OfflinePatrolJob> => (
    await api.post<OfflinePatrolJob>(`${base}/`, data)
  ).data,
  attachSource: async (id: string, data: {
    period_role: string
    file_id: string
    relative_path: string
    sha256?: string
    size_bytes: number
  }): Promise<OfflinePatrolSourceFile> => (
    await api.post<OfflinePatrolSourceFile>(`${base}/${id}/sources/`, data)
  ).data,
  removeSource: async (id: string, sourceId: string) => (
    await api.delete(`${base}/${id}/sources/${sourceId}/`)
  ).data,
  preview: async (id: string) => (
    await api.post(`${base}/${id}/preview/`, {})
  ).data,
  confirmBindings: async (id: string, bindings: Array<{ binding_id: string; inventory_device_id?: string | null }>) => (
    await api.post(`${base}/${id}/device-bindings/confirm/`, { bindings })
  ).data,
  confirmOutputDispositions: async (id: string, reviewItemIds: string[], reason: string) => (
    await api.post(`${base}/${id}/output-dispositions/confirm/`, {
      review_item_ids: reviewItemIds,
      reason,
    })
  ).data,
  execute: async (id: string, idempotencyKey: string) => (
    await api.post(`${base}/${id}/execute/`, { idempotency_key: idempotencyKey })
  ).data,
  cancel: async (id: string) => (await api.post(`${base}/${id}/cancel/`, {})).data,
  retry: async (id: string) => (await api.post(`${base}/${id}/retry/`, {})).data,
  archive: async (id: string) => (await api.post(`${base}/${id}/archive/`, {})).data,
  restore: async (id: string) => (await api.post(`${base}/${id}/restore/`, {})).data,
  artifact: async (id: string, kind: 'raw' | 'changes' | 'events' | 'report' | 'excel'): Promise<Blob> => (
    await api.get(`${base}/${id}/artifacts/${kind}/`, {
      responseType: 'blob',
      timeout: 300000,
    })
  ).data as Blob,
})

