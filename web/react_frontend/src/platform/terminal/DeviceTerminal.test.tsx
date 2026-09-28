import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionForm } from './DeviceTerminal'

const api = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('../../config/api', () => ({ api }))
vi.mock('@xterm/xterm', () => ({ Terminal: vi.fn() }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); api.post.mockResolvedValue({ data: { connection_id: 'new' } }) })
afterEach(cleanup)

it('switches to the vault without submitting an earlier typed password', async () => {
  const created = vi.fn()
  render(<ConnectionForm address="10.0.0.1" domain="lab" onCreated={created} />)
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'temporary-user' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'discarded-password' } })
  fireEvent.mouseDown(screen.getByLabelText('凭据来源'))
  fireEvent.click(screen.getByText('按设备作用域选择凭据集'))
  expect(screen.queryByLabelText('密码')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '创建连接授权' }))
  await waitFor(() => expect(created).toHaveBeenCalled())
  expect(api.post.mock.calls[0][1]).toEqual({
    address: '10.0.0.1', domain: 'lab', port: 22, profiles: ['comware-lldp-v1'],
    allow_terminal: false, credential_mode: 'vault',
  })
  fireEvent.click(screen.getByRole('button', { name: '创建连接授权' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
  expect(api.post.mock.calls[1][1]).not.toHaveProperty('password')
})

it('keeps the original manual flow and clears the password after failure', async () => {
  api.post.mockRejectedValue(new Error('denied'))
  render(<ConnectionForm address="10.0.0.1" onCreated={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'operator' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'temporary-password' } })
  fireEvent.click(screen.getByRole('button', { name: '创建连接授权' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/connections', expect.objectContaining({ credential_mode: 'temporary', username: 'operator', password: 'temporary-password' })))
  await waitFor(() => expect(screen.getByLabelText('密码')).toHaveValue(''))
})

it('requires an explicit opt-in for controlled diagnostic sessions', async () => {
  render(<ConnectionForm address="10.0.0.1" onCreated={vi.fn()} />)
  expect(screen.getByLabelText('允许受控诊断会话')).not.toBeChecked()
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'operator' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'temporary-password' } })
  fireEvent.click(screen.getByLabelText('允许受控诊断会话'))
  fireEvent.click(screen.getByRole('button', { name: '创建连接授权' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/connections', expect.objectContaining({ allow_session: true })))
})
