import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { describe, expect, it, vi } from 'vitest'
import RuleAssetBundleWorkspace from './RuleAssetBundleWorkspace'

const { listBundles } = vi.hoisted(() => ({
  listBundles: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'bundle-ready',
        status: 'pending_approval',
        source_ref: 'offline:job-1',
        observation_type: 'interface.oper_status',
        command: 'display interface brief',
        context_id: 'context-1',
        context_digest: 'a'.repeat(64),
        context_gate: {
          success: true,
          mode: 'immutable_dataset_context',
          context_id: 'context-1',
          context_digest: 'a'.repeat(64),
          errors: [],
          replay_run_id_count: 3,
          privacy: {
            success: true,
            raw_cli_included: false,
            scope_type: 'offline_period_authoritative_runs',
            source_snapshot_count: 6,
            candidate_record_count: 548,
            sampled_record_count: 354,
          },
        },
        offline_job_id: 'offline-job-1',
        dataset_ids: ['dataset-q1', 'dataset-q2'],
        asset_counts: { OBSERVATION_CONTRACT: 1, CHANGE_POLICY: 1, EVENT_RULE: 2 },
        active_asset_counts: {},
        release_ready: true,
        blockers: [],
        replay: {
          sampled_change_count: 62,
          matched_change_count: 26,
          collision_count: 0,
          event_count: 6,
          event_types: {
            transceiver_rx_power_low: 4,
            transceiver_rx_power_low_recovery: 2,
          },
          replacement_rule_ids: ['old-rule-1', 'old-rule-2'],
          replacement_logical_keys: ['rx-power:low', 'rx-power:recovery'],
          estimated_daily_event_count: 3,
          scope: {
            mode: 'run_ids',
            run_ids: ['run-q1', 'run-q2', 'run-q3'],
            run_id_count: 3,
            sampled_run_ids: ['run-q2', 'run-q3'],
            sampled_run_id_count: 2,
            missing_run_ids: ['run-q1'],
          },
        },
        replay_gate: {
          success: true,
          scope_required: true,
          scope_mode: 'run_ids',
          expected_run_ids: ['run-q1', 'run-q2', 'run-q3'],
          actual_run_ids: ['run-q1', 'run-q2', 'run-q3'],
          errors: [],
        },
        steps: [
          { key: 'context', label: 'context', status: 'passed', detail: {} },
          { key: 'contract', label: 'contract', status: 'passed', detail: {} },
          { key: 'policy', label: 'policy', status: 'passed', detail: {} },
          { key: 'rules', label: 'rules', status: 'passed', detail: {} },
          { key: 'replay', label: 'replay', status: 'passed', detail: { matched_change_count: 26, collision_count: 0 } },
          { key: 'release', label: 'release', status: 'ready', detail: {} },
        ],
        children: [
          {
            id: 'contract-job',
            asset_type: 'OBSERVATION_CONTRACT',
            asset_version_id: 'contract-v1',
            status: 'pending_approval',
          },
        ],
        created_at: '2026-07-14T00:00:00Z',
      },
      {
        id: 'bundle-blocked',
        status: 'rejected',
        source_ref: 'offline:job-2',
        observation_type: 'interface.optical.rx_power',
        command: 'display transceiver diagnosis interface',
        context_id: 'context-2',
        context_digest: 'b'.repeat(64),
        asset_counts: { OBSERVATION_CONTRACT: 1, CHANGE_POLICY: 1, EVENT_RULE: 2 },
        active_asset_counts: {},
        release_ready: false,
        blockers: [
          'historical replay is missing, failed or has collisions',
          'historical replay is missing, failed or has collisions',
          'historical replay run_ids do not match immutable context',
        ],
        replay: {
          sampled_change_count: 20,
          matched_change_count: 4,
          collision_count: 1,
          scope: { mode: 'run_ids', run_ids: ['run-other'], run_id_count: 1 },
        },
        replay_gate: {
          success: false,
          scope_required: true,
          scope_mode: 'run_ids',
          expected_run_ids: ['run-q1', 'run-q2'],
          actual_run_ids: ['run-other'],
          errors: ['historical replay run_ids do not match immutable context'],
        },
        steps: [
          { key: 'context', label: 'context', status: 'passed', detail: {} },
          { key: 'replay', label: 'replay', status: 'blocked', detail: { matched_change_count: 4, collision_count: 1 } },
          { key: 'release', label: 'release', status: 'blocked', detail: {} },
        ],
        children: [],
        created_at: '2026-07-14T00:00:00Z',
      },
    ],
    total: 10001,
    limit: 20,
    offset: 0,
  }),
}))

vi.mock('../../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../services/api')>()
  return {
    ...original,
    parserApi: {
      ...original.parserApi,
      ruleAssetBundles: listBundles,
      approveRuleGenerationJob: vi.fn(),
      rejectRuleGenerationJob: vi.fn(),
      deprecateRuleGenerationJob: vi.fn(),
    },
  }
})

const renderWorkspace = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RuleAssetBundleWorkspace />
    </QueryClientProvider>,
  )
}

const renderFocusedWorkspace = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RuleAssetBundleWorkspace initialSearch="offline-job-1" />
    </QueryClientProvider>,
  )
}

describe('RuleAssetBundleWorkspace', () => {
  it('shows the governed release path, blockers and server pagination', async () => {
    const user = userEvent.setup()
    renderWorkspace()

    expect(await screen.findByText('interface.oper_status')).toBeVisible()
    expect(screen.getByText('全部门禁已通过')).toBeVisible()
    expect(screen.getByText(/历史回放缺失、失败或存在规则碰撞/)).toBeVisible()
    expect(screen.getByText(/历史回放 Run 范围与不可变上下文不一致/)).toBeVisible()
    expect(screen.getByText('共 10001 套')).toBeVisible()
    expect(screen.getByRole('button', { name: /审批发布/ })).toBeEnabled()
    expect(screen.getByText('限定 3 个巡检 Run')).toBeVisible()

    await user.click(screen.getAllByRole('button', { name: 'Expand row' })[0])
    expect(await screen.findByText('回放摘要')).toBeVisible()
    expect(screen.getByText('LLM 数据边界')).toBeVisible()
    expect(screen.getByText('隐私门禁已通过')).toBeVisible()
    expect(screen.getByText('原始 CLI 0')).toBeVisible()
    expect(screen.getByText('来源 Snapshot 6')).toBeVisible()
    expect(screen.getByText('脱敏结构化记录 354')).toBeVisible()
    expect(screen.getByText('事件 6')).toBeVisible()
    expect(screen.getByText('替换旧规则 2（不计碰撞）')).toBeVisible()
    expect(screen.getByText('transceiver_rx_power_low 4')).toBeVisible()
    expect(screen.getByText('预估每日事件 3')).toBeVisible()
    expect(screen.getByText('范围门禁已通过')).toBeVisible()
    expect(screen.getByText(/巡检 Run：run-q1、run-q2、run-q3/)).toBeVisible()
    expect(screen.getByText(/无 Change 的基线 Run：run-q1/)).toBeVisible()
    expect(screen.getByText(/观测契约 · contract-v1/)).toBeVisible()
    expect(screen.getByText('审批发布', { selector: '.ant-steps-item-title' })).toBeVisible()
  })

  it('focuses bundles by immutable offline job context', async () => {
    renderFocusedWorkspace()

    expect(await screen.findByText(/正在查看离线任务 offline-job-1/)).toBeVisible()
    expect(listBundles).toHaveBeenCalledWith(expect.objectContaining({ search: 'offline-job-1' }))
  })
})
