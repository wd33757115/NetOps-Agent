import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineeringRevisionResolution } from './EngineeringRevisionResolution'

const { inspect, resolve } = vi.hoisted(() => ({ inspect: vi.fn(), resolve: vi.fn() }))
vi.mock('@netops/services/api', () => ({ parserApi: { inspectEngineeringRevision: inspect, resolveEngineeringRevision: resolve } }))

const evidence = { digest: 'server-evidence', can_resolve: true, records: [{ id: 'partial-bundle', job_type: 'RULE_ASSET_BUNDLE', status: 'rejected', validation: { success: false } }] }
const mount = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onResolved = vi.fn()
  render(<QueryClientProvider client={client}><EngineeringRevisionResolution taskId="task-1" onResolved={onResolved} /></QueryClientProvider>)
  return { client, onResolved }
}

describe('EngineeringRevisionResolution', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    inspect.mockReset()
    resolve.mockReset()
    inspect.mockResolvedValue(evidence)
    resolve.mockResolvedValue({ state: 'resolved' })
  })

  it('shows actual partial assets and submits the reviewed server snapshot with a note', async () => {
    const { client, onResolved } = mount()
    expect(await screen.findByText('partial-bundle')).toBeVisible()
    expect(screen.getByText('rejected')).toBeVisible()
    const button = screen.getByRole('button', { name: '记录核查并结案' })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText('中断任务核查说明'), '保留候选供后续修复')
    await userEvent.click(button)
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('task-1', { evidence_digest: 'server-evidence', note: '保留候选供后续修复' }))
    await waitFor(() => expect(onResolved).toHaveBeenCalledOnce())
    client.clear()
  })

  it('refreshes conflicting evidence without automatically resubmitting', async () => {
    resolve.mockRejectedValueOnce(new Error('资产状态已变化'))
    const { client, onResolved } = mount()
    await screen.findByText('partial-bundle')
    inspect.mockResolvedValue({ ...evidence, digest: 'new-evidence' })
    await userEvent.type(screen.getByLabelText('中断任务核查说明'), '保留候选供后续修复')
    await userEvent.click(screen.getByRole('button', { name: '记录核查并结案' }))
    await waitFor(() => expect(inspect).toHaveBeenCalledTimes(2))
    expect(resolve).toHaveBeenCalledTimes(1)
    expect(onResolved).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('button', { name: '记录核查并结案' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '记录核查并结案' }))
    await waitFor(() => expect(resolve).toHaveBeenLastCalledWith('task-1', { evidence_digest: 'new-evidence', note: '保留候选供后续修复' }))
    client.clear()
  })

  it('blocks unsafe historical execution and displays the server explanation', async () => {
    inspect.mockResolvedValue({ ...evidence, can_resolve: false, blocked_reason: '需先核验旧 Worker 排空' })
    const { client } = mount()
    await screen.findByText('需先核验旧 Worker 排空')
    await userEvent.type(screen.getByLabelText('中断任务核查说明'), '保留候选供后续修复')
    expect(screen.getByRole('button', { name: '记录核查并结案' })).toBeDisabled()
    expect(resolve).not.toHaveBeenCalled()
    client.clear()
  })

  it('recovers a completed resolution after the response was lost', async () => {
    resolve.mockRejectedValueOnce(new Error('network timeout'))
    const { client, onResolved } = mount()
    await screen.findByText('partial-bundle')
    inspect.mockResolvedValue({ ...evidence, state: 'resolved', can_resolve: false })
    await userEvent.type(screen.getByLabelText('中断任务核查说明'), '保留候选供后续修复')
    await userEvent.click(screen.getByRole('button', { name: '记录核查并结案' }))
    await waitFor(() => expect(onResolved).toHaveBeenCalledOnce())
    expect(resolve).toHaveBeenCalledTimes(1)
    client.clear()
  })
})
