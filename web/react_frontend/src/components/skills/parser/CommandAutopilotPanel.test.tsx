import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { commandAutopilotApi, type CommandAutopilot } from '../../../services/commandAutopilot'
import { useIsAdmin } from '../../../context/AuthContext'
import CommandAutopilotPanel from './CommandAutopilotPanel'

vi.mock('../../../context/AuthContext', () => ({ useIsAdmin: vi.fn(() => false) }))
afterEach(() => vi.clearAllMocks())

function show(status: CommandAutopilot['status'], canEdit = false) {
  const state: CommandAutopilot = { status, stage: 'review', stage_label: '发布', can_edit: canEdit, history: [], outputs: {} }
  vi.spyOn(commandAutopilotApi, 'get').mockResolvedValue(state)
  const control = vi.spyOn(commandAutopilotApi, 'control').mockResolvedValue(state)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CommandAutopilotPanel jobId="source" unitId="unit" associated /></MemoryRouter></QueryClientProvider>)
  return control
}

it('lets a running task be paused for human intervention', async () => {
  const control = show('running')
  await userEvent.click(await screen.findByRole('button', { name: '暂停并接管' }))
  expect(control).toHaveBeenCalledWith('source', 'unit', 'pause')
})

it('waits for the active step to stop before resuming', async () => {
  show('paused', false)
  expect(await screen.findByRole('button', { name: '继续自动制作' })).toBeDisabled()
})

it('does not offer publication authority to an operator', async () => {
  vi.mocked(useIsAdmin).mockReturnValue(false)
  show('needs_authorization', true)
  await screen.findByText('制作完成，等待管理员授权发布')
  expect(screen.queryByRole('button', { name: '授权自动发布' })).not.toBeInTheDocument()
})

it('lets an administrator authorize the prepared task', async () => {
  vi.mocked(useIsAdmin).mockReturnValue(true)
  const control = show('needs_authorization', true)
  await userEvent.click(await screen.findByRole('button', { name: '授权自动发布' }))
  expect(control).toHaveBeenCalledWith('source', 'unit', 'authorize')
})
