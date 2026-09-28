import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import HostKeys from './HostKeys'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api }))
const row = { domain: 'lab', host: '10.0.0.1', port: 22, revision: 3, key_type: 'ssh-rsa', public_key: 'TEST_PUBLIC_KEY', fingerprint_sha256: 'SHA256:old', observed_fingerprint: 'SHA256:changed', status: 'pinned' }
beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue({ data: { keys: [row], policies: [{ domain: 'lab', policy: 'tofu_pinned' }] } })
  api.post.mockResolvedValue({ data: {} }); api.put.mockResolvedValue({ data: {} })
})
afterEach(cleanup)

it('shows a changed-key alert and requires independent fingerprint confirmation', async () => {
  render(<HostKeys />)
  expect(await screen.findByText(/发现主机密钥变化/)).toBeInTheDocument()
  expect(screen.getByText('变化后：SHA256:changed')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '核验或更新' }))
  expect(screen.getByLabelText('独立核验的 SHA256 指纹')).toHaveValue('')
  fireEvent.click(screen.getByRole('button', { name: '确认已核验并保存' }))
  await waitFor(() => expect(screen.getByText('请输入独立核验的指纹')).toBeInTheDocument())
  expect(api.post).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('独立核验的 SHA256 指纹'), { target: { value: 'SHA256:confirmed' } })
  fireEvent.click(screen.getByRole('button', { name: '确认已核验并保存' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/host-keys/verify', {
    domain: 'lab', host: '10.0.0.1', port: 22, revision: 3, key_type: 'ssh-rsa',
    public_key: 'TEST_PUBLIC_KEY', confirm_fingerprint: 'SHA256:confirmed',
  }))
})

it('revokes the displayed revision and configures strict policy explicitly', async () => {
  render(<HostKeys />)
  fireEvent.click(await screen.findByRole('button', { name: '撤销信任' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/apps/device-access/host-keys/revoke', { domain: 'lab', host: '10.0.0.1', port: 22, revision: 3 }))
  fireEvent.change(screen.getByLabelText('管理域'), { target: { value: 'production' } })
  fireEvent.click(screen.getByRole('button', { name: '保存域策略' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/device-access/host-keys/policy', { domain: 'production', policy: 'strict' }))
})
