import React, { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import PrincipalEditor from './PrincipalEditor'
import { systemApi } from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }))
  vi.spyOn(systemApi, 'directory').mockImplementation(async body => {
    const all = Array.from({ length: 105 }, (_, i) => ({ id: i + 1, name: `person-${String(i + 1).padStart(3, '0')}`, is_active: true }))
    const rows = all.filter(r => (!body.ids || body.ids.includes(r.id)) && (!body.exclude_ids || !body.exclude_ids.includes(r.id)) && r.name.includes(body.search || ''))
    const offset = ((body.page || 1) - 1) * 10
    return { total: rows.length, items: rows.slice(offset, offset + 10) }
  })
})
function Harness({ initial = [1, 2] }: { initial?: number[] }) {
  const [ids, setIds] = useState(initial)
  return <><output aria-label="selected ids">{JSON.stringify(ids)}</output><PrincipalEditor title="已加入成员" value={ids} onChange={setIds} /></>
}
function mount(initial?: number[]) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })}><Harness initial={initial} /></QueryClientProvider>)
}
it('shows existing members and removes one without a token dropdown', async () => {
  mount()
  expect(await screen.findByText('person-001')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '移出 person-001' }))
  await waitFor(() => expect(screen.getByLabelText('selected ids')).toHaveTextContent('[2]'))
  expect(await screen.findByText('person-002')).toBeVisible()
})
it('searches and pages candidates on the server, preserves selection and returns to members', async () => {
  mount()
  await userEvent.click(screen.getByRole('button', { name: /添加用户/ }))
  await screen.findByText('person-003')
  await userEvent.click(screen.getByLabelText('选择 person-003'))
  await userEvent.click(screen.getByTitle('2'))
  await screen.findByText('person-013')
  await userEvent.click(screen.getByLabelText('选择 person-013'))
  await userEvent.click(screen.getByRole('button', { name: /添加所选/ }))
  expect(screen.getByLabelText('selected ids')).toHaveTextContent('[1,2,3,13]')
  await userEvent.type(screen.getByLabelText('搜索已加入成员'), '105')
  expect(await screen.findByText('person-105')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '添加 person-105' }))
  await userEvent.click(screen.getByRole('button', { name: /返回已选列表/ }))
  expect(await screen.findByText('person-105')).toBeVisible()
  expect(systemApi.directory).toHaveBeenCalledWith(expect.objectContaining({ page: 2, page_size: 10 }))
})
it('recovers a failed member request without clearing the draft selection', async () => {
  vi.mocked(systemApi.directory).mockRejectedValueOnce(new Error('网络中断'))
  mount()
  expect(await screen.findByText('网络中断')).toBeVisible()
  expect(screen.getByLabelText('selected ids')).toHaveTextContent('[1,2]')
  await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
  expect(await screen.findByText('person-001')).toBeVisible()
})
