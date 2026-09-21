import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
const reference = { id: 'doc-test', title_path: ['接口检查'], revision: 'a'.repeat(64), excerpt: '原修订正文', start_line: 1, end_line: 2 }
let state: { messages: { id: string; role: string; content: string; references: typeof reference[] }[]; latest_turn: { id: string; status: string } | null }
beforeEach(() => {
  vi.clearAllMocks()
  state = { messages: [], latest_turn: null }
  mocks.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/documents')) return { data: [{ id: 'manual', title: '手册', revision: 'a'.repeat(64), sections: 2, metadata: { software_version: '8.2.1' } }] }
    if (url.endsWith('/conversations')) return { data: [{ id: 'chat', title: '新问答' }] }
    if (url.includes('/citations/')) return { data: reference }
    return { data: state }
  })
  mocks.post.mockResolvedValue({ data: { id: 'chat', title: '新问答' } })
  mocks.put.mockResolvedValue({ data: {} })
})
afterEach(cleanup)

it('keeps the request identifier when a question is retried', async () => {
  render(<Page />)
  fireEvent.click(screen.getByRole('button', { name: '新建会话' }))
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('/apps/assistant/conversations/chat'))
  fireEvent.change(screen.getByLabelText('问题'), { target: { value: '如何检查接口' } })
  mocks.post.mockRejectedValue(new Error('connection lost'))
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText(/问答请求未完成/)
  const first = mocks.post.mock.calls.at(-1)?.[1]
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(3))
  expect(mocks.post.mock.calls.at(-1)?.[1].request_id).toBe(first.request_id)
})

it('opens the cited snapshot and requests durable turn cancellation', async () => {
  state = { messages: [{ id: 'm', role: 'assistant', content: '回答', references: [reference] }], latest_turn: { id: 'turn', status: 'running' } }
  render(<Page />)
  fireEvent.click(screen.getByRole('button', { name: '新建会话' }))
  fireEvent.click(await screen.findByRole('button', { name: '原文 1 · 接口检查' }))
  expect(await screen.findByText('原修订正文')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取消本次问答' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/apps/assistant/turns/turn/cancel'))
})

it('does not expose source management in the chat app', async () => {
  render(<Page />)
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('/apps/assistant/conversations'))
  expect(screen.queryByRole('tab', { name: '共享资料' })).not.toBeInTheDocument()
  expect(mocks.get).not.toHaveBeenCalledWith('/apps/assistant/documents')
})
