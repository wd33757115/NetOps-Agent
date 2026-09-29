import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import SystemDeviceScopesPage from './SystemDeviceScopesPage'
import { systemApi } from '../components/system/api'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' }, refreshProfile: async () => {} }) }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })
beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: [{ id: 'scope', owner_id: '10', domain: 'lab', cidr: '10.0.0.0/24', action: 'device.collect', enabled: true, revision: 3 }] })
  mocks.patch.mockResolvedValue({ data: {} })
  mocks.post.mockResolvedValue({ data: { added: 1, skipped: 0 } })
  vi.spyOn(systemApi, 'directory').mockResolvedValue({ items: [{ id: 10, name: 'engineer', is_active: true }], total: 1 })
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }))
})
const mount = () => render(<MemoryRouter initialEntries={['/system/device-scopes']}><SystemDeviceScopesPage /></MemoryRouter>)

test('lives in system administration and names accounts instead of bare ids', async () => {
  mount()
  expect(screen.getByRole('link', { name: /设备访问范围/ })).toHaveAttribute('aria-current', 'page')
  expect(await screen.findByText('engineer（ID 10）')).toBeVisible()
  // The directory accepts at most 100 per page; listed owners are resolved by id.
  await waitFor(() => expect(systemApi.directory).toHaveBeenCalledWith({ kind: 'user', ids: [10], page_size: 100 }))
  expect(vi.mocked(systemApi.directory).mock.calls.every(([body]) => (body.page_size ?? 10) <= 100)).toBe(true)
})

test('revocation sends the displayed revision and refreshes', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /撤\s*销/ }))
  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith('/apps/device-access/grants/scope', { revision: 3, enabled: false }))
  await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2))
})

test('a new scope is saved for the chosen account id', async () => {
  mount()
  await screen.findByText('engineer（ID 10）')
  await userEvent.click(screen.getByRole('button', { name: /新增范围/ }))
  await userEvent.click(screen.getByRole('combobox', { name: '账号' }))
  await userEvent.click(await screen.findByTitle('engineer（ID 10）'))
  const domain = screen.getByRole('textbox', { name: '管理域' })
  await userEvent.clear(domain); await userEvent.type(domain, 'lab')
  await userEvent.type(screen.getByRole('textbox', { name: 'CIDR 网段或单个 IP' }), '10.10.1.0/24')
  await userEvent.click(screen.getByRole('button', { name: /保存授权/ }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/apps/device-access/grants', { owner_id: '10', domain: 'lab', cidr: '10.10.1.0/24', action: 'device.collect' }))
})

test('file selection waits for explicit import and submits legacy JSON', async () => {
  mount()
  const legacy = { alice: [{ domain: 'lab', cidrs: ['10.0.0.0/24'], actions: ['device.collect'] }] }
  const file = new File([JSON.stringify(legacy)], 'grants.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', { value: async () => JSON.stringify(legacy) })
  fireEvent.change(screen.getByLabelText('旧授权 JSON 文件'), { target: { files: [file] } })
  expect(mocks.post).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '导入所选文件' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/apps/device-access/grants/import', { grants: legacy }))
})
