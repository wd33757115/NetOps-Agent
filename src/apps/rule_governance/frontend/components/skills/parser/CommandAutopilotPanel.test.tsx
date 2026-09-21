import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { commandAutopilotApi, type CommandAutopilot } from '@netops/services/commandAutopilot'
import { useAuth } from '@netops/context/AuthContext'
import CommandAutopilotPanel from './CommandAutopilotPanel'

vi.mock('@netops/context/AuthContext', () => ({ useAuth: vi.fn(() => ({ user: { role: 'viewer', app_ids: ['rule-governance'] } })) }))
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

it('does not offer publication authority without App access', async () => {
  vi.mocked(useAuth).mockReturnValue({ user: { role: 'viewer', app_ids: [] } } as ReturnType<typeof useAuth>)
  show('needs_authorization', true)
  await screen.findByText('制作完成，等待确认发布')
  expect(screen.queryByRole('button', { name: '授权自动发布' })).not.toBeInTheDocument()
})

it('lets an authorized viewer confirm publication', async () => {
  vi.mocked(useAuth).mockReturnValue({ user: { role: 'viewer', app_ids: ['rule-governance'] } } as ReturnType<typeof useAuth>)
  const control = show('needs_authorization', true)
  await userEvent.click(await screen.findByRole('button', { name: '授权自动发布' }))
  expect(control).toHaveBeenCalledWith('source', 'unit', 'authorize')
})
