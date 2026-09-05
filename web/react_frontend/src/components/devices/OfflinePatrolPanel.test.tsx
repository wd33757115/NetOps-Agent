import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { afterEach, vi } from 'vitest'
import OfflinePatrolPanel, {
  OfflineFileJobWorkbench,
  buildOfflineAssetControlUrl,
  buildOfflineRuleBundleUrl,
  formatOfflineBlockerMessage,
  offlineJobDisplayName,
} from './OfflinePatrolPanel'

const {
  list,
  offlineList,
  offlineGet,
  offlineRetry,
  offlineExecute,
  offlineConfirmBindings,
  offlineConfirmOutputDispositions,
  sourceCreate,
  sourceGet,
  sourceRetry,
  sourceConfirmBindings,
  sourceConfirmOutputDispositions,
  breakpoint,
} = vi.hoisted(() => ({
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
      warning_count: 2,
      archived: false,
      created_at: '2026-07-18T10:00:00Z',
      updated_at: '2026-07-18T10:01:00Z',
    }],
    total: 1,
    page: 1,
    page_size: 25,
  }),
  sourceGet: vi.fn(),
  sourceCreate: vi.fn(),
  sourceRetry: vi.fn(),
  sourceConfirmBindings: vi.fn().mockResolvedValue({ success: true }),
  sourceConfirmOutputDispositions: vi.fn().mockResolvedValue({ success: true }),
  offlineGet: vi.fn(),
  offlineRetry: vi.fn(),
  offlineExecute: vi.fn(),
  offlineConfirmBindings: vi.fn().mockResolvedValue({ success: true }),
  offlineConfirmOutputDispositions: vi.fn().mockResolvedValue({ success: true }),
  breakpoint: { mobile: false },
}))

vi.mock('../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../services/api')>()
  return {
    ...original,
    ruleAssetSourceApi: {
      list,
      get: sourceGet,
      replay: vi.fn(),
      create: sourceCreate,
      attachSource: vi.fn(),
      removeSource: vi.fn(),
      preview: vi.fn(),
      confirmBindings: sourceConfirmBindings,
      confirmOutputDispositions: sourceConfirmOutputDispositions,
      execute: vi.fn(),
      cancel: vi.fn(),
      retry: sourceRetry,
      archive: vi.fn(),
      restore: vi.fn(),
      artifact: vi.fn(),
    },
    offlinePatrolApi: {
      list: offlineList,
      get: offlineGet,
      replay: vi.fn(),
      create: vi.fn(),
      attachSource: vi.fn(),
      removeSource: vi.fn(),
      preview: vi.fn(),
      confirmBindings: offlineConfirmBindings,
      confirmOutputDispositions: offlineConfirmOutputDispositions,
      execute: offlineExecute,
      cancel: vi.fn(),
      retry: offlineRetry,
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
  sourceGet.mockReset()
  sourceCreate.mockClear()
  sourceRetry.mockReset()
  sourceConfirmBindings.mockClear()
  sourceConfirmOutputDispositions.mockClear()
  offlineGet.mockReset()
  offlineRetry.mockReset()
  offlineExecute.mockReset()
  offlineConfirmBindings.mockClear()
  offlineConfirmOutputDispositions.mockClear()
})

const renderPanel = (
  purpose: 'offline_patrol' | 'rule_asset_source' = 'rule_asset_source',
  options: { initialEntries?: string[]; accessory?: React.ReactNode } = {},
) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={options.initialEntries || ['/']}>
      <QueryClientProvider client={queryClient}>
        {options.accessory}
        <Routes>
          {purpose === 'rule_asset_source' ? <>
            <Route path="/apps/patrol-rules/source-jobs/:jobId" element={<SourceJobDetailHarness />} />
            <Route path="/skills/parser/source-jobs/:jobId" element={<SourceJobDetailHarness />} />
            <Route path="*" element={<OfflineFileJobWorkbench purpose="rule_asset_source" />} />
          </> : (
            <Route path="*" element={<OfflinePatrolPanel />} />
          )}
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const SourceJobDetailHarness: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>()
  return <OfflineFileJobWorkbench purpose="rule_asset_source" detailPageJobId={jobId} />
}

const SameJobTabSwitch: React.FC<{ jobId: string }> = ({ jobId }) => {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      aria-label="switch-same-job-source-tab"
      onClick={() => navigate(
        `/skills/parser/source-jobs/${jobId}?tab=sources`,
      )}
    >
      switch
    </button>
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

  it('uses product language for internal runtime asset blockers', () => {
    expect(formatOfflineBlockerMessage({
      message: 'device.hardware.model 缺少 active ChangePolicy',
    })).toBe('device.hardware.model 缺少已发布的变化判断策略')
    expect(formatOfflineBlockerMessage({
      message: 'device.hardware.model 缺少匹配的 active EventRule',
    })).toBe('device.hardware.model 缺少匹配的已发布的事件判定规则')
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

    expect(url).toContain('workspace=production')
    expect(url).toContain('subtab=asset-production-units')
    expect(url).toContain('action=bundle-review')
    expect(url).toContain('offline_job_id=offline-history-1')
    expect(url).toContain('dataset_ids=dataset-q1%2Cdataset-q2')
  })

  it('hides corrupted historical labels without mutating the stored name', () => {
    const job = {
      name: '?????? Q2?Q3?????????',
      job_type: 'history_compare',
    } as never

    expect(offlineJobDisplayName(job)).toBe('Q2 → Q3 CLI 样本导入')
    expect(job.name).toBe('?????? Q2?Q3?????????')
  })

  it('uses server pagination and exposes both rule asset sample modes', async () => {
    renderPanel()

    expect(await screen.findByText('Q1 Parser 验证')).toBeInTheDocument()
    expect(screen.getByText('2 台 · 2 文件 · 54 命令')).toBeInTheDocument()
    expect(screen.getByText(/共 10001 个任务/)).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 25 }))

    fireEvent.click(screen.getByRole('button', { name: /导入单周期 CLI 样本/ }))
    expect(await screen.findByText('新建 CLI 样本导入')).toBeInTheDocument()
    expect(screen.getAllByText('单周期 CLI 样本').length).toBeGreaterThan(0)
    const createButton = screen.getByRole('button', { name: '创建并继续' })
    expect(createButton).toBeDisabled()
    const observedAt = screen.getByLabelText('样本采集时间')
    expect(observedAt).toHaveAttribute('placeholder', '选择原始数据采集时间')
    expect(screen.getByText('填写原始文件实际采集时间，不是本次导入时间')).toBeInTheDocument()
    fireEvent.change(observedAt, { target: { value: '2026-03-23 09:00' } })
    fireEvent.keyDown(observedAt, { key: 'Enter', code: 'Enter' })
    expect(createButton).toBeEnabled()

    fireEvent.click(observedAt)
    fireEvent.keyDown(observedAt, { key: 'Escape', code: 'Escape' })
    expect(screen.getByText('新建 CLI 样本导入')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Close'))
    fireEvent.click(screen.getByRole('button', { name: /导入双周期 CLI 样本/ }))
    expect((await screen.findAllByText('双周期 CLI 样本')).length).toBeGreaterThan(0)
    expect(screen.getByText('数据用途')).toBeInTheDocument()
    expect(screen.queryByText('事件发布')).not.toBeInTheDocument()
  })

  it('keeps offline patrol as a separate active-rule execution workspace', async () => {
    renderPanel('offline_patrol')

    expect(await screen.findByText('离线巡检任务')).toBeInTheDocument()
    expect(screen.getByText('园区离线巡检')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /发起单周期离线巡检/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /对比两个巡检周期/ })).toBeInTheDocument()
    expect(screen.getByText('2 个质量提醒')).toBeInTheDocument()
    expect(screen.queryByText('CLI 样本导入')).not.toBeInTheDocument()
    expect(offlineList).toHaveBeenCalled()
  })

  it('reruns a completed patrol with the currently active production rules', async () => {
    const completedJob = {
      id: 'offline-patrol-completed',
      purpose: 'offline_patrol',
      job_type: 'history_compare',
      name: '季度巡检复核',
      owner_id: 'operator',
      status: 'completed',
      publish_events: false,
      attempt_number: 1,
      progress: { phase: 'completed', percent: 100 },
      preview_totals: { file_count: 4, device_count: 2, command_count: 78 },
      blocker_count: 0,
      archived: false,
      created_at: '2026-08-09T10:00:00Z',
      updated_at: '2026-08-09T10:10:00Z',
    }
    offlineList.mockReset().mockResolvedValue({
      items: [completedJob], total: 1, page: 1, page_size: 25,
    })
    offlineRetry.mockResolvedValue({ ...completedJob, status: 'ready' })
    offlineExecute.mockResolvedValue({ ...completedJob, status: 'queued' })
    offlineGet.mockResolvedValue(completedJob)

    const user = userEvent.setup()
    renderPanel('offline_patrol')

    expect(await screen.findByText(completedJob.name)).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /按当前规则重新执行/ }))
    await waitFor(() => {
      expect(offlineRetry).toHaveBeenCalledWith('offline-patrol-completed')
      expect(offlineExecute).toHaveBeenCalledWith(
        'offline-patrol-completed',
        expect.any(String),
      )
    })
  })

  it('requires current-attestation devices to bind Inventory instead of accepting external identity', async () => {
    offlineGet.mockResolvedValue({
      id: 'offline-current-1',
      purpose: 'offline_patrol',
      report_intent: 'current_attestation',
      job_type: 'single_patrol',
      name: '当前现状证明',
      owner_id: 'operator',
      status: 'blocked',
      publish_events: false,
      attempt_number: 1,
      progress: { phase: 'blocked', percent: 100 },
      preview_totals: { file_count: 1, device_count: 1, command_count: 1 },
      blocker_count: 1,
      blockers: [{
        code: 'identity_binding_unconfirmed',
        message: '存在未与 Inventory 唯一确认的设备',
      }],
      archived: false,
      created_at: '2026-08-01T08:00:00Z',
      updated_at: '2026-08-01T08:05:00Z',
      periods: [{
        id: 'period-current-1',
        role: 'current',
        label: '当前周期',
        observed_at: '2026-08-01T07:30:00Z',
        dataset_id: 'offline-current-1-current',
        status: 'completed',
      }],
      source_files: [{
        id: 'source-current-1',
        period_role: 'current',
        relative_path: 'current.txt',
        status: 'verified',
        sha256: 'abc123',
      }],
      device_bindings: [{
        id: 'binding-current-1',
        period_role: 'current',
        external_device_id: 'W04B-SW1',
        device_name: 'W04B-SW1',
        management_ip: '192.0.2.40',
        match_status: 'external',
        confidence: 0,
        match_reason: 'external_device',
        match_candidates: [{
          inventory_device_id: 'inventory-current-1',
          device_name: 'W04B-SW1',
          management_ip: '192.0.2.40',
          vendor: 'H3C',
          model: 'S5130',
          confidence: 1,
          match_reason: 'management_ip',
        }],
      }],
    })
    const user = userEvent.setup()
    renderPanel('offline_patrol')

    await user.click(await screen.findByRole('button', { name: /详情/ }))
    await user.click(await screen.findByRole('tab', { name: /文件与设备/ }))

    expect(screen.queryByRole('button', { name: /作为外部设备/ })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: '确认匹配' }))

    expect(offlineConfirmBindings).toHaveBeenCalledWith('offline-current-1', [{
      binding_id: 'binding-current-1',
      inventory_device_id: 'inventory-current-1',
    }])
  })

  it('resumes an existing draft at upload without creating a duplicate job', async () => {
    const draft = {
      id: 'offline-draft-1',
      purpose: 'rule_asset_source',
      job_type: 'parser_validation',
      name: '待续传规则样本',
      owner_id: 'operator',
      status: 'draft',
      vendor: 'H3C',
      model: 'MSR5620',
      publish_events: false,
      folder_id: 'folder-draft-1',
      attempt_number: 0,
      progress: { phase: 'draft', percent: 0 },
      preview_totals: {},
      blocker_count: 0,
      blockers: [],
      source_files: [],
      periods: [{
        id: 'period-draft-1',
        role: 'sample',
        label: '样本周期',
        observed_at: '2026-03-23T09:00:00Z',
        dataset_id: 'offline-draft-1-sample',
        status: 'pending',
      }],
      archived: false,
      created_at: '2026-07-13T10:00:00Z',
      updated_at: '2026-07-13T10:01:00Z',
    }
    list.mockResolvedValueOnce({
      items: [draft], total: 1, page: 1, page_size: 25,
    })
    sourceGet.mockResolvedValue(draft)
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /继续上传/ }))

    expect(await screen.findByText('继续 CLI 样本导入')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /选择文件$/ })).toBeInTheDocument()
    expect(screen.getByText('0 个源文件')).toBeInTheDocument()
    expect(sourceGet).toHaveBeenCalledWith('offline-draft-1')
    expect(sourceCreate).not.toHaveBeenCalled()
    expect(sourceRetry).not.toHaveBeenCalled()
  })

  it('restores a failed job before reopening the same upload workspace', async () => {
    const failed = {
      id: 'offline-failed-1',
      purpose: 'rule_asset_source',
      job_type: 'parser_validation',
      name: '失败规则样本',
      owner_id: 'operator',
      status: 'failed',
      publish_events: false,
      folder_id: 'folder-failed-1',
      attempt_number: 1,
      progress: { phase: 'preview_failed', percent: 100 },
      preview_totals: {},
      blocker_count: 0,
      blockers: [],
      source_files: [],
      periods: [{
        id: 'period-failed-1',
        role: 'sample',
        label: '样本周期',
        observed_at: '2026-03-23T09:00:00Z',
        dataset_id: 'offline-failed-1-sample',
        status: 'pending',
      }],
      archived: false,
      created_at: '2026-07-13T10:00:00Z',
      updated_at: '2026-07-13T10:01:00Z',
    }
    const restored = {
      ...failed,
      status: 'draft',
      progress: { phase: 'draft', percent: 0 },
      error_message: null,
    }
    list.mockResolvedValueOnce({
      items: [failed], total: 1, page: 1, page_size: 25,
    })
    sourceGet.mockResolvedValue(failed)
    sourceRetry.mockResolvedValue(restored)
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /修复并继续/ }))

    expect(sourceRetry).toHaveBeenCalledWith('offline-failed-1')
    expect(await screen.findByText('继续 CLI 样本导入')).toBeInTheDocument()
    expect(sourceCreate).not.toHaveBeenCalled()
  })

  it('honors a deep-link tab change while keeping the same source job open', async () => {
    const detail = {
      id: 'offline-test-1',
      purpose: 'rule_asset_source',
      job_type: 'parser_validation',
      name: 'Rule asset source',
      owner_id: 'operator',
      status: 'blocked',
      publish_events: false,
      attempt_number: 0,
      progress: { phase: 'blocked', percent: 100 },
      preview_totals: { file_count: 1, device_count: 1, command_count: 1 },
      blocker_count: 1,
      blockers: [],
      archived: false,
      created_at: '2026-07-13T10:00:00Z',
      updated_at: '2026-07-13T10:01:00Z',
      periods: [],
      source_files: [{
        id: 'source-1',
        period_role: 'sample',
        relative_path: 'sample.txt',
        status: 'ready',
        sha256: 'abc123',
      }],
      device_bindings: [],
    }
    sourceGet.mockResolvedValue(detail)
    const user = userEvent.setup()
    renderPanel('rule_asset_source', {
      initialEntries: [
        '/skills/parser?workspace=production&subtab=source-jobs&source_job_id=offline-test-1&source_job_tab=overview',
      ],
      accessory: <SameJobTabSwitch jobId="offline-test-1" />,
    })

    expect(await screen.findByRole('region', { name: 'CLI 样本导入任务详情' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    let tabs = await screen.findAllByRole('tab')
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')

    await user.click(screen.getByRole('button', { name: 'switch-same-job-source-tab' }))

    await waitFor(() => {
      tabs = screen.getAllByRole('tab')
      expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
    })
    expect(sourceGet).toHaveBeenCalledWith('offline-test-1')
  })

  it('uses an ambiguous detected identity as read-only sample context for rule generation', async () => {
    sourceGet.mockResolvedValue({
      id: 'offline-test-1',
      purpose: 'rule_asset_source',
      job_type: 'parser_validation',
      name: 'Q1 Parser 验证',
      owner_id: 'operator',
      status: 'blocked',
      publish_events: false,
      attempt_number: 0,
      progress: { phase: 'blocked', percent: 100 },
      preview_totals: { file_count: 1, device_count: 1, command_count: 1 },
      blocker_count: 1,
      blockers: [{ code: 'ambiguous_device_binding', message: '1 台设备存在多个资产匹配候选' }],
      archived: false,
      created_at: '2026-07-13T10:00:00Z',
      updated_at: '2026-07-13T10:01:00Z',
      source_files: [],
      periods: [],
      device_bindings: [{
        id: 'binding-1',
        period_role: 'sample',
        external_device_id: 'external-sw-1',
        device_name: 'SW-CONFLICT',
        management_ip: '192.0.2.10',
        match_status: 'ambiguous',
        confidence: 0.95,
        match_reason: 'multiple_inventory_candidates',
        match_candidates: [
          {
            inventory_device_id: 'inventory-by-ip',
            device_name: 'SW-BY-IP',
            management_ip: '192.0.2.10',
            vendor: 'Cisco',
            model: 'N9K',
            confidence: 0.95,
            match_reason: 'management_ip',
          },
          {
            inventory_device_id: 'inventory-by-name',
            device_name: 'SW-CONFLICT',
            management_ip: '192.0.2.11',
            vendor: 'H3C',
            model: 'S5130',
            confidence: 0.85,
            match_reason: 'device_name',
          },
        ],
      }],
    })
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /详情/ }))
    await user.click(await screen.findByRole('tab', { name: /文件与设备/ }))
    expect(await screen.findByText('样本设备识别')).toBeVisible()
    expect(screen.getByText('已识别为样本设备')).toBeVisible()
    expect(screen.queryByText('2 个候选，需人工选择')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'SW-CONFLICT 选择资产' })).not.toBeInTheDocument()
    expect(sourceConfirmBindings).not.toHaveBeenCalled()
  })

  it('lets an engineer confirm an empty command and continue the same job', async () => {
    sourceGet.mockResolvedValue({
      id: 'offline-test-1',
      purpose: 'rule_asset_source',
      report_intent: 'historical',
      job_type: 'history_compare',
      name: '双周期规则样本',
      owner_id: 'operator',
      status: 'blocked',
      publish_events: false,
      attempt_number: 1,
      progress: { phase: 'blocked', percent: 100 },
      preview_totals: { file_count: 2, device_count: 1, command_count: 2, empty_unverified: 1 },
      blocker_count: 1,
      blockers: [{
        code: 'empty_unverified',
        period_role: 'current',
        message: '命令输出为空，需人工确认是否可忽略：1 条',
      }],
      archived: false,
      created_at: '2026-08-08T10:00:00Z',
      updated_at: '2026-08-08T10:01:00Z',
      periods: [],
      source_files: [],
      device_bindings: [],
      output_dispositions: [],
      output_review_items: [{
        review_item_id: 'output-review-vpc-empty',
        period_role: 'current',
        source_relative_path: 'Q3/N9K-1.txt',
        device_id: 'N9K-1-192.0.2.10',
        device_name: 'N9K-1',
        management_ip: '192.0.2.10',
        canonical_command: 'show vpc',
        start_line: 20,
        end_line: 21,
        raw_text_hash: '0'.repeat(64),
        raw_text_length: 0,
        status: 'empty_unverified',
      }],
    })
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /详情/ }))
    await user.click(await screen.findByRole('tab', { name: /输出确认/ }))
    await user.click(screen.getByRole('checkbox', { name: '选择 N9K-1 show vpc' }))
    await user.click(screen.getByRole('button', { name: /确认为空并忽略所选命令/ }))

    await waitFor(() => {
      expect(sourceConfirmOutputDispositions).toHaveBeenCalledWith(
        'offline-test-1',
        ['output-review-vpc-empty'],
        '',
      )
    })
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
