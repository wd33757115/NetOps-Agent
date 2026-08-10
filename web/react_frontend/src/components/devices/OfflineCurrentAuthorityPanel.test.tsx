import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, vi } from 'vitest'

import OfflineCurrentAuthorityPanel from './OfflineCurrentAuthorityPanel'

const {
  attestCurrentAuthority,
  auth,
  currentAuthority,
  patrolReadiness,
  prepareCurrentAuthority,
  revokeCurrentAuthority,
} = vi.hoisted(() => ({
  attestCurrentAuthority: vi.fn(),
  auth: {
    user: { id: 'admin-reviewer', role: 'admin', username: 'reviewer' },
  },
  currentAuthority: vi.fn(),
  patrolReadiness: vi.fn(),
  prepareCurrentAuthority: vi.fn(),
  revokeCurrentAuthority: vi.fn(),
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: auth.user }),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../services/api')>()
  return {
    ...original,
    offlinePatrolApi: {
      ...original.offlinePatrolApi,
      currentAuthority,
      prepareCurrentAuthority,
      attestCurrentAuthority,
      revokeCurrentAuthority,
    },
    deviceApi: {
      ...original.deviceApi,
      patrolReadiness,
    },
  }
})

const currentJob = {
  id: 'offline-current-1',
  purpose: 'offline_patrol',
  report_intent: 'current_attestation',
  job_type: 'single_patrol',
  name: '当前现状证明',
  owner_id: 'operator-importer',
  status: 'completed',
  publish_events: false,
  attempt_number: 1,
  progress: { phase: 'completed', percent: 100 },
  preview_totals: { file_count: 2, device_count: 2, command_count: 10 },
  blocker_count: 0,
  archived: false,
  created_at: '2026-08-01T08:00:00Z',
  updated_at: '2026-08-01T08:05:00Z',
} as never

const renderPanel = (
  job = currentJob,
  actions: {
    onOpenSources?: () => void
    onOpenInventory?: () => void
    onOpenRuntimeAssets?: () => void
    onStartReplacement?: () => void
  } = {},
) => {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <OfflineCurrentAuthorityPanel job={job} {...actions} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  patrolReadiness.mockResolvedValue({
    schema_version: 'patrol-production-readiness/v1',
    status: 'ready',
    can_launch: true,
    checked_at: '2026-08-01T12:00:00Z',
    workflow_name: 'patrol-attested-offline-current',
    launch_mode: 'workflow',
    blocking_count: 0,
    warning_count: 0,
    checks: [],
    blockers: [],
    warnings: [],
  })
})

afterEach(() => {
  auth.user = { id: 'admin-reviewer', role: 'admin', username: 'reviewer' }
  currentAuthority.mockReset()
  patrolReadiness.mockReset()
  prepareCurrentAuthority.mockReset()
  attestCurrentAuthority.mockReset()
  revokeCurrentAuthority.mockReset()
})

describe('OfflineCurrentAuthorityPanel', () => {
  it('keeps historical jobs outside the current-authority workflow', async () => {
    renderPanel({
      ...currentJob,
      id: 'offline-history-1',
      report_intent: 'historical',
    } as never)

    expect(await screen.findByText('该任务仅用于历史分析')).toBeInTheDocument()
    expect(currentAuthority).not.toHaveBeenCalled()
    expect(screen.queryByText('独立复核')).not.toBeInTheDocument()
  })

  it('shows the imported evidence scope and the inventory binding action before preparation', async () => {
    currentAuthority.mockResolvedValue({
      status: 'not_requested',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    const onOpenSources = vi.fn()
    const onOpenInventory = vi.fn()
    const user = userEvent.setup()
    renderPanel({
      ...currentJob,
      source_files: [
        { id: 'source-1', period_role: 'current', status: 'verified' },
      ],
      device_bindings: [{
        id: 'binding-1',
        period_role: 'current',
        external_device_id: 'SW1',
        device_name: 'SW1',
        match_status: 'unmatched',
        confidence: 0,
      }],
      preview_totals: { file_count: 1, device_count: 1, command_count: 1 },
    } as never, { onOpenSources, onOpenInventory })

    expect(await screen.findByText('1 台设备 · 检查项待核验 · 1 个文件'))
      .toBeInTheDocument()
    expect(screen.getByText('1 台设备尚未绑定 Inventory')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '打开文件与设备' }))
    await user.click(screen.getByRole('button', { name: '前往设备资产' }))

    expect(onOpenSources).toHaveBeenCalledTimes(1)
    expect(onOpenInventory).toHaveBeenCalledTimes(1)
  })

  it('renders the service-owned remediation action for a blocked request', async () => {
    currentAuthority.mockResolvedValue({
      status: 'blocked',
      source_file_count: 1,
      device_count: 1,
      selected_check_item_count: 0,
      blockers: [{
        code: 'identity_binding_unconfirmed',
        message: '存在未与 Inventory 唯一确认的设备',
        remediation: {
          code: 'confirm_inventory_binding',
          label: '绑定 Inventory 设备',
          target: 'sources',
        },
      }],
      production_batch: { workflow_status_counts: {} },
    })
    const onOpenSources = vi.fn()
    const user = userEvent.setup()
    renderPanel(currentJob, { onOpenSources })

    expect(await screen.findByText('存在未与 Inventory 唯一确认的设备'))
      .toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '处理设备绑定' }))
    expect(onOpenSources).toHaveBeenCalledTimes(1)
  })

  it('explains an empty production plan and links to runtime asset remediation', async () => {
    currentAuthority.mockResolvedValue({
      status: 'failed',
      source_file_count: 1,
      device_count: 1,
      selected_check_item_count: 1,
      blockers: [],
      production_batch: {
        dispatch_status: 'failed',
        dispatch_error: 'authority_production_plan_incomplete: no active runtime bundle',
        dispatch_error_code: 'authority_production_plan_incomplete',
        workflow_status_counts: {},
      },
    })
    const onOpenRuntimeAssets = vi.fn()
    const user = userEvent.setup()
    renderPanel(currentJob, { onOpenRuntimeAssets })

    expect(await screen.findByText('当前范围没有可执行的生产规则资产'))
      .toBeInTheDocument()
    expect(screen.getByText(/没有把“未执行”显示为“已完成”/))
      .toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '前往规则资产控制面' }))
    expect(onOpenRuntimeAssets).toHaveBeenCalledTimes(1)
  })

  it('shows the five stages and submits the platform-owned evidence ledger', async () => {
    currentAuthority.mockResolvedValue({
      status: 'not_requested',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    prepareCurrentAuthority.mockResolvedValue({ status: 'ready_for_attestation' })
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('证据核验')).toBeInTheDocument()
    expect(screen.getByText('独立复核')).toBeInTheDocument()
    expect(screen.getByText('冻结计划')).toBeInTheDocument()
    expect(screen.getByText('生产执行')).toBeInTheDocument()
    expect(screen.getByText('报告门禁')).toBeInTheDocument()

    await user.type(
      screen.getByPlaceholderText('现场采集人或采集主体'),
      '西安现场采集组',
    )
    await user.click(screen.getByRole('button', { name: /核验证据并提交复核/ }))

    await waitFor(() => expect(prepareCurrentAuthority).toHaveBeenCalledTimes(1))
    expect(prepareCurrentAuthority).toHaveBeenCalledWith(
      'offline-current-1',
      expect.objectContaining({
        collector_identity: '西安现场采集组',
        completeness_evidence_refs: [
          'source_manifest',
          'identity_binding_manifest',
          'current_run_snapshot_manifest',
          'offline_job_result_ledger',
        ],
        observed_at_evidence_refs: [
          'current_period_observed_at',
          'snapshot_observed_at_set',
        ],
      }),
      expect.any(String),
    )
  })

  it('prevents the submitting principal from performing the independent review', async () => {
    auth.user = { id: 'operator-importer', role: 'admin', username: 'importer' }
    currentAuthority.mockResolvedValue({
      status: 'ready_for_attestation',
      request_digest: 'a'.repeat(64),
      requested_by_principal_id: 'operator-importer',
      requested_by_display: '导入管理员',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    renderPanel()

    expect(await screen.findByText('等待另一位管理员复核')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /确认材料并进入生产执行/ }))
      .not.toBeInTheDocument()
  })

  it('blocks attestation until the production runtime recovers', async () => {
    currentAuthority.mockResolvedValue({
      status: 'ready_for_attestation',
      request_digest: 'c'.repeat(64),
      requested_by_principal_id: 'operator-importer',
      requested_by_display: '导入管理员',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    patrolReadiness
      .mockResolvedValueOnce({
        schema_version: 'patrol-production-readiness/v1',
        status: 'blocked',
        can_launch: false,
        checked_at: '2026-08-01T12:00:00Z',
        workflow_name: 'patrol-attested-offline-current',
        launch_mode: 'workflow',
        blocking_count: 1,
        warning_count: 0,
        checks: [],
        blockers: [{
          id: 'celery_beat',
          name: '巡检恢复调度',
          scope: 'scheduling',
          status: 'blocked',
          message: 'Celery Beat 心跳状态为 stale',
          remediation: '启动 Celery Beat，并确认健康队列有 Worker 消费。',
          latency_ms: 1,
          detail: {},
          blocking: true,
          failure_code: 'patrol_celery_beat_not_ready',
        }],
        warnings: [],
      })
      .mockResolvedValueOnce({
        schema_version: 'patrol-production-readiness/v1',
        status: 'ready',
        can_launch: true,
        checked_at: '2026-08-01T12:01:00Z',
        workflow_name: 'patrol-attested-offline-current',
        launch_mode: 'workflow',
        blocking_count: 0,
        warning_count: 0,
        checks: [],
        blockers: [],
        warnings: [],
      })
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('生产巡检运行环境未就绪'))
      .toBeInTheDocument()
    expect(patrolReadiness).toHaveBeenCalledWith({
      workflow_name: 'patrol-attested-offline-current',
      launch_mode: 'workflow',
    })
    expect(screen.getByText(/Celery Beat 心跳状态为 stale/))
      .toBeInTheDocument()
    await user.type(
      screen.getByPlaceholderText('记录你核对了哪些证据，以及同意进入生产计划的依据'),
      '已核对冻结证据，等待生产运行环境恢复',
    )
    const attestButton = screen.getByRole('button', {
      name: /确认材料并进入生产执行/,
    })
    expect(attestButton).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /重新检查运行环境/ }))

    expect(await screen.findByText('生产巡检运行环境已就绪'))
      .toBeInTheDocument()
    expect(attestButton).not.toBeDisabled()
  })

  it('keeps runtime blockers visible after approval while dispatch is pending', async () => {
    currentAuthority.mockResolvedValue({
      status: 'approved',
      request_digest: 'd'.repeat(64),
      requested_by_principal_id: 'operator-importer',
      requested_by_display: '导入管理员',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    patrolReadiness.mockResolvedValue({
      schema_version: 'patrol-production-readiness/v1',
      status: 'blocked',
      can_launch: false,
      checked_at: '2026-08-01T12:02:00Z',
      workflow_name: 'patrol-attested-offline-current',
      launch_mode: 'workflow',
      blocking_count: 1,
      warning_count: 0,
      checks: [],
      blockers: [{
        id: 'celery_workers',
        name: '巡检 Worker',
        status: 'blocked',
        blocking: true,
        failure_code: 'patrol_celery_patrol_queues_not_ready',
        message: '巡检队列当前没有可用消费者',
        remediation: '恢复 Celery Worker 后平台会自动继续处理已批准请求',
        details: {},
      }],
      warnings: [],
    })

    renderPanel()

    expect(await screen.findByText('生产巡检运行环境未就绪'))
      .toBeInTheDocument()
    expect(screen.getByText(/恢复 Celery Worker 后平台会自动继续处理/))
      .toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /确认材料并进入生产执行/ }))
      .not.toBeInTheDocument()
  })

  it('allows a different administrator to attest the frozen request', async () => {
    currentAuthority.mockResolvedValue({
      status: 'ready_for_attestation',
      request_digest: 'b'.repeat(64),
      requested_by_principal_id: 'operator-importer',
      requested_by_display: '导入管理员',
      blockers: [],
      production_batch: { workflow_status_counts: {} },
    })
    attestCurrentAuthority.mockResolvedValue({ status: 'approved' })
    const user = userEvent.setup()
    renderPanel()

    await user.type(
      await screen.findByPlaceholderText('记录你核对了哪些证据，以及同意进入生产计划的依据'),
      '已核对文件哈希、设备范围和失败清单',
    )
    await user.click(screen.getByRole('button', { name: /确认材料并进入生产执行/ }))

    await waitFor(() => expect(attestCurrentAuthority).toHaveBeenCalledTimes(1))
    expect(attestCurrentAuthority).toHaveBeenCalledWith(
      'offline-current-1',
      {
        request_digest: 'b'.repeat(64),
        reason: '已核对文件哈希、设备范围和失败清单',
      },
      expect.any(String),
    )
  })
})
