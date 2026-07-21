import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { describe, expect, it, vi } from 'vitest'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

const { getDraft, reviewBundle } = vi.hoisted(() => ({
  getDraft: vi.fn().mockResolvedValue({
    bundle_id: 'bundle-1',
    bundle_status: 'pending_approval',
    base_digest: 'a'.repeat(64),
    context_id: 'context-1',
    context_digest: 'b'.repeat(64),
    offline_job_id: 'offline-1',
    contract: { entity_type: 'interface_l2', primary_keys: ['interface'] },
    change_policies: [{ observation_type: 'interface.oper_status', compare_mode: 'state_transition' }],
    event_rules: [{ event_type: 'InterfaceDown', condition: { field: 'status' } }],
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
})
