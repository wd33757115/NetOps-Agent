import React from 'react'
import { App } from 'antd'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parserApi, type OfflineCommandAssetUnitDetail } from '@netops/services/api'
import RuleAssetProductionStep from './RuleAssetProductionStep'

vi.mock('@netops/services/api', () => ({ parserApi: {
  latestGeneration: vi.fn(), resolveGeneration: vi.fn(),
  previewRuleGenerationContext: vi.fn(), ruleAssetBundles: vi.fn(),
  startRuleAssetGeneration: vi.fn(),
} }))

const unit = { id: 'unit', offline_job_id: 'source', rule_seed_job_id: 'seed',
  context_id: 'context', canonical_command: 'show cpu', evidence: [], evidence_pagination: { total: 0 }, sample_count: 0,
} as unknown as OfflineCommandAssetUnitDetail

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><RuleAssetProductionStep unit={unit} /></App></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(parserApi.previewRuleGenerationContext).mockResolvedValue({ ready: true, context: {} } as never)
  vi.mocked(parserApi.ruleAssetBundles).mockResolvedValue({ items: [] } as never)
})

describe('Durable rule generation', () => {
  it('restores a running request after reopening and blocks duplicate generation', async () => {
    vi.mocked(parserApi.latestGeneration).mockResolvedValue({ run_id: 'run', status: 'running', assets: [] } as never)
    show()
    expect(await screen.findByText('生成任务：执行中')).toBeVisible()
    expect(await screen.findByRole('button', { name: /生成任务已提交/ })).toBeDisabled()
    expect(parserApi.startRuleAssetGeneration).not.toHaveBeenCalled()
  })

  it('shows retained partial assets and requires an inspection note before closing', async () => {
    vi.mocked(parserApi.latestGeneration).mockResolvedValue({ run_id: 'run', status: 'needs_attention',
      error: '持久化中断', assets: [{ id: 'candidate-1', status: 'draft' }] } as never)
    vi.mocked(parserApi.resolveGeneration).mockResolvedValue({ status: 'resolved' } as never)
    show()
    expect(await screen.findByText('候选 candidate-1：draft')).toBeVisible()
    const close = screen.getByRole('button', { name: '记录核查并结案' })
    expect(close).toBeDisabled()
    expect(await screen.findByRole('button', { name: /确认语义方案并生成规则资产/ })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('生成任务核查说明'), { target: { value: '已检查并保留候选，结束本次任务' } })
    fireEvent.click(close)
    await waitFor(() => expect(parserApi.resolveGeneration).toHaveBeenCalledWith('run', '已检查并保留候选，结束本次任务'))
    expect(parserApi.startRuleAssetGeneration).not.toHaveBeenCalled()
  })
})
