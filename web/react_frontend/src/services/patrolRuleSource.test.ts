import { beforeEach, describe, expect, it, vi } from 'vitest'
import { offlinePatrolApi, ruleAssetSourceApi, type OfflinePatrolJob } from './api'
import { createPatrolReanalysis, reusePatrolRuleSource } from './patrolRuleSource'

const period = { role: 'current', label: '采集期', observed_at: '2026-09-03T00:00:00Z' }
const file = { file_id: 'file-1', period_role: 'current', relative_path: 'switch.log', size_bytes: 20 }
const original = { id: 'patrol-1', purpose: 'offline_patrol', status: 'completed', name: '日检', job_type: 'single_patrol', periods: [period], source_files: [file] } as OfflinePatrolJob

describe('reusePatrolRuleSource', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(offlinePatrolApi, 'get').mockResolvedValue(original)
    vi.spyOn(ruleAssetSourceApi, 'create').mockResolvedValue({ id: 'source-1' } as OfflinePatrolJob)
    vi.spyOn(ruleAssetSourceApi, 'get').mockResolvedValue({ id: 'source-1', status: 'draft', source_files: [] } as unknown as OfflinePatrolJob)
    vi.spyOn(ruleAssetSourceApi, 'attachSource').mockResolvedValue({ ...file, id: 'attachment-1', status: 'ready' })
  })
  it('preserves collection time and files without granting production authority', async () => {
    expect(await reusePatrolRuleSource('patrol-1')).toBe('source-1')
    expect(ruleAssetSourceApi.create).toHaveBeenCalledWith(expect.objectContaining({
      job_type: 'parser_validation', report_intent: 'historical', publish_events: false,
      idempotency_key: 'patrol-rule-source:patrol-1', periods: [{ ...period, role: 'sample' }],
    }))
    expect(ruleAssetSourceApi.attachSource).toHaveBeenCalledWith('source-1', expect.objectContaining({ file_id: 'file-1', period_role: 'sample' }))
  })
  it('resumes the same task without reattaching completed files', async () => {
    vi.mocked(ruleAssetSourceApi.get).mockResolvedValue({ id: 'source-1', status: 'completed', source_files: [{ ...file, period_role: 'sample' }] } as OfflinePatrolJob)
    await reusePatrolRuleSource('patrol-1')
    await reusePatrolRuleSource('patrol-1')
    expect(ruleAssetSourceApi.attachSource).not.toHaveBeenCalled()
    expect(vi.mocked(ruleAssetSourceApi.create).mock.calls.map(([payload]) => payload.idempotency_key)).toEqual(['patrol-rule-source:patrol-1', 'patrol-rule-source:patrol-1'])
  })
  it('does not create a task when original evidence is absent', async () => {
    vi.mocked(offlinePatrolApi.get).mockResolvedValue({ ...original, source_files: [] })
    await expect(reusePatrolRuleSource('patrol-1')).rejects.toThrow('缺少完整文件或采集周期')
    expect(ruleAssetSourceApi.create).not.toHaveBeenCalled()
  })
})

describe('createPatrolReanalysis', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(offlinePatrolApi, 'get').mockImplementation(async id => id === original.id ? original
      : { id: 'new-analysis', status: 'uploading', source_files: [] } as unknown as OfflinePatrolJob)
    vi.spyOn(offlinePatrolApi, 'create').mockResolvedValue({ id: 'new-analysis' } as OfflinePatrolJob)
    vi.spyOn(offlinePatrolApi, 'attachSource').mockResolvedValue({ ...file, id: 'copy-1', status: 'ready' })
    vi.spyOn(offlinePatrolApi, 'retry').mockResolvedValue({})
    vi.spyOn(offlinePatrolApi, 'execute').mockResolvedValue({})
  })
  it('copies collection evidence into a historical task and leaves the original untouched', async () => {
    expect(await createPatrolReanalysis('patrol-1', 'attempt-1')).toBe('new-analysis')
    expect(offlinePatrolApi.create).toHaveBeenCalledWith(expect.objectContaining({
      job_type: 'single_patrol', report_intent: 'historical', publish_events: false,
      periods: [period], idempotency_key: 'patrol-reanalysis:patrol-1:attempt-1',
    }))
    expect(offlinePatrolApi.attachSource).toHaveBeenCalledWith('new-analysis', expect.objectContaining({ file_id: 'file-1', period_role: 'current' }))
    expect(offlinePatrolApi.retry).not.toHaveBeenCalled()
    expect(offlinePatrolApi.execute).not.toHaveBeenCalled()
  })
  it('resumes a partially attached task with the same request key', async () => {
    vi.mocked(offlinePatrolApi.attachSource).mockRejectedValueOnce(new Error('network interrupted'))
    await expect(createPatrolReanalysis('patrol-1', 'attempt-1')).rejects.toThrow('network interrupted')
    await createPatrolReanalysis('patrol-1', 'attempt-1')
    expect(vi.mocked(offlinePatrolApi.create).mock.calls.map(([payload]) => payload.idempotency_key))
      .toEqual(['patrol-reanalysis:patrol-1:attempt-1', 'patrol-reanalysis:patrol-1:attempt-1'])
  })
})
