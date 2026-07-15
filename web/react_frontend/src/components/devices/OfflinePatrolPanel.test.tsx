import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import OfflinePatrolPanel, {
  buildOfflineAssetControlUrl,
  buildOfflineRuleBundleUrl,
  formatOfflineBlockerMessage,
  offlineJobDisplayName,
} from './OfflinePatrolPanel'

const { list } = vi.hoisted(() => ({
  list: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'offline-test-1',
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
}))

vi.mock('../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../services/api')>()
  return {
    ...original,
    offlinePatrolApi: {
      list,
      get: vi.fn(),
      analysis: vi.fn(),
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

vi.mock('../../hooks/useBreakpoint', () => ({ useIsMobile: () => false }))

const renderPanel = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <OfflinePatrolPanel />
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

    expect(url).toContain('offline_job_id=offline-history-1')
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

    expect(url).toContain('workspace=today')
    expect(url).toContain('subtab=rule-generation')
    expect(url).toContain('offline_job_id=offline-history-1')
    expect(url).toContain('dataset_ids=dataset-q1%2Cdataset-q2')
  })

  it('hides corrupted historical labels without mutating the stored name', () => {
    const job = {
      name: '?????? Q2?Q3?????????',
      job_type: 'history_compare',
    } as never

    expect(offlineJobDisplayName(job)).toBe('Q2 → Q3 历史周期对比')
    expect(job.name).toBe('?????? Q2?Q3?????????')
  })

  it('uses server pagination and exposes both offline patrol workflows', async () => {
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('Q1 Parser 验证')).toBeInTheDocument()
    expect(screen.getByText('2 台 · 2 文件 · 54 命令')).toBeInTheDocument()
    expect(screen.getByText(/共 10001 个任务/)).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 25 }))

    await user.click(screen.getByRole('button', { name: /导入单周期/ }))
    expect(await screen.findByText('新建离线巡检任务')).toBeInTheDocument()
    expect(screen.getAllByText('单周期解析验证').length).toBeGreaterThan(1)

    await user.click(screen.getByLabelText('Close'))
    await user.click(screen.getByRole('button', { name: /对比两个周期/ }))
    expect(await screen.findByText('双周期变化分析')).toBeInTheDocument()
    expect(screen.getByText('事件发布')).toBeInTheDocument()
  })
})
