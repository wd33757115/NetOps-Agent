import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'
const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
const document = { id: 'doc', title: '接口手册', revision: 'a'.repeat(64), sections: 1, status: 'active', metadata: { vendor: 'Huawei' }, has_original: false }
beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/documents')) return { data: [document] }
    if (url.endsWith('/revisions')) return { data: { items: [{ ...document, created_at: '2026-09-21' }], next_offset: null } }
    if (url.endsWith('/sections')) return { data: { items: [{ section_id: 'line-42', title_path: ['接口说明'] }], next_offset: null } }
    return { data: { citation: { excerpt: '第42行原文' } } }
  })
  mocks.put.mockResolvedValue({ data: {} })
})
afterEach(cleanup)

it('browses the exact revision and actual section identifier', async () => {
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '查看修订' }))
  fireEvent.click(await screen.findByRole('button', { name: '接口说明' }))
  expect(await screen.findByText('第42行原文')).toBeInTheDocument()
  expect(mocks.get).toHaveBeenCalledWith('/apps/materials/documents/doc/sections/line-42',
    { params: { revision: document.revision, limit: 4000 } })
})

it('updates text through the management app with stale-write protection', async () => {
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '更新修订' }))
  fireEvent.click(screen.getByRole('tab', { name: '文本资料' }))
  fireEvent.change(screen.getByLabelText('资料正文'), { target: { value: '# 新正文\n接口内容' } })
  fireEvent.click(screen.getByRole('button', { name: '保存资料' }))
  await waitFor(() => expect(mocks.put).toHaveBeenCalledWith('/apps/materials/documents/doc',
    expect.objectContaining({ expected_revision: document.revision, text: '# 新正文\n接口内容' })))
})

it('reads subsequent excerpts from the selected immutable revision', async () => {
  mocks.get.mockImplementation(async (url: string, config?: { params?: { offset?: number } }) => {
    if (url.endsWith('/documents')) return { data: [document] }
    if (url.endsWith('/revisions')) return { data: { items: [{ ...document, created_at: '2026-09-21' }], next_offset: null } }
    if (url.endsWith('/sections')) return { data: { items: [{ section_id: 'line-42', title_path: ['接口说明'] }], next_offset: null } }
    return { data: { citation: { excerpt: config?.params?.offset ? '后续正文' : '首段正文' }, next_offset: config?.params?.offset ? null : 4000 } }
  })
  render(<Page />)
  fireEvent.click(await screen.findByRole('button', { name: '查看修订' }))
  fireEvent.click(await screen.findByRole('button', { name: '接口说明' }))
  expect(await screen.findByText('首段正文')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '下一段' }))
  expect(await screen.findByText('后续正文')).toBeInTheDocument()
  expect(mocks.get).toHaveBeenLastCalledWith('/apps/materials/documents/doc/sections/line-42',
    { params: { revision: document.revision, offset: 4000, limit: 4000 } })
  expect(screen.getByRole('button', { name: '下一段' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '上一段' }))
  expect(await screen.findByText('首段正文')).toBeInTheDocument()
})

it('distinguishes an empty filtered view from an empty library', async () => {
  render(<Page />)
  await screen.findByRole('button', { name: '查看修订' })
  fireEvent.click(screen.getByRole('button', { name: /含 Word 原件/ }))
  expect(await screen.findByText('没有匹配的资料，试试其他关键词或分类')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /全部资料/ }))
  expect(await screen.findByRole('button', { name: '查看修订' })).toBeInTheDocument()
})
