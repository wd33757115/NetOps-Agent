import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CredentialSets from './CredentialSets'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }))
const actor = vi.hoisted(() => ({ role: 'operator' }))
vi.mock('@netops/config/api', () => ({ api }))
vi.mock('@netops/context/AuthContext', () => ({ useAuth: () => ({ user: actor }) }))
const item = { id: 'a', name: '实验网', username: 'operator', domain: 'lab', scope_kind: 'domain', scope_value: 'lab', priority: 1, disabled: false, has_enable_secret: true, rotated_at: '2026-09-28T00:00:00Z' }

beforeEach(() => {
  vi.clearAllMocks(); actor.role = 'operator'
  api.get.mockResolvedValue({ data: [item] }); api.post.mockResolvedValue({ data: item }); api.patch.mockResolvedValue({ data: item })
})
afterEach(cleanup)

it('shows metadata and disables a set without requesting secrets', async () => {
  render(<CredentialSets />)
  expect(await screen.findByText('实验网')).toBeInTheDocument()
  expect(screen.getByText('已设置')).toBeInTheDocument()
  expect(screen.queryByLabelText('密码')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /禁\s*用/ }))
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/apps/device-access/credential-sets/a', { disabled: true }))
  expect(api.get).toHaveBeenCalledWith('/apps/device-access/credential-sets')
})

it('creates a domain set and clears passwords on success', async () => {
  render(<CredentialSets />)
  fireEvent.click(screen.getByRole('button', { name: '新建凭据集' }))
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新的凭据' } })
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'test-user' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'write-only-test-secret' } })
  fireEvent.click(screen.getByRole('button', { name: '加密保存' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/credential-sets', expect.objectContaining({ domain: 'default', scope_value: 'default', password: 'write-only-test-secret' })))
  await waitFor(() => expect(screen.queryByDisplayValue('write-only-test-secret')).not.toBeInTheDocument())
})

it('does not retain failed submission passwords for a retry', async () => {
  api.post.mockRejectedValue(new Error('denied'))
  render(<CredentialSets />)
  fireEvent.click(screen.getByRole('button', { name: '新建凭据集' }))
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新的凭据' } })
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'test-user' } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'forgotten-test-secret' } })
  fireEvent.click(screen.getByRole('button', { name: '加密保存' }))
  await waitFor(() => expect(api.post).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByLabelText('密码')).toHaveValue(''))
})

it('rotates without reading an old password and can clear the enable secret', async () => {
  render(<CredentialSets />)
  fireEvent.click(await screen.findByRole('button', { name: '轮换密码' }))
  expect(screen.getByLabelText('新密码')).toHaveValue('')
  fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'replacement-test-secret' } })
  fireEvent.click(screen.getByLabelText('清除原特权密码'))
  fireEvent.click(screen.getByRole('button', { name: '保存新密码' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/credential-sets/a/rotate', { password: 'replacement-test-secret', enable_secret: null }))
  await waitFor(() => expect(screen.queryByDisplayValue('replacement-test-secret')).not.toBeInTheDocument())
})

it('viewer sees metadata but no credential maintenance controls', async () => {
  actor.role = 'viewer'
  render(<CredentialSets />)
  expect(await screen.findByText('实验网')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '新建凭据集' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '轮换密码' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /禁\s*用/ })).not.toBeInTheDocument()
})
