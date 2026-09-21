import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import EvidenceLinks from './EvidenceLinks'
import { api } from '../config/api'
vi.mock('../config/api', () => ({ api: { get: vi.fn() } }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const reference = { id: 'evidence-123', kind: 'business', title_path: ['业务证据', '查询设备'], revision: 'abc', excerpt: '' }

it('fetches the authorized snapshot only when a user opens its evidence', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: { ...reference, excerpt: '{"count":0}', retrieved_at: '2026-09-21' } })
  render(<EvidenceLinks conversationId="chat-1" references={[reference]} />)
  expect(api.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '业务证据 1 · 查询设备' }))
  expect(await screen.findByText('{"count":0}')).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith('/apps/assistant/conversations/chat-1/citations/evidence-123')
  expect(screen.getByText(/不代表实时状态/)).toBeInTheDocument()
})

it('shows a permission failure without falling back to cached raw evidence', async () => {
  vi.mocked(api.get).mockRejectedValue(new Error('Forbidden'))
  render(<EvidenceLinks conversationId="chat-1" references={[{ ...reference, excerpt: 'private snapshot' }]} />)
  fireEvent.click(screen.getByRole('button', { name: '业务证据 1 · 查询设备' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('无法打开证据')
  expect(screen.queryByText('private snapshot')).not.toBeInTheDocument()
})

it('shows Word source limitations and downloads through authenticated revision routes', async () => {
  const citation = { id: 'doc-1', document_id: 'd', title_path: ['接口'], revision: 'r', excerpt: '原文',
    source: { filename: '手册.docx', warnings: ['图片未提取'], locations: [{ kind: 'table_row', table: 2, row: 3 }], locations_truncated: false } }
  vi.mocked(api.get).mockResolvedValueOnce({ data: citation }).mockResolvedValueOnce({ data: new Blob(['word']) })
  const create = vi.fn().mockReturnValue('blob:download')
  vi.stubGlobal('URL', { ...URL, createObjectURL: create, revokeObjectURL: vi.fn() })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  try {
    render(<EvidenceLinks conversationId="c" references={[citation]} />)
    fireEvent.click(screen.getByRole('button', { name: '原文 1 · 接口' }))
    expect(await screen.findByText('图片未提取')).toBeInTheDocument()
    expect(screen.getByText('表 2 第 3 行')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下载完整 Word 原文件核对' }))
    await waitFor(() => expect(click).toHaveBeenCalledOnce())
    expect(api.get).toHaveBeenLastCalledWith('/apps/assistant/documents/d/revisions/r/source', { responseType: 'blob' })
    expect(create).toHaveBeenCalledOnce()
  } finally { click.mockRestore(); vi.unstubAllGlobals() }
})

it('renders older conversations whose references are null', () => {
  const { container } = render(<EvidenceLinks conversationId="legacy" references={null} />)
  expect(container).toBeEmptyDOMElement()
  expect(api.get).not.toHaveBeenCalled()
})
