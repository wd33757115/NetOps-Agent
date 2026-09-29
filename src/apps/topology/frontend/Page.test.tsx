import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api }))
vi.mock('@netops/platform/terminal/DeviceTerminal', () => ({
  default: () => null,
  readHostKey: vi.fn().mockResolvedValue({ host: '10.0.0.1', port: 22, status: 'trusted', key_type: 'ssh-rsa', fingerprint: 'SHA256:old', observed_fingerprint: 'SHA256:new' }),
  acceptHostKey: vi.fn().mockResolvedValue({}),
}))

const target = (reason: string) => ({ id: 't1', address: '10.0.0.1', depth: 0, status: 'waiting_for_input', reason, version: 2 })
const run = (reason: string) => ({ id: 'r1', network_id: 'n1', status: 'waiting_for_input', port: 22, created_at: '2026-09-29T00:00:00Z', graph: { nodes: [], links: [], model_profile: 'simap' }, targets: [target(reason)] })
let state: { configured: boolean; networks: object[]; reason: string }

beforeEach(() => {
  vi.clearAllMocks()
  state = { configured: true, networks: [], reason: 'authentication_failed' }
  api.get.mockImplementation((url: string) => Promise.resolve({ data:
    url.endsWith('/default-login') ? { username: state.configured ? 'admin' : null, configured: state.configured }
      : url.endsWith('/networks') ? state.networks
        : url.endsWith('/runs') ? [{ id: 'r1', status: 'waiting_for_input', created_at: '2026-09-29T00:00:00Z' }]
          : run(state.reason) }))
  api.post.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/runs') ? { ...run('authentication_failed'), status: 'queued', targets: [] } : run(state.reason) }))
  api.put.mockResolvedValue({ data: {} })
})
afterEach(cleanup)

const startDiscovery = async (login?: { username: string; password: string }) => {
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '新建发现任务' }))
  fireEvent.change(screen.getByLabelText('种子设备地址'), { target: { value: '10.0.0.1' } })
  fireEvent.change(screen.getByLabelText('本任务允许的管理网段'), { target: { value: '10.0.0.0/24' } })
  if (login) {
    fireEvent.change(screen.getByLabelText('本次用户名（可选）'), { target: { value: login.username } })
    fireEvent.change(screen.getByLabelText('本次密码（可选）'), { target: { value: login.password } })
  }
  fireEvent.click(screen.getByRole('button', { name: '开始发现' }))
}

it('has no management domain or neighbour option and uses the default login when empty', async () => {
  await startDiscovery()
  expect(screen.queryByLabelText('管理域')).not.toBeInTheDocument()
  expect(screen.queryByText(/允许本次凭据用于/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '访问范围' })).not.toBeInTheDocument()
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/topology/runs', expect.anything()))
  const body = api.post.mock.calls[0][1]
  expect(body).not.toHaveProperty('credentials')
  expect(body).not.toHaveProperty('domain')
  expect(body).toMatchObject({ seeds: ['10.0.0.1'], allowed_cidrs: ['10.0.0.0/24'], max_depth: 3, max_targets: 30 })
})

it('sends a temporary login for this run only', async () => {
  await startDiscovery({ username: 'ops', password: 'run-test-secret' })
  await waitFor(() => expect(api.post).toHaveBeenCalled())
  expect(api.post.mock.calls[0][1].credentials).toEqual({ username: 'ops', password: 'run-test-secret' })
})

it('needs a temporary login when no default is saved', async () => {
  state.configured = false
  await startDiscovery()
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/topology/default-login'))
  expect(api.post).not.toHaveBeenCalled()
})

it('saves the default login', async () => {
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: /默认登录/ }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('用户名'), { target: { value: 'admin' } })
  fireEvent.change(within(dialog).getByLabelText('密码'), { target: { value: 'default-test-secret' } })
  fireEvent.click(within(dialog).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/topology/default-login', { username: 'admin', password: 'default-test-secret' }))
})

it('asks for a new login for a node whose login failed and retries only that node', async () => {
  state.networks = [{ id: 'n1', name: 'lab' }]
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '人工处理' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('为此节点输入新的用户名和密码')).toBeChecked()
  fireEvent.change(within(dialog).getByLabelText('用户名'), { target: { value: 'typed' } })
  fireEvent.change(within(dialog).getByLabelText('密码'), { target: { value: 'node-test-secret' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '确认并重试一次' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/topology/runs/r1/targets/t1/resolve', expect.objectContaining({ action: 'retry', version: 2, credentials: { username: 'typed', password: 'node-test-secret' } })))
})

it('a connection failure offers retry or skip without asking for a login', async () => {
  state.networks = [{ id: 'n1', name: 'lab' }]
  state.reason = 'connection_failed'
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '人工处理' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('为此节点输入新的用户名和密码')).not.toBeChecked()
  fireEvent.click(within(dialog).getByRole('button', { name: '跳过此节点' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/topology/runs/r1/targets/t1/resolve', expect.objectContaining({ action: 'skip' })))
})

it('shows both fingerprints when a node host key changed and trusts the new one before retrying', async () => {
  const terminal = await import('@netops/platform/terminal/DeviceTerminal')
  state.networks = [{ id: 'n1', name: 'lab' }]
  state.reason = 'host_key_changed'
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '人工处理' }))
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByText('SHA256:new')).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: '确认并重试一次' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/topology/runs/r1/targets/t1/resolve', expect.objectContaining({ action: 'retry' })))
  expect(terminal.acceptHostKey).toHaveBeenCalled()
})
