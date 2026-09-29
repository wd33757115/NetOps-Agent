import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HostKeyModal, LiveTerminal, LoginModal } from './DeviceTerminal'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../../config/api', () => ({ api }))
vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({ loadAddon: vi.fn(), open: vi.fn(), onData: () => ({ dispose: vi.fn() }), write: vi.fn(), focus: vi.fn(), dispose: vi.fn(), cols: 80, rows: 24 })),
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: vi.fn().mockImplementation(() => ({ fit: vi.fn() })) }))

class Socket {
  static last: Socket
  static OPEN = 1
  readyState = 1
  sent: string[] = []
  onopen?: () => void
  onmessage?: (event: { data: string }) => void
  onerror?: () => void
  onclose?: () => void
  constructor() { Socket.last = this }
  send(value: string) { this.sent.push(value) }
  close() {}
  emit(payload: object) { act(() => this.onmessage?.({ data: JSON.stringify(payload) })) }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('WebSocket', Socket)
  api.post.mockResolvedValue({ data: { ticket: 'one-use', session_id: 'op' } })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const ticketBodies = () => api.post.mock.calls.filter(([url]) => url === '/apps/device-access/terminal-tickets').map(([, body]) => body)

it('opens a saved session with a one-use ticket sent in the first frame', async () => {
  render(<LiveTerminal target={{ session_id: 's1' }} />)
  await waitFor(() => expect(ticketBodies()).toHaveLength(1))
  expect(ticketBodies()[0]).toEqual({ request_id: expect.any(String), session_id: 's1' })
  act(() => Socket.last.onopen?.())
  expect(JSON.parse(Socket.last.sent[0])).toEqual({ ticket: 'one-use' })
  Socket.last.emit({ type: 'ready' })
  expect(await screen.findByText(/已连接/)).toBeInTheDocument()
})

it('asks for a login after a failed one and retries with it, optionally saved', async () => {
  render(<LiveTerminal target={{ session_id: 's1' }} />)
  await waitFor(() => expect(ticketBodies()).toHaveLength(1))
  Socket.last.emit({ type: 'closed', reason: 'authentication_failed', host: '10.0.0.1', port: 22 })
  expect(await screen.findByText('登录失败，请重新输入')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'ops' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'typed-test-secret' } })
  fireEvent.click(screen.getByLabelText('保存到会话'))
  fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
  await waitFor(() => expect(ticketBodies()).toHaveLength(2))
  expect(ticketBodies()[1]).toEqual({ request_id: expect.any(String), session_id: 's1', login: { username: 'ops', password: 'typed-test-secret' }, save: true })
  expect(screen.queryByDisplayValue('typed-test-secret')).not.toBeInTheDocument()
})

it('a device address uses the default login and cannot save to a session', async () => {
  render(<LiveTerminal target={{ host: '10.0.0.9', port: 22 }} />)
  await waitFor(() => expect(ticketBodies()).toHaveLength(1))
  expect(ticketBodies()[0]).toEqual({ request_id: expect.any(String), host: '10.0.0.9', port: 22 })
  Socket.last.emit({ type: 'closed', reason: 'credentials_required', host: '10.0.0.9', port: 22 })
  expect(await screen.findByText('输入用户名和密码')).toBeInTheDocument()
  expect(screen.queryByLabelText('保存到会话')).not.toBeInTheDocument()
})

it('shows a first-seen fingerprint and reconnects after the user trusts it', async () => {
  const key = { host: '10.0.0.1', port: 22, status: 'pending', key_type: 'ssh-ed25519', fingerprint: 'SHA256:first' }
  api.get.mockResolvedValue({ data: key })
  render(<LiveTerminal target={{ session_id: 's1' }} />)
  await waitFor(() => expect(ticketBodies()).toHaveLength(1))
  Socket.last.emit({ type: 'closed', reason: 'host_key_unknown', host: '10.0.0.1', port: 22 })
  expect(await screen.findByText('SHA256:first')).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith('/apps/device-access/host-keys/10.0.0.1/22')
  fireEvent.click(screen.getByRole('button', { name: '信任并连接' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/host-keys/accept', { host: '10.0.0.1', port: 22, fingerprint: 'SHA256:first' }))
  await waitFor(() => expect(ticketBodies()).toHaveLength(2))
})

it('warns about a changed key and shows both fingerprints', () => {
  const accept = vi.fn()
  render(<HostKeyModal hostKey={{ host: '10.0.0.1', port: 22, status: 'trusted', key_type: 'ssh-rsa', fingerprint: 'SHA256:old', observed_key_type: 'ssh-rsa', observed_fingerprint: 'SHA256:new' }} onAccept={accept} onCancel={vi.fn()} />)
  expect(screen.getByText('主机密钥已变化')).toBeInTheDocument()
  expect(screen.getByText('SHA256:old')).toBeInTheDocument()
  expect(screen.getByText('SHA256:new')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '替换并连接' }))
  expect(accept).toHaveBeenCalled()
})

it('the login prompt requires both fields', async () => {
  const submit = vi.fn()
  render(<LoginModal open title="输入用户名和密码" onSubmit={submit} onCancel={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'ops' } })
  fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
  expect(await screen.findByText('请输入密码')).toBeInTheDocument()
  expect(submit).not.toHaveBeenCalled()
})
