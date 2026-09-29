import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api }))
vi.mock('@netops/context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'operator', role: 'operator' } }) }))
vi.mock('@netops/platform/terminal/DeviceTerminal', () => ({
  LiveTerminal: ({ target }: { target: { session_id: string } }) => <div>终端 {target.session_id}</div>,
}))

const sessions = [
  { id: 's1', name: '核心交换机', host: '10.0.0.1', port: 22, username: null, uses_default: true },
  { id: 's2', name: '边界路由器', host: '10.0.0.2', port: 2222, username: 'ops', uses_default: false },
]

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/sessions') ? sessions : { username: 'admin', configured: true } }))
  for (const method of [api.post, api.patch, api.put, api.delete]) method.mockResolvedValue({ data: {} })
})
afterEach(cleanup)

const view = () => render(<MemoryRouter><Page /></MemoryRouter>)

it('lists saved sessions and opens each double-click as a tab', async () => {
  view()
  expect(await screen.findByText('核心交换机')).toBeInTheDocument()
  expect(screen.getByText('10.0.0.1 · 默认登录')).toBeInTheDocument()
  expect(screen.getByText('10.0.0.2:2222 · ops')).toBeInTheDocument()
  expect(screen.getByText('admin · 密码已加密保存')).toBeInTheDocument()
  fireEvent.doubleClick(screen.getByText('核心交换机'))
  fireEvent.doubleClick(screen.getByText('边界路由器'))
  expect(screen.getByRole('tab', { name: '核心交换机' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '边界路由器' })).toBeInTheDocument()
  expect(screen.getByText('终端 s2')).toBeInTheDocument()
})

it('batch edits the username and password of the selected sessions', async () => {
  view()
  fireEvent.click(await screen.findByLabelText('选择 核心交换机'))
  fireEvent.click(screen.getByLabelText('选择 边界路由器'))
  fireEvent.click(screen.getByRole('button', { name: '批量修改用户名密码' }))
  expect(await screen.findByText('修改 2 个会话的用户名密码')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'ops' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'batch-test-secret' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/sessions/login', { username: 'ops', password: 'batch-test-secret', session_ids: ['s1', 's2'] }))
})

it('creates a session that uses the default login when both fields are empty', async () => {
  view()
  fireEvent.click(await screen.findByRole('button', { name: '新建会话' }))
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '接入交换机' } })
  fireEvent.change(screen.getByLabelText('地址'), { target: { value: '10.0.0.3' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/sessions', { name: '接入交换机', host: '10.0.0.3', port: 22 }))
})

it('rejects a username without a password', async () => {
  view()
  fireEvent.click(await screen.findByRole('button', { name: '新建会话' }))
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '接入交换机' } })
  fireEvent.change(screen.getByLabelText('地址'), { target: { value: '10.0.0.3' } })
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'ops' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(screen.getByLabelText('名称')).toBeInTheDocument())
  expect(api.post).not.toHaveBeenCalled()
})

it('saves the default username and password', async () => {
  view()
  fireEvent.click(await screen.findByText('默认用户名密码'))
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'admin' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'default-test-secret' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/device-access/default-login', { username: 'admin', password: 'default-test-secret' }))
})
