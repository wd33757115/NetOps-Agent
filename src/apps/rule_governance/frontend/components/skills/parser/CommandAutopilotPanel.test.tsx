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

function show(status: CommandAutopilot['status'], canEdit = false, error?: string) {
  const state: CommandAutopilot = { status, stage: 'review', stage_label: '发布', can_edit: canEdit, history: [], outputs: {}, error }
  vi.spyOn(commandAutopilotApi, 'get').mockResolvedValue(state)
  const control = vi.spyOn(commandAutopilotApi, 'control').mockResolvedValue(state)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CommandAutopilotPanel jobId="source" unitId="unit" associated /></MemoryRouter></QueryClientProvider>)
  return control
}

it('lets a running task be paused for human intervention', async () => {
  const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries')
  const control = show('running')
  await userEvent.click(await screen.findByRole('button', { name: '暂停并接管' }))
  expect(control).toHaveBeenCalledWith('source', 'unit', 'pause')
  expect(invalidate).toHaveBeenCalledWith(['offline-command-asset-unit', 'source', 'unit'])
  expect(invalidate).toHaveBeenCalledWith('parser-job-inline')
})

it('waits for the active step to stop before resuming', async () => {
  show('paused', false)
  expect(await screen.findByRole('button', { name: '继续自动制作' })).toBeDisabled()
})

it('explains a historical deployment cancellation and offers resume', async () => {
  const control = show('needs_attention', true, '应用已停用')
  expect(await screen.findByRole('alert')).toHaveTextContent('上次 Parser 任务在应用停用或升级时被取消')
  expect(screen.queryByText('应用已停用', { exact: true })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '继续自动制作' }))
  expect(control).toHaveBeenCalledWith('source', 'unit', 'resume')
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
