import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { parserApi } from '@netops/services/api'
import PublishedAssetsWorkspace from '../../../../src/apps/rule_governance/frontend/components/skills/parser/PublishedAssetsWorkspace'

const template = (id: string) => ({ id, vendor: 'H3C', profile: 'test', canonical_command: `display ${id}`,
  category: 'test', version: '1', status: 'active', created_at: '2026-09-23T12:00:00Z' })
const mount = () => render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
  <PublishedAssetsWorkspace />
</QueryClientProvider></MemoryRouter>)

describe('published asset actions', () => {
  beforeEach(() => {
    vi.spyOn(parserApi, 'templates').mockImplementation(async (params) => ({
      items: [template(params?.offset ? 'second' : 'first')], total: 26, limit: params?.limit || 25, offset: params?.offset || 0,
    }) as never)
    vi.spyOn(parserApi, 'changePolicies').mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
    vi.spyOn(parserApi, 'eventRules').mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
  })
  afterEach(() => vi.clearAllMocks())

  it('keeps cross-page selection and retains failed IDs for retry', async () => {
    const action = vi.spyOn(parserApi, 'publishedAssetAction').mockResolvedValue({ succeeded: 1, failed: 1,
      results: [{ id: 'first', success: true }, { id: 'second', success: false, error: '资产正在变更' }] })
    mount()
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择 display first' }))
    fireEvent.click(screen.getByTitle('2'))
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择 display second' }))
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(await screen.findByText('将删除 2 项Parser 模板')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(action).toHaveBeenCalledWith({ kind: 'parser', action: 'delete', ids: ['first','second'], reason: '' }))
    expect(await screen.findByText('second：资产正在变更')).toBeInTheDocument()
    expect(screen.getByText('已选择 1 项（支持跨页，单次最多 100 项）')).toBeInTheDocument()
  })

  it('supports a single stop and refreshes counts and list', async () => {
    const action = vi.spyOn(parserApi, 'publishedAssetAction').mockResolvedValue({ succeeded: 1, failed: 0,
      results: [{ id:'first',success:true }] })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: '停用' }))
    fireEvent.change(screen.getByRole('textbox', { name: '操作备注' }), { target: { value: '替换旧规则' } })
    fireEvent.click(screen.getByRole('button', { name: '确认停用' }))
    await waitFor(() => expect(action).toHaveBeenCalledWith({ kind:'parser', action:'deprecate', ids:['first'], reason:'替换旧规则' }))
    await waitFor(() => expect(screen.getByText('已选择 0 项（支持跨页，单次最多 100 项）')).toBeInTheDocument())
  })

  it('loads disabled assets and clears selection on status change', async () => {
    mount()
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择 display first' }))
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '资产状态' }))
    fireEvent.click(await screen.findByText('已停用'))
    await waitFor(() => expect(parserApi.templates).toHaveBeenCalledWith(expect.objectContaining({ status:'deprecated' })))
    expect(screen.queryByRole('button', { name:'批量停用' })).not.toBeInTheDocument()
    expect(screen.getByText('已选择 0 项（支持跨页，单次最多 100 项）')).toBeInTheDocument()
  })
})
