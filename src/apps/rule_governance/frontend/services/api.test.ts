import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@netops/config/api'
import { parserApi, ruleAssetSourceApi } from './api'
import {
  parserApi as legacyParserApi,
  ruleAssetSourceApi as legacySourceApi,
} from '@netops/services/api'

afterEach(() => vi.restoreAllMocks())

describe('rule governance API migration', () => {
  it('keeps publication reason and idempotency on the existing guarded endpoint', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { version_id: 'v1' } })
    await parserApi.activateCheckItemRuntimeBundle('v1', 'regression passed', 'publish-1')
    expect(post).toHaveBeenCalledWith(
      '/parser/check-item-runtime-bundles/v1/activate/',
      { reason: 'regression passed' },
      { headers: { 'Idempotency-Key': 'publish-1' } },
    )
    expect(legacyParserApi).toBe(parserApi)
  })

  it('keeps source tasks distinct from inspection tasks and normalizes old previews', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      data: { id: 'source-1', preview: { output_review_items: [{ id: 'review-1' }] } },
    })
    const result = await ruleAssetSourceApi.get('source-1')
    expect(get).toHaveBeenCalledWith('/parser/asset-source-jobs/source-1/')
    expect(result.output_review_items).toEqual([{ id: 'review-1' }])
    expect(result.output_dispositions).toEqual([])
    expect(legacySourceApi).toBe(ruleAssetSourceApi)
  })
})
