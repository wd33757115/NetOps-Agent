import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BatchImport from './BatchImport'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
const preview = { source_sha256: 'a'.repeat(64), normalized_sha256: 'b'.repeat(64), warnings: ['图片未提取'], sections: [{ id: 'line-1' }, { id: 'line-5' }] }
beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

it('keeps successful publications when another file fails and only retries remaining files', async () => {
  let fail = true
  mocks.post.mockImplementation(async (url: string, body: FormData) => {
    if (url.endsWith('/preview')) return { data: preview }
    if ((body.get('file') as File).name === '二.docx' && fail) throw { response: { data: { detail: '文件已存在：二' } } }
    return { data: {} }
  })
  const changed = vi.fn().mockResolvedValue(undefined)
  render(<BatchImport onPublished={changed} />)
  fireEvent.change(screen.getByLabelText('批量 Word 文件'), { target: { files: [new File(['one'], '一.docx'), new File(['two'], '二.docx')] } })
  fireEvent.click(screen.getByRole('button', { name: '解析待处理文件' }))
  await waitFor(() => expect(screen.getAllByText('待发布')).toHaveLength(2))
  fireEvent.click(screen.getByLabelText('已核对本批文件的提取提示，确认逐个发布整个文件'))
  fireEvent.click(screen.getByRole('button', { name: '发布待处理文件（2）' }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  expect(screen.getByText('文件已存在：二')).toBeInTheDocument()
  expect(screen.getByText('已发布')).toBeInTheDocument()
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '发布待处理文件（1）' }))
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(2))
  const publications = mocks.post.mock.calls.filter(call => call[0].endsWith('/import'))
  expect(publications.map(call => (call[1].get('file') as File).name)).toEqual(['一.docx', '二.docx', '二.docx'])
  expect(JSON.parse(publications[0][1].get('options'))).not.toHaveProperty('section_ids')
})

it('rejects unsupported batches without any upload', () => {
  render(<BatchImport onPublished={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('批量 Word 文件'), { target: { files: [new File(['pdf'], 'wrong.pdf')] } })
  expect(screen.getByText(/最多 10 个 .docx 文件/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '解析待处理文件' })).toBeDisabled()
  expect(mocks.post).not.toHaveBeenCalled()
})
