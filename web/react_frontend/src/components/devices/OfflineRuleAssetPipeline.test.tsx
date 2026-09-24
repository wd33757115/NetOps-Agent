import React from 'react'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ruleAssetSourceApi } from '../../services/api'
import OfflineRuleAssetPipeline from './OfflineRuleAssetPipeline'

describe('OfflineRuleAssetPipeline', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows import progress and sends command classification to the catalog', async () => {
    vi.spyOn(ruleAssetSourceApi, 'assetPipeline').mockResolvedValue({
      offline_job_id: 'offline-1',
      status: 'completed',
      progress_percent: 100,
      next_action: {
        code: 'open_check_item_catalog',
        label: '查看待归类命令',
        description: '在检查项目录中关联 CheckItem。',
        target_url: '/skills/parser?workspace=production&subtab=catalog-management&action=classify_check_items',
      },
      secondary_actions: [],
      steps: [
        {
          key: 'catalog',
          label: '命令进入检查项目录',
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

    expect(await screen.findByText('样本导入进度')).toBeVisible()
    expect(screen.getByText('查看待归类命令')).toBeVisible()
    expect(screen.getByText('下一步：关联检查项')).toBeVisible()
    expect(screen.queryByRole('button', { name: '继续制作与验证' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关联检查项' })).toBeVisible()
    expect(screen.queryByText('Contract')).not.toBeInTheDocument()
  })
})
