import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import SystemAppsPage from './SystemAppsPage'
import { systemApi, type AccessData } from '../components/system/api'

vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' }, refreshProfile: async () => {} }) }))
const data: AccessData = {
  items: [{ id: 'inspection', name: '网络巡检', state: 'enabled', revision: 'version-1', grants: [{ user_id: 10, group_id: null }, { user_id: null, group_id: 2 }] }],
  users: [{ id: 10, username: 'engineer', role: 'operator', is_active: true, group_ids: [2] }],
  groups: [{ id: 2, name: '核心网', protected: false }],
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })
beforeEach(() => {
  vi.spyOn(systemApi, 'directory').mockImplementation(async body => {
    if (body.access) return { items: [{ id: 10, name: 'engineer', is_active: true, sources: ['用户组：核心网'] }], total: 1 }
    const rows = body.kind === 'group' ? [{ id: 2, name: '核心网', member_count: 1 }] : [{ id: 10, name: 'engineer', is_active: true }]
    const items = rows.filter(r => body.ids?.includes(r.id) || (body.exclude_ids && !body.exclude_ids.includes(r.id)))
    return { items, total: items.length }
  })
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }))
})
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/system/apps']}><SystemAppsPage /></MemoryRouter></QueryClientProvider>)
}
async function removeDirectGrant() {
  await userEvent.click(await screen.findByRole('button', { name: '管理授权' }))
  await userEvent.click(await screen.findByRole('button', { name: '撤销 engineer' }))
}
it('previews remaining group access and saves the explicit revision', async () => {
  vi.spyOn(systemApi, 'access').mockResolvedValue(data)
  const save = vi.spyOn(systemApi, 'saveAccess').mockResolvedValue({})
  mount(); await removeDirectGrant()
  expect(screen.getByText('用户组：核心网')).toBeVisible()
  expect(screen.getByText('可使用')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '保存授权' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('inspection', [], [2], 'version-1'))
})
it('preserves unsaved changes on conflict instead of silently overwriting', async () => {
  vi.spyOn(systemApi, 'access').mockResolvedValue(data)
  vi.spyOn(systemApi, 'saveAccess').mockRejectedValue(new Error('应用授权已发生变化'))
  mount(); await removeDirectGrant()
  await userEvent.click(screen.getByRole('button', { name: '保存授权' }))
  expect(await screen.findByText('应用授权已发生变化')).toBeVisible()
  expect(screen.getByText('有未保存的修改')).toBeVisible()
  expect(screen.getByText('用户组：核心网')).toBeVisible()
})
it('offers retry when loading fails, without presenting an empty application list', async () => {
  const load = vi.spyOn(systemApi, 'access').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(data)
  mount()
  expect(await screen.findByText('应用授权加载失败')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
  expect(await screen.findByRole('button', { name: '管理授权' })).toBeVisible()
  expect(load).toHaveBeenCalledTimes(2)
})
