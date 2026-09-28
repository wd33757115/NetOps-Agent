import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Grants from './Grants'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: [{ id: 'scope', owner_id: 'alice', domain: 'lab', cidr: '10.0.0.0/24', action: 'device.collect', enabled: true, revision: 3 }] })
  mocks.patch.mockResolvedValue({ data: {} })
  mocks.post.mockResolvedValue({ data: { added: 1, skipped: 0 } })
})

test('revocation sends the displayed revision and refreshes', async () => {
  render(<Grants />)
  fireEvent.click(await screen.findByRole('button', { name: /撤\s*销/ }))
  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith('/apps/device-access/grants/scope', { revision: 3, enabled: false }))
  await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2))
})

test('file selection waits for explicit import and submits legacy JSON', async () => {
  render(<Grants />)
  const legacy = { alice: [{ domain: 'lab', cidrs: ['10.0.0.0/24'], actions: ['device.collect'] }] }
  const file = new File([JSON.stringify(legacy)], 'grants.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', { value: async () => JSON.stringify(legacy) })
  fireEvent.change(screen.getByLabelText('旧授权 JSON 文件'), { target: { files: [file] } })
  expect(mocks.post).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '导入所选文件' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/apps/device-access/grants/import', { grants: legacy }))
})

test('invalid file never sends an import request', async () => {
  render(<Grants />)
  const file = new File(['invalid'], 'grants.json')
  Object.defineProperty(file, 'text', { value: async () => 'invalid' })
  fireEvent.change(screen.getByLabelText('旧授权 JSON 文件'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: '导入所选文件' }))
  await screen.findByText('导入失败，请检查文件格式、CIDR 和动作；最多 1000 条')
  expect(mocks.post).not.toHaveBeenCalled()
})
