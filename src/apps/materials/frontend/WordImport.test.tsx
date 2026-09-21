import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WordImport from './WordImport'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
const preview = { source_sha256: 'a'.repeat(64), normalized_sha256: 'b'.repeat(64),
  warnings: ['图片未提取'],
  sections: [
    { id: 'line-1', title_path: ['接口配置'], start_line: 1, text: '接口原文' },
    { id: 'line-5', title_path: ['路由配置'], start_line: 5, text: '路由原文' },
  ],
}
beforeEach(() => { vi.clearAllMocks(); mocks.post.mockResolvedValue({ data: preview }) })
afterEach(cleanup)
async function openPreview() {
  fireEvent.change(screen.getByLabelText('Word 文件'), { target: { files: [new File(['zip'], '手册.docx')] } })
  fireEvent.click(screen.getByRole('button', { name: '解析并预览' }))
  await screen.findByText('图片未提取')
}

it('previews locally before allowing whole-file, acknowledged publication', async () => {
  const refreshed = vi.fn().mockResolvedValue(undefined)
  render(<WordImport onPublished={refreshed} />)
  await openPreview()
  const publication = screen.getByRole('button', { name: '发布整个文件' })
  expect(publication).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '接口配置' }))
  expect(screen.getByText('接口原文')).toBeInTheDocument()
  expect(mocks.post).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByLabelText('已核对提取预览与缺失提示，确认发布整个文件'))
  fireEvent.click(publication)
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce())
  const [url, body, config] = mocks.post.mock.calls[1]
  expect(url).toBe('/apps/materials/documents/import')
  expect(body).toBeInstanceOf(FormData)
  expect(body.get('file').name).toBe('手册.docx')
  expect(JSON.parse(body.get('options'))).toMatchObject({
    source_sha256: preview.source_sha256, normalized_sha256: preview.normalized_sha256,
    acknowledged_limitations: true,
  })
  expect(config.headers['Content-Type']).toBeUndefined()
  expect(await screen.findByText('资料已发布，可在问答中按需检索。')).toBeInTheDocument()
})

it('invalidates the preview when the file changes', async () => {
  render(<WordImport onPublished={vi.fn()} />)
  await openPreview()
  fireEvent.change(screen.getByLabelText('Word 文件'), { target: { files: [new File(['new'], '新版.docx')] } })
  expect(screen.queryByText('图片未提取')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '发布整个文件' })).not.toBeInTheDocument()
  expect(mocks.post).toHaveBeenCalledTimes(1)
})

it('publishes a revision with the original version precondition', async () => {
  render(<WordImport target={{ id: 'd', title: '原手册', revision: 'old', metadata: { vendor: 'Huawei' } }} onPublished={vi.fn()} />)
  await openPreview()
  fireEvent.click(screen.getByLabelText('已核对提取预览与缺失提示，确认发布整个文件'))
  mocks.post.mockRejectedValueOnce(new Error('Conflict'))
  fireEvent.click(screen.getByRole('button', { name: '发布新修订' }))
  expect(await screen.findByText(/发布未完成/)).toBeInTheDocument()
  expect(JSON.parse(mocks.post.mock.calls[1][1].get('options'))).toMatchObject({
    title: '原手册', document_id: 'd', expected_revision: 'old', metadata: { vendor: 'Huawei' },
  })
})

it('search only filters the preview and never limits publication', async () => {
  const refreshed = vi.fn().mockResolvedValue(undefined)
  render(<WordImport onPublished={refreshed} />)
  await openPreview()
  fireEvent.change(screen.getByLabelText('搜索预览章节'), { target: { value: '接口' } })
  expect(screen.getByText('整份文件 · 2 个章节')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '路由配置' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('已核对提取预览与缺失提示，确认发布整个文件'))
  fireEvent.click(screen.getByRole('button', { name: '发布整个文件' }))
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce())
  expect(JSON.parse(mocks.post.mock.calls[1][1].get('options'))).not.toHaveProperty('section_ids')
})
