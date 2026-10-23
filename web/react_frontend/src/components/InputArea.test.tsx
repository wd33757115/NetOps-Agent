import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import InputArea from './InputArea'
import ChatMessage from './ChatMessage'
import { useChatStore } from '../store/useChatStore'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('../config/api', () => ({ api: mocks }))
vi.mock('./ModelSettings', () => ({ default: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  useChatStore.setState({ currentConversationId: 'c' })
})
afterEach(cleanup)

const pick = (files: File[]) => fireEvent.change(document.querySelector('input[type=file]')!, { target: { files } })

it('uploads attachments to the netdisk and sends their ids with the question', async () => {
  mocks.post.mockResolvedValue({ data: { file_id: 'file-1', name: 'REQ-1.xlsx', size: 2048, kind: 'file' } })
  const onSend = vi.fn()
  render(<InputArea onSend={onSend} loading={false} />)
  const sheet = new File(['x'.repeat(2048)], 'REQ-1.xlsx')
  pick([sheet])

  expect(screen.getByText('上传中')).toBeInTheDocument()
  expect(await screen.findByText('2.0 KB')).toBeInTheDocument()
  expect(mocks.post).toHaveBeenCalledWith('/apps/assistant/attachments', sheet, expect.objectContaining({
    params: { filename: 'REQ-1.xlsx' }, headers: { 'Content-Type': 'application/octet-stream' } }))

  fireEvent.change(screen.getByPlaceholderText('有什么想问的？'), { target: { value: '按工单 REQ-1 生成防火墙策略' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(onSend).toHaveBeenCalledWith('按工单 REQ-1 生成防火墙策略', [{ file_id: 'file-1', name: 'REQ-1.xlsx', size: 2048, kind: 'file' }])
  expect(screen.queryByText('REQ-1.xlsx')).not.toBeInTheDocument()
})

it('keeps a failed upload visible with its reason and leaves it out of the question', async () => {
  mocks.post.mockRejectedValue(new Error('不支持这种文件'))
  const onSend = vi.fn()
  render(<InputArea onSend={onSend} loading={false} />)
  pick([new File(['x'], 'tool.bin')])
  expect(await screen.findByText('不支持这种文件')).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('有什么想问的？'), { target: { value: '看一下' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(onSend).toHaveBeenCalledWith('看一下', [])
})

it('waits for uploads before sending and removes an attachment on request', async () => {
  let finish!: (value: unknown) => void
  mocks.post.mockReturnValue(new Promise(resolve => { finish = resolve }))
  render(<InputArea onSend={vi.fn()} loading={false} />)
  pick([new File(['a'], 'R1.cfg')])
  fireEvent.change(screen.getByPlaceholderText('有什么想问的？'), { target: { value: '对比' } })
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  finish({ data: { file_id: 'file-2', name: 'R1.cfg', size: 1, kind: 'text' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '移除 R1.cfg' }))
  expect(screen.queryByText('R1.cfg')).not.toBeInTheDocument()
})

it('shows the files a question was sent with', () => {
  render(<ChatMessage role="user" content="生成策略" attachments={[{ file_id: 'file-1', name: 'REQ-1.xlsx' }]} />)
  expect(screen.getByRole('list', { name: '附件' })).toHaveTextContent('REQ-1.xlsx')
})
