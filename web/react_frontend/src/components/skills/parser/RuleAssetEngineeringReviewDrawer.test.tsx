import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

const { getDraft, reviewBundle } = vi.hoisted(() => ({
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
}))

vi.mock('../../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../services/api')>()
  return {
    ...original,
    parserApi: {
      ...original.parserApi,
      ruleAssetEngineeringDraft: getDraft,
      reviewRuleAssetBundle: reviewBundle,
      reviseRuleAssetBundle: vi.fn(),
    },
  }
})

describe('RuleAssetEngineeringReviewDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('requires an auditable reason before accepting an LLM candidate', async () => {
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
    expect(await screen.findByText('本候选包含 1 条事件规则，覆盖 1 个观测语义')).toBeVisible()
    const accept = screen.getByRole('button', { name: /接受候选/ })
    expect(accept).toBeDisabled()
    await user.type(
      screen.getByPlaceholderText(/说明接受依据/),
      '字段语义和事件条件符合离线样本',
    )
    expect(accept).toBeEnabled()
    await user.click(accept)

    await waitFor(() => expect(reviewBundle).toHaveBeenCalledWith(
      'bundle-1',
      expect.objectContaining({ decision: 'accepted' }),
    ))
    expect(changed).toHaveBeenCalled()
  })

  it('blocks review and revision when replay has no Change evidence', async () => {
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
          } as never}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('历史回放没有形成有效 Change 证据')).toBeVisible()
    await user.type(
      screen.getByPlaceholderText(/说明接受依据/),
      '回放结果需要补齐后才能审批',
    )
    expect(screen.getByRole('button', { name: /接受候选/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /保存为新版本并重新验证/ })).toBeDisabled()
    expect(reviewBundle).not.toHaveBeenCalled()
  })
})
