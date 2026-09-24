import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { parserApi } from '@netops/services/api'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

it('reviews CheckItem rules without a legacy contract editor or generation call', async () => {
  vi.spyOn(parserApi, 'ruleAssetEngineeringDraft').mockResolvedValue({ change_policies: [{ name: 'cpu-change' }], event_rules: [{ name: 'cpu-high' }] } as never)
  const save = vi.spyOn(parserApi, 'reviewRuleAssetBundle').mockResolvedValue({} as never)
  const changed = vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RuleAssetEngineeringReviewDrawer bundle={{ id: 'current-rule-bundle' } as never} open onClose={vi.fn()} onChanged={changed} />
  </QueryClientProvider>)
  expect(await screen.findByText(/cpu-change/)).toBeVisible()
  expect(screen.getByText(/cpu-high/)).toBeVisible()
  expect(screen.queryByText('Observation Contract')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '通过复核' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('复核意见'), { target: { value: '已核对检查项语义' } })
  fireEvent.click(screen.getByRole('button', { name: '通过复核' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('current-rule-bundle', { decision: 'accepted', reason: '已核对检查项语义' }))
  await waitFor(() => expect(changed).toHaveBeenCalled())
})
