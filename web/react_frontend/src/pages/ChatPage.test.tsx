import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ChatPage from './ChatPage'
import { useChatStore } from '../store/useChatStore'
import type { AgentProgress } from '../components/TraceProgress'
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), stream: vi.fn() }))
vi.mock('../config/api', () => ({ api: mocks }))
vi.mock('../services/chatStream', () => ({ streamChatMessage: mocks.stream }))
vi.mock('../components/layout/GrokShellLayout', () => ({ default: ({ children, footer }: any) => <div>{children}{footer}</div> }))
vi.mock('../components/InputArea', () => ({ default: ({ onSend, loading }: any) => <button disabled={loading} onClick={() => onSend('查找 BGP EVPN')}>发送</button> }))
const progress: AgentProgress = { run_id: 'r', status: 'running', message: '正在分析问题', started_at: new Date().toISOString(), steps: [] }
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  useChatStore.setState({ currentConversationId: 'c', conversations: [{ id: 'c', title: '会话', messages: [], detailLoaded: true }],
    loadConversations: vi.fn().mockResolvedValue(undefined), refreshConversationMeta: vi.fn().mockResolvedValue(undefined) })
  mocks.get.mockResolvedValue({ data: { messages: [], latest_turn: null } })
})
afterEach(cleanup)
it('shows progress immediately, receives real calls, and restores records after completion', async () => {
  let resolve!: (value: any) => void
  let handlers: any
  mocks.stream.mockImplementation((_request, options) => { handlers = options; return new Promise(r => { resolve = r }) })
  render(<ChatPage />)
  await waitFor(() => expect(mocks.get).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByText('正在发送问题')).toBeInTheDocument()
  const working = { ...progress, message: '阅读原文', steps: [{ id: 't', label: '阅读原文', detail: 'BGP EVPN', status: 'running', started_at: progress.started_at }] }
  act(() => handlers.onProgress(working))
  expect(screen.getByText('BGP EVPN')).toBeInTheDocument()
  const completed = { ...working, status: 'completed', message: '已完成', ended_at: new Date().toISOString(), steps: working.steps.map(s => ({ ...s, status: 'completed' })) }
  mocks.get.mockResolvedValue({ data: { messages: [{ id: 'answer', role: 'assistant', content: '实际回答', progress: completed }],
    latest_turn: { id: 'r', status: 'completed', result_message_id: 'answer', progress: completed } } })
  await act(async () => { handlers.onProgress(completed); resolve({ response: '实际回答', thread_id: 'c' }) })
  await screen.findByText('实际回答')
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  expect(screen.getByText('BGP EVPN')).toBeInTheDocument()
})
it('keeps a failed stream visible instead of leaving a blank waiting area', async () => {
  mocks.stream.mockRejectedValue(new Error('问答服务暂不可用'))
  render(<ChatPage />)
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('问答服务暂不可用')
  expect(mocks.stream).toHaveBeenCalledOnce()
})
it('restores an active turn on refresh and uses the existing cancel endpoint', async () => {
  mocks.get.mockResolvedValue({ data: { messages: [], latest_turn: { id: 'r', status: 'running', progress } } })
  mocks.post.mockResolvedValue({ data: { progress: { ...progress, status: 'cancel_requested', message: '正在停止本次问答' } } })
  render(<ChatPage />)
  fireEvent.click(await screen.findByRole('button', { name: '停止' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/apps/assistant/turns/r/cancel'))
  expect(await screen.findByText('正在停止本次问答')).toBeInTheDocument()
})
