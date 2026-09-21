import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

const { getDraft, reviewBundle, reviseBundle, listTasks, getTask } = vi.hoisted(() => ({
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getDraft: vi.fn().mockResolvedValue({
    bundle_id: 'bundle-1',
    bundle_status: 'pending_approval',
    base_digest: 'a'.repeat(64),
    context_id: 'context-1',
    context_digest: 'b'.repeat(64),
    offline_job_id: 'offline-1',
    contract: {
      entity_type: 'interface_l2',
      primary_keys: ['interface'],
      state_semantics: {
        interface: { semantic_type: 'identity', monitor: false },
        status: {
          semantic_type: 'state',
          observation_type: 'interface.oper_status',
          comparison_role: 'current_value',
          monitor: true,
        },
      },
    },
    change_policies: [{
      observation_type: 'interface.oper_status',
      semantic_type: 'state',
      compare_mode: 'state_transition',
    }],
    event_rules: [{
      event_type: 'InterfaceDown',
      severity: 'major',
      event_class: 'fault',
      trigger_role: 'fault',
      condition: {
        observation_types: ['interface.oper_status'],
        field: 'status',
      },
    }],
    review: {},
    editable: true,
    completeness: { contract_count: 1, change_policy_count: 1, event_rule_count: 1, complete: true },
  }),
  reviewBundle: vi.fn().mockResolvedValue({ success: true }),
  reviseBundle: vi.fn().mockResolvedValue({ success: true, bundle_id: 'bundle-2' }),
}))

vi.mock('@netops/services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@netops/services/api')>()
  return {
    ...original,
    parserApi: {
      ...original.parserApi,
      ruleAssetEngineeringDraft: getDraft,
      reviewRuleAssetBundle: reviewBundle,
      reviseRuleAssetBundle: reviseBundle,
      engineeringRevisionTasks: listTasks,
      engineeringRevisionTask: getTask,
      inspectEngineeringRevision: vi.fn().mockResolvedValue({ records: [], digest: 'test', can_resolve: false }),
    },
  }
})

describe('RuleAssetEngineeringReviewDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listTasks.mockResolvedValue({ items: [] })
    getTask.mockResolvedValue({ task_id: 'saved-task', state: 'running', status: 'STARTED', ready: false })
  })

  it.each(['running', 'needs_attention'])('restores a %s request after reload and blocks duplicate revision', async (state) => {
    const task = { task_id: 'saved-task', state, status: state === 'running' ? 'STARTED' : 'FAILURE', ready: state !== 'running', error: state === 'needs_attention' ? '请核查已生成的资产' : null }
    listTasks.mockResolvedValue({ items: [task] })
    getTask.mockResolvedValue(task)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer open bundle={{ id: 'bundle-1', status: 'pending_approval' } as never} onClose={vi.fn()} onChanged={vi.fn()} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(getTask).toHaveBeenCalledWith('saved-task'))
    await userEvent.type(screen.getByPlaceholderText(/如需保存修订/), '修订原因需要保留')
    expect(screen.getByRole('button', { name: /保存为新版本并重新验证/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /接受候选/ })).toBeDisabled()
    expect(reviseBundle).not.toHaveBeenCalled()
    client.clear()
  })

  it('reuses the request identity after an unacknowledged network failure', async () => {
    reviseBundle.mockRejectedValueOnce(new Error('network timeout'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer open bundle={{ id: 'bundle-1', status: 'pending_approval' } as never} onClose={vi.fn()} onChanged={vi.fn()} />
      </QueryClientProvider>,
    )
    await screen.findByText('interface.oper_status')
    await userEvent.type(screen.getByPlaceholderText(/如需保存修订/), '修订原因需要保留')
    const button = screen.getByRole('button', { name: /保存为新版本并重新验证/ })
    await userEvent.click(button)
    await waitFor(() => expect(reviseBundle).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    await waitFor(() => expect(reviseBundle).toHaveBeenCalledTimes(2))
    expect(reviseBundle.mock.calls[1][1].idempotency_key).toBe(reviseBundle.mock.calls[0][1].idempotency_key)
    client.clear()
  })

  it('allows a corrected submission after a definitive validation failure', async () => {
    listTasks.mockResolvedValue({ items: [{ task_id: 'saved-task', state: 'running', ready: false }] })
    getTask.mockResolvedValue({ task_id: 'saved-task', state: 'failed', status: 'FAILURE', ready: true, error: '规则校验失败' })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer open bundle={{ id: 'bundle-1', status: 'pending_approval' } as never} onClose={vi.fn()} onChanged={vi.fn()} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(getTask).toHaveBeenCalledWith('saved-task'))
    await userEvent.type(screen.getByPlaceholderText(/如需保存修订/), '修复规则校验失败')
    expect(screen.getByRole('button', { name: /保存为新版本并重新验证/ })).toBeEnabled()
    expect(document.querySelector('.ant-progress-status-exception')).not.toBeNull()
    client.clear()
  })

  it('blocks changes when previous request history cannot be read', async () => {
    listTasks.mockRejectedValueOnce(new Error('history unavailable'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer open bundle={{ id: 'bundle-1', status: 'pending_approval' } as never} onClose={vi.fn()} onChanged={vi.fn()} />
      </QueryClientProvider>,
    )
    await screen.findByText('暂时无法确认已有修订任务，请刷新后重试')
    await userEvent.type(screen.getByPlaceholderText(/如需保存修订/), '修订原因需要保留')
    expect(screen.getByRole('button', { name: /保存为新版本并重新验证/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /接受候选/ })).toBeDisabled()
    client.clear()
  })

  it('accepts a validated LLM candidate without forcing a narrative note', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const changed = vi.fn()
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer
          open
          bundle={{ id: 'bundle-1', status: 'pending_approval' } as never}
          onClose={vi.fn()}
          onChanged={changed}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('工程师复核与候选修订')).toBeVisible()
    expect(screen.getByText('复核摘要')).toBeVisible()
    expect(await screen.findByText('本候选包含 1 条 Current State / Event Rule，覆盖 1 个观测语义')).toBeVisible()
    const accept = screen.getByRole('button', { name: /接受候选/ })
    expect(accept).toBeEnabled()
    await user.click(accept)

    await waitFor(() => expect(reviewBundle).toHaveBeenCalledWith(
      'bundle-1',
      { decision: 'accepted', reason: '' },
    ))
    expect(changed).toHaveBeenCalled()
  }, 10000)

  it('summarizes multi-record contracts by record set instead of showing empty top-level fields', async () => {
    getDraft.mockResolvedValueOnce({
      bundle_id: 'bundle-multi-record',
      bundle_status: 'pending_approval',
      base_digest: 'm'.repeat(64),
      context_id: 'context-multi-record',
      contract: {
        entity_type: 'multi_record',
        primary_keys: [],
        state_semantics: {},
        record_sets: [
          {
            name: 'fans',
            canonical_entity_type: 'environment_fan',
            primary_keys: ['fan'],
            state_semantics: {
              fan: { semantic_type: 'identity', monitor: false },
              fan_status: {
                semantic_type: 'state',
                observation_type: 'environment.fan.state',
                monitor: true,
              },
            },
          },
          {
            name: 'summary',
            entity_type: 'device',
            primary_keys: [],
            state_semantics: {
              fan_zone_speed_raw: { semantic_type: 'text', monitor: false },
            },
          },
        ],
      },
      change_policies: [{
        observation_type: 'environment.fan.state',
        semantic_type: 'state',
        compare_mode: 'enum_transition',
      }],
      event_rules: [{
        event_type: 'environment_fan_state_down',
        severity: 'major',
        event_class: 'fault',
        trigger_role: 'down',
        condition: { observation_types: ['environment.fan.state'] },
      }],
      review: {},
      editable: true,
      completeness: { contract_count: 1, change_policy_count: 1, event_rule_count: 1, complete: true },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer
          open
          bundle={{
            id: 'bundle-multi-record',
            status: 'pending_approval',
            asset_requirements: { requires_event_rule_assets: true, requires_historical_replay: false },
          } as never}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('多记录集（2 个）')).toBeVisible()
    expect(screen.getByText('fans：fan；summary：设备级')).toBeVisible()
    expect(screen.getByText('3')).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'Observation Contract' }))
    expect((await screen.findAllByText('fans')).length).toBe(2)
    expect(screen.getByText('summary')).toBeVisible()
    expect(screen.getByText('fan_status')).toBeVisible()
    expect(screen.getByText('fan_zone_speed_raw')).toBeVisible()
  })

  it('blocks acceptance but allows revision when replay has no Change evidence', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer
          open
          bundle={{
            id: 'bundle-1',
            status: 'pending_approval',
            replay_gate: {
              success: false,
              evidence_success: false,
              sampled_change_count: 0,
              errors: ['historical replay contains no Change evidence'],
            },
            asset_requirements: { requires_historical_replay: true },
          } as never}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('历史回放没有形成有效 Change 证据')).toBeVisible()
    await user.type(
      screen.getByPlaceholderText(/如需保存修订/),
      '回放结果需要补齐后才能审批',
    )
    expect(screen.getByRole('button', { name: /接受候选/ })).toBeDisabled()
    const revise = screen.getByRole('button', { name: /保存为新版本并重新验证/ })
    expect(revise).toBeEnabled()
    await user.click(revise)
    await waitFor(() => expect(reviseBundle).toHaveBeenCalledWith(
      'bundle-1',
      expect.objectContaining({ reason: '回放结果需要补齐后才能审批' }),
      { asyncMode: true },
    ))
    expect(reviewBundle).not.toHaveBeenCalled()
  })

  it('reviews a contract-only candidate without inventing replay or event requirements', async () => {
    getDraft.mockResolvedValueOnce({
      bundle_id: 'bundle-contract-only',
      bundle_status: 'pending_approval',
      base_digest: 'c'.repeat(64),
      context_id: 'context-contract-only',
      contract: {
        entity_type: 'interface_l3',
        primary_keys: ['interface'],
        state_semantics: {
          interface: { semantic_type: 'identity', monitor: false },
          ip_address: {
            semantic_type: 'state',
            observation_type: 'interface.ip.address',
            comparison_role: 'observation',
            monitor: true,
          },
        },
      },
      change_policies: [],
      event_rules: [],
      review: {},
      editable: true,
      completeness: { contract_count: 1, change_policy_count: 0, event_rule_count: 0, complete: true },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer
          open
          bundle={{
            id: 'bundle-contract-only',
            status: 'pending_approval',
            asset_requirements: { requires_historical_replay: false },
          } as never}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('interface.ip.address')).toBeVisible()
    expect(screen.getByText('本候选定义 1 个观测语义，不要求 Current State / Event Rule')).toBeVisible()
    expect(screen.getByText(/本检查项不生成 Change Policy 或 Current State \/ Event Rule/)).toBeVisible()
    expect(screen.queryByText('历史回放没有形成有效 Change 证据')).not.toBeInTheDocument()
  })

  it('does not present a missing current-state rule as a valid no-event bundle', async () => {
    getDraft.mockResolvedValueOnce({
      bundle_id: 'bundle-current-state-missing',
      bundle_status: 'pending_approval',
      base_digest: 'd'.repeat(64),
      context_id: 'context-current-state-missing',
      contract: {
        entity_type: 'fan',
        primary_keys: ['fan'],
        state_semantics: {
          fan: { semantic_type: 'identity', monitor: false },
          fan_status: {
            semantic_type: 'state',
            observation_type: 'environment.fan.state',
            comparison_role: 'observation',
            monitor: true,
          },
        },
      },
      change_policies: [{
        observation_type: 'environment.fan.state',
        semantic_type: 'state',
        compare_mode: 'enum_transition',
      }],
      event_rules: [],
      review: {},
      editable: true,
      completeness: { contract_count: 1, change_policy_count: 1, event_rule_count: 0, complete: false },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RuleAssetEngineeringReviewDrawer
          open
          bundle={{
            id: 'bundle-current-state-missing',
            status: 'pending_approval',
            asset_requirements: {
              requires_change_policy: true,
              requires_current_state_rule: true,
              requires_event_rule_assets: true,
              requires_historical_replay: false,
            },
          } as never}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('该候选要求 Current State / Event Rule，但当前未生成')).toBeVisible()
    expect(screen.getByText(/当前状态判断不依赖双周期 Change，因此不要求历史回放/)).toBeVisible()
    expect(screen.getByRole('button', { name: /接受候选/ })).toBeDisabled()
    expect(screen.queryByText(/本检查项不生成 Change\/Event/)).not.toBeInTheDocument()
  })
})
