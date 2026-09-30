import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }))
const auth = vi.hoisted(() => ({ user: { username: 'operator', role: 'operator' } }))
vi.mock('@netops/config/api', () => ({ api }))
vi.mock('@netops/context/AuthContext', () => ({ useAuth: () => auth }))
vi.mock('@netops/platform/terminal/DeviceTerminal', () => ({
  LiveTerminal: ({ target }: { target: { session_id: string } }) => <div>终端 {target.session_id}</div>,
}))

const sessions = [
  { id: 's1', name: '核心交换机', host: '10.0.0.1', port: 22, username: null, uses_default: true },
  { id: 's2', name: '边界路由器', host: '10.0.0.2', port: 2222, username: 'ops', uses_default: false },
]
const sessionLogs = [
  { id: 'legacy:connection/1', filename: '核心交换机_20260930_110000.log', device_name: '核心交换机', host: '10.0.0.1', port: 22, source_app: 'device-access', actor_id: 'operator-1', started_at: '2026-09-30T11:00:00+08:00', updated_at: '2026-09-30T11:01:00+08:00', status: 'closed' },
  { id: 'connection2', filename: '核心交换机_20260930_120000.log', device_name: '核心交换机', host: '10.0.0.1', port: 22, source_app: 'inspection', actor_id: 'operator-1', started_at: '2026-09-30T12:00:00+08:00', updated_at: '2026-09-30T12:01:00+08:00', status: 'failed' },
]
const logText = '<H3C>display interface brief\nInterface      State\nGE1/0          UP\n[输出已截断]\n'

beforeEach(() => {
  vi.clearAllMocks()
  auth.user.role = 'operator'
  api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/sessions') ? sessions : url.endsWith('/audit') || url.endsWith('/session-logs') ? { items: [], next_cursor: null } : { username: 'admin', configured: true } }))
  for (const method of [api.post, api.patch, api.put, api.delete]) method.mockResolvedValue({ data: {} })
})
afterEach(() => {
  cleanup(); vi.unstubAllGlobals()
  if (vi.isMockFunction(HTMLAnchorElement.prototype.click)) vi.mocked(HTMLAnchorElement.prototype.click).mockRestore()
})

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

it('keeps command policy maintenance unavailable to ordinary users', async () => {
  view()
  await screen.findByText('核心交换机')
  expect(screen.queryByRole('button', { name: '命令白名单' })).not.toBeInTheDocument()
})

it('lets an admin replace the global whitelist, including an empty deny-all list', async () => {
  auth.user.role = 'admin'
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string) => url.endsWith('/command-policy') ? Promise.resolve({ data: { patterns: ['.*'], version: 1, updated_at: null, updated_by: null } }) : get(url))
  view()
  fireEvent.click(screen.getByRole('button', { name: '命令白名单' }))
  const rules = await screen.findByLabelText('命令白名单规则')
  await waitFor(() => expect(rules).toHaveValue('.*'))
  fireEvent.change(rules, { target: { value: '^display .*$\n^ping .*$' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/device-access/command-policy', { patterns: ['^display .*$', '^ping .*$'], version: 1 }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: '命令白名单' }))
  await waitFor(() => expect(screen.getByLabelText('命令白名单规则')).toHaveValue('.*'))
  fireEvent.change(screen.getByLabelText('命令白名单规则'), { target: { value: '' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/device-access/command-policy', { patterns: [], version: 1 }))
})

it('does not overwrite the policy when loading it fails', async () => {
  auth.user.role = 'admin'
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string) => url.endsWith('/command-policy') ? Promise.reject(new Error('读取失败')) : get(url))
  view()
  fireEvent.click(screen.getByRole('button', { name: '命令白名单' }))
  expect(await screen.findByText('读取失败')).toBeInTheDocument()
  expect(within(screen.getByRole('dialog')).getByRole('button', { name: /保\s*存/ })).toBeDisabled()
  expect(api.put).not.toHaveBeenCalled()
})

it('executes selected sessions in selection order and expands command results', async () => {
  api.post.mockResolvedValue({ data: { id: 'b1', status: 'completed', results: [{ session_id: 's2', name: '边界路由器', host: '10.0.0.2', status: 'succeeded', commands: [{ command: 'display version', status: 'succeeded', output: 'Comware test output' }] }] } })
  view()
  fireEvent.click(await screen.findByLabelText('选择 边界路由器'))
  fireEvent.click(screen.getByLabelText('选择 核心交换机'))
  fireEvent.click(screen.getByRole('tab', { name: '批量执行' }))
  fireEvent.change(screen.getByLabelText('批量命令'), { target: { value: ' display version \n\ndisplay interface brief' } })
  fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/batches', { request_id: expect.any(String), session_ids: ['s2', 's1'], commands: [' display version ', 'display interface brief'] }))
  const results = await screen.findByLabelText('批量执行结果')
  fireEvent.click(within(results).getByText('边界路由器').closest('summary')!)
  expect(within(results).getByText('Comware test output')).toBeVisible()
})

it('can stop a running batch and retains its results when changing tabs', async () => {
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string) => url.endsWith('/batches/b1') ? Promise.resolve({ data: { id: 'b1', status: 'cancelled', results: [] } }) : get(url))
  api.post.mockImplementation((url: string) => Promise.resolve({ data: { id: 'b1', status: 'running', results: [], cancel_requested: url.endsWith('/cancel') } }))
  view()
  fireEvent.click(await screen.findByLabelText('选择 核心交换机'))
  fireEvent.click(screen.getByRole('tab', { name: '批量执行' }))
  fireEvent.change(screen.getByLabelText('批量命令'), { target: { value: 'display version' } })
  fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
  expect(await screen.findByRole('button', { name: '停止执行' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始执行' })).toBeDisabled()
  fireEvent.click(screen.getByRole('tab', { name: '终端' }))
  fireEvent.click(screen.getByRole('tab', { name: '批量执行' }))
  fireEvent.click(screen.getByRole('button', { name: '停止执行' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/batches/b1/cancel'))
  expect(await screen.findByText('正在停止')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停止执行' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '刷新结果' }))
  expect(await screen.findByText('已停止')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始执行' })).not.toBeDisabled()
})

it('opens management records separately with the management-only filter', async () => {
  const get = api.get.getMockImplementation()!
  const record = { id: 'a1', created_at: '2026-09-30T01:00:00Z', user_id: '1', action: 'ssh.session_updated', status: 'succeeded', detail: { host: '10.0.0.1', name: '核心交换机' } }
  api.get.mockImplementation((url: string, config?: { params?: { before?: string } }) => url.endsWith('/audit') ? Promise.resolve({ data: { items: [{ ...record, id: config?.params?.before ? 'a2' : 'a1' }], next_cursor: config?.params?.before ? null : '50' } }) : get(url))
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  expect(api.get.mock.calls.filter(([url]) => url.endsWith('/audit'))).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: '管理记录' }))
  expect(await screen.findByText('查看自己的 SSH 管理操作。')).toBeInTheDocument()
  const first = await screen.findByText('修改会话')
  fireEvent.click(first.closest('summary')!)
  fireEvent.change(screen.getByLabelText('筛选设备地址'), { target: { value: '10.0.0.1' } })
  fireEvent.change(screen.getByRole('combobox', { name: '筛选操作类型' }), { target: { value: 'session_updated' } })
  fireEvent.change(screen.getByRole('combobox', { name: '筛选操作状态' }), { target: { value: 'succeeded' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /查\s*询/ }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/device-access/audit', { params: { host: '10.0.0.1', kind: 'session_updated', status: 'succeeded', management_only: true, limit: 50 } }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '加载更多' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/device-access/audit', { params: { host: '10.0.0.1', kind: 'session_updated', status: 'succeeded', management_only: true, limit: 50, before: '50' } }))
  await waitFor(() => expect(within(screen.getByLabelText('操作记录列表')).getAllByText('修改会话')).toHaveLength(2))
})

function mockSessionLogs(detail = logText) {
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string) => {
    if (url.endsWith('/session-logs')) return Promise.resolve({ data: { items: sessionLogs, next_cursor: null } })
    const found = sessionLogs.find(item => url.endsWith(`/session-logs/${encodeURIComponent(item.id)}`))
    return found ? Promise.resolve({ data: { ...found, content: detail } }) : get(url)
  })
}

it.each(['operator', 'admin'])('lists one log file per connection and keeps the %s scope visible', async role => {
  auth.user.role = role
  mockSessionLogs()
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  const file = await screen.findByRole('button', { name: sessionLogs[0].filename })
  expect(screen.getByRole('button', { name: sessionLogs[1].filename })).toBeInTheDocument()
  expect(screen.getByRole('table').querySelectorAll('tbody tr[data-row-key]')).toHaveLength(2)
  expect(screen.queryAllByText('· 用户 operator-1').length > 0).toBe(role === 'admin')
  expect(screen.getAllByRole('columnheader')).toHaveLength(3)
  expect(screen.getByText(role === 'admin' ? '所有用户的连接记录，一次连接一个日志文件。' : '自己的连接记录，一次连接一个日志文件。')).toBeInTheDocument()
  fireEvent.click(file)
  const preview = await screen.findByLabelText('日志内容')
  expect(preview.tagName).toBe('PRE')
  expect(preview.textContent).toBe(logText)
  expect(api.get).toHaveBeenCalledWith('/apps/device-access/session-logs/legacy%3Aconnection%2F1')
  expect(api.post).not.toHaveBeenCalled()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /关\s*闭/ }))
  fireEvent.click(screen.getByRole('button', { name: '管理记录' }))
  expect(await screen.findByText(role === 'admin' ? '查看所有用户的 SSH 管理操作。' : '查看自己的 SSH 管理操作。')).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith('/apps/device-access/audit', { params: { management_only: true, limit: 50 } })
})

it('filters and paginates session files by the server address query', async () => {
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string, config?: { params?: { before?: string | number } }) => url.endsWith('/session-logs') ? Promise.resolve({ data: { items: [sessionLogs[config?.params?.before ? 1 : 0]], next_cursor: config?.params?.before ? null : '50' } }) : get(url))
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  await screen.findByRole('button', { name: sessionLogs[0].filename })
  fireEvent.change(screen.getByLabelText('日志设备地址'), { target: { value: '10.0.0.1' } })
  fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/device-access/session-logs', { params: { host: '10.0.0.1', limit: 50, before: 0 } }))
  fireEvent.click(await screen.findByRole('button', { name: '加载更多' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/device-access/session-logs', { params: { host: '10.0.0.1', limit: 50, before: '50' } }))
  expect(await screen.findByRole('button', { name: sessionLogs[1].filename })).toBeInTheDocument()
})

it('ignores a stale preview response after opening a different connection', async () => {
  mockSessionLogs()
  const get = api.get.getMockImplementation()!
  let resolveFirst!: (value: unknown) => void
  api.get.mockImplementation((url: string) => url.endsWith('/legacy%3Aconnection%2F1') ? new Promise(resolve => { resolveFirst = resolve }) : get(url))
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  fireEvent.click(await screen.findByRole('button', { name: sessionLogs[0].filename }))
  expect(await screen.findByText('正在读取日志…')).toBeInTheDocument()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /关\s*闭/ }))
  fireEvent.click(screen.getByRole('button', { name: sessionLogs[1].filename }))
  expect(await screen.findByLabelText('日志内容')).toHaveTextContent('display interface brief')
  await act(async () => { resolveFirst({ data: { ...sessionLogs[0], content: 'wrong old connection' } }) })
  expect(screen.getByLabelText('日志内容').textContent).toBe(logText)
  expect(screen.queryByText('wrong old connection')).not.toBeInTheDocument()
})

it('lets the user retry a failed preview without executing device commands', async () => {
  mockSessionLogs()
  const get = api.get.getMockImplementation()!
  let failed = false
  api.get.mockImplementation((url: string) => {
    if (url.endsWith('/legacy%3Aconnection%2F1') && !failed) { failed = true; return Promise.reject(new Error('读取日志失败')) }
    return get(url)
  })
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  fireEvent.click(await screen.findByRole('button', { name: sessionLogs[0].filename }))
  expect(await screen.findByText('读取日志失败')).toBeInTheDocument()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /重\s*试/ }))
  expect(await screen.findByLabelText('日志内容')).toHaveTextContent('display interface brief')
  expect(api.post).not.toHaveBeenCalled()
})

it('downloads UTF-8 log files through the authenticated client using encoded ids and filename*', async () => {
  mockSessionLogs()
  const get = api.get.getMockImplementation()!
  const blob = new Blob([logText], { type: 'text/plain;charset=utf-8' })
  const create = vi.fn(() => 'blob:session-log')
  const revoke = vi.fn()
  vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = revoke })
  let downloaded: { href: string; name: string } | undefined
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) { downloaded = { href: this.href, name: this.download } })
  api.get.mockImplementation((url: string) => url.endsWith('/download') ? Promise.resolve({ data: blob, headers: { 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent('设备甲_连接时间.log')}` } }) : get(url))
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  fireEvent.click(await screen.findByRole('button', { name: `下载 ${sessionLogs[0].filename}` }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apps/device-access/session-logs/legacy%3Aconnection%2F1/download', { responseType: 'blob' }))
  await waitFor(() => expect(downloaded).toEqual({ href: 'blob:session-log', name: '设备甲_连接时间.log' }))
  expect(create).toHaveBeenCalledWith(blob)
  expect(revoke).toHaveBeenCalledWith('blob:session-log')
})

it('keeps a failed authenticated download recoverable without linking around authorization', async () => {
  mockSessionLogs()
  const get = api.get.getMockImplementation()!
  api.get.mockImplementation((url: string) => url.endsWith('/download') ? Promise.reject(new Error('没有权限读取日志')) : get(url))
  view()
  fireEvent.click(screen.getByRole('tab', { name: '操作记录' }))
  const download = await screen.findByRole('button', { name: `下载 ${sessionLogs[0].filename}` })
  fireEvent.click(download)
  expect(await screen.findByText(`${sessionLogs[0].filename}：没有权限读取日志`)).toBeInTheDocument()
  expect(download).not.toBeDisabled()
  expect(screen.queryByRole('link', { name: sessionLogs[0].filename })).not.toBeInTheDocument()
  expect(api.post).not.toHaveBeenCalled()
})
