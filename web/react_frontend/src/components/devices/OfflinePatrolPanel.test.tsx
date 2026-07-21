import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, vi } from 'vitest'
import OfflinePatrolPanel, {
  OfflineFileJobWorkbench,
  buildOfflineAssetControlUrl,
  buildOfflineRuleBundleUrl,
  formatOfflineBlockerMessage,
  offlineJobDisplayName,
} from './OfflinePatrolPanel'

const { list, offlineList, breakpoint } = vi.hoisted(() => ({
  list: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'offline-test-1',
        purpose: 'rule_asset_source',
        job_type: 'parser_validation',
        name: 'Q1 Parser 验证',
        owner_id: 'operator',
        status: 'ready',
        publish_events: false,
        attempt_number: 0,
        progress: { phase: 'ready', percent: 100 },
        preview_totals: { file_count: 2, device_count: 2, command_count: 54 },
        blocker_count: 0,
        archived: false,
        created_at: '2026-07-13T10:00:00Z',
        updated_at: '2026-07-13T10:01:00Z',
      },
    ],
    total: 10001,
    page: 1,
    page_size: 25,
  }),
  offlineList: vi.fn().mockResolvedValue({
    items: [{
      id: 'offline-patrol-1',
      purpose: 'offline_patrol',
      job_type: 'single_patrol',
      name: '园区离线巡检',
      owner_id: 'operator',
      status: 'ready',
      publish_events: false,
      attempt_number: 0,
      progress: { phase: 'ready', percent: 100 },
      preview_totals: { file_count: 6, device_count: 6, command_count: 314 },
      blocker_count: 0,
      archived: false,
      created_at: '2026-07-18T10:00:00Z',
      updated_at: '2026-07-18T10:01:00Z',
    }],
    total: 1,
    page: 1,
    page_size: 25,
  }),
  breakpoint: { mobile: false },
}))

vi.mock('../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../services/api')>()
  return {
    ...original,
    ruleAssetSourceApi: {
      list,
      get: vi.fn(),
      replay: vi.fn(),
      create: vi.fn(),
      attachSource: vi.fn(),
      removeSource: vi.fn(),
      preview: vi.fn(),
      confirmBindings: vi.fn(),
      execute: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
      archive: vi.fn(),
      restore: vi.fn(),
      artifact: vi.fn(),
    },
    offlinePatrolApi: {
      list: offlineList,
      get: vi.fn(),
      replay: vi.fn(),
      create: vi.fn(),
      attachSource: vi.fn(),
      removeSource: vi.fn(),
      preview: vi.fn(),
      confirmBindings: vi.fn(),
      execute: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
      archive: vi.fn(),
      restore: vi.fn(),
      artifact: vi.fn(),
    },
    storageApi: {
      ...original.storageApi,
      uploadInit: vi.fn(),
      uploadComplete: vi.fn(),
    },
  }
})

vi.mock('../../hooks/useBreakpoint', () => ({ useIsMobile: () => breakpoint.mobile }))

afterEach(() => {
  breakpoint.mobile = false
})

const renderPanel = (purpose: 'offline_patrol' | 'rule_asset_source' = 'rule_asset_source') => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        {purpose === 'rule_asset_source'
          ? <OfflineFileJobWorkbench purpose="rule_asset_source" />
          : <OfflinePatrolPanel />}
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('OfflinePatrolPanel', () => {
  it('distinguishes the same blocker across comparison periods', () => {
    const blocker = { code: 'template_missing', message: '缺少活动模板' }

    expect(formatOfflineBlockerMessage({ ...blocker, period_role: 'baseline' }))
      .toBe('基线周期：缺少活动模板')
    expect(formatOfflineBlockerMessage({ ...blocker, period_role: 'current' }))
      .toBe('当前周期：缺少活动模板')
  })

  it('carries offline job and period datasets into the asset control deep link', () => {
    const url = buildOfflineAssetControlUrl({
      id: 'offline-history-1',
      periods: [
        { id: 'p1', role: 'baseline', dataset_id: 'dataset-q1', observed_at: '2026-01-01T00:00:00Z' },
        { id: 'p2', role: 'current', dataset_id: 'dataset-q2', observed_at: '2026-04-01T00:00:00Z' },
      ],
    } as never)

    expect(url).toContain('workspace=production')
    expect(url).toContain('subtab=source-jobs')
    expect(url).toContain('source_job_id=offline-history-1')
    expect(url).toContain('dataset_id=dataset-q1')
    expect(url).toContain('dataset_ids=dataset-q1%2Cdataset-q2')
  })

  it('opens the bundle workspace with the immutable offline job scope', () => {
    const url = buildOfflineRuleBundleUrl({
      id: 'offline-history-1',
      periods: [
        { id: 'p1', role: 'baseline', dataset_id: 'dataset-q1', observed_at: '2026-01-01T00:00:00Z' },
        { id: 'p2', role: 'current', dataset_id: 'dataset-q2', observed_at: '2026-04-01T00:00:00Z' },
      ],
    } as never)

    expect(url).toContain('workspace=release')
    expect(url).toContain('subtab=asset-bundles')
    expect(url).toContain('offline_job_id=offline-history-1')
    expect(url).toContain('dataset_ids=dataset-q1%2Cdataset-q2')
  })

  it('hides corrupted historical labels without mutating the stored name', () => {
    const job = {
      name: '?????? Q2?Q3?????????',
      job_type: 'history_compare',
    } as never

    expect(offlineJobDisplayName(job)).toBe('Q2 → Q3 规则回放样本')
    expect(job.name).toBe('?????? Q2?Q3?????????')
  })

  it('uses server pagination and exposes both rule asset sample modes', async () => {
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('Q1 Parser 验证')).toBeInTheDocument()
    expect(screen.getByText('2 台 · 2 文件 · 54 命令')).toBeInTheDocument()
    expect(screen.getByText(/共 10001 个任务/)).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 25 }))

    await user.click(screen.getByRole('button', { name: /导入单周期样本/ }))
    expect(await screen.findByText('新建规则资产样本任务')).toBeInTheDocument()
    expect(screen.getAllByText('单周期资产样本').length).toBeGreaterThan(1)
    const createButton = screen.getByRole('button', { name: '创建并继续' })
    expect(createButton).toBeDisabled()
    const observedAt = document.querySelector<HTMLInputElement>('input[type="datetime-local"]')
    expect(observedAt).not.toBeNull()
    fireEvent.change(observedAt!, { target: { value: '2026-03-23T09:00' } })
    expect(createButton).toBeEnabled()

    await user.click(screen.getByLabelText('Close'))
    await user.click(screen.getByRole('button', { name: /导入双周期样本/ }))
    expect(await screen.findByText('双周期规则回放样本')).toBeInTheDocument()
    expect(screen.getByText('数据用途')).toBeInTheDocument()
    expect(screen.queryByText('事件发布')).not.toBeInTheDocument()
  })

  it('keeps offline patrol as a separate active-rule execution workspace', async () => {
    renderPanel('offline_patrol')

    expect(await screen.findByText('离线巡检任务')).toBeInTheDocument()
    expect(screen.getByText('园区离线巡检')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /发起单周期离线巡检/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /对比两个巡检周期/ })).toBeInTheDocument()
    expect(screen.queryByText('规则资产样本任务')).not.toBeInTheDocument()
    expect(offlineList).toHaveBeenCalled()
  })

  it('renders mobile tasks as actionable records instead of a horizontally clipped table', async () => {
    breakpoint.mobile = true
    renderPanel()

    expect(await screen.findByText('Q1 Parser 验证')).toBeInTheDocument()
    expect(screen.getByText('共 10001 个任务')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '任务 / 创建时间' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /详情/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '更多操作' })).toBeInTheDocument()
  })
})
