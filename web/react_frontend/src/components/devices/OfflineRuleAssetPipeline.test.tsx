import React from 'react'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ruleAssetSourceApi } from '../../services/api'
import OfflineRuleAssetPipeline from './OfflineRuleAssetPipeline'

vi.mock('./OfflineCommandAssetUnits', () => ({
  default: ({ jobId }: { jobId: string }) => (
    <div data-testid="command-asset-units">{jobId}</div>
  ),
}))

describe('OfflineRuleAssetPipeline', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows task progress and the command units scoped to this sample job', async () => {
    vi.spyOn(ruleAssetSourceApi, 'assetPipeline').mockResolvedValue({
      offline_job_id: 'offline-1',
      status: 'ready',
      progress_percent: 40,
      next_action: {
        code: 'bind_check_item',
        label: '绑定检查项',
        description: '确认命令对应的巡检检查项。',
        target_url: '/skills/parser?workspace=production&subtab=asset-production-units',
      },
      secondary_actions: [],
      steps: [
        {
          key: 'samples',
          label: '样本准备',
          status: 'completed',
          detail: '样本已就绪。',
          counts: {},
          optional: false,
        },
      ],
      dataset_ids: ['dataset-q2'],
      template_ids: [],
      context_ids: [],
      bundle_ids: [],
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <OfflineRuleAssetPipeline jobId="offline-1" />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('样本任务生产进度')).toBeVisible()
    expect(screen.getByText('绑定检查项')).toBeVisible()
    expect(screen.getByText('按命令处理样本资产')).toBeVisible()
    expect(screen.getByTestId('command-asset-units')).toHaveTextContent('offline-1')
    expect(screen.queryByText('Contract')).not.toBeInTheDocument()
  })
})
