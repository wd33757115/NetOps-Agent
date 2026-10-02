import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ModelSettings from './ModelSettings'
import { api } from '../config/api'

vi.mock('../config/api', () => ({ api: { get: vi.fn(), put: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const options = {
  models: [{ id: 'deepseek-v4-flash', label: 'V4 Flash' }, { id: 'deepseek-v4-pro', label: 'V4 Pro' }],
  reasoning_efforts: [{ id: 'off', label: '关' }, { id: 'low', label: '低' }, { id: 'high', label: '高' }, { id: 'max', label: '最高' }],
  default: { model: 'deepseek-v4-flash', reasoning_effort: 'off' },
}
let saved = { model: 'deepseek-v4-flash', reasoning_effort: 'off', chosen: false }
beforeEach(() => {
  saved = { model: 'deepseek-v4-flash', reasoning_effort: 'off', chosen: false }
  vi.mocked(api.get).mockImplementation(((url: string) =>
    Promise.resolve({ data: url.endsWith('/conversations/c1/model-settings') ? saved : options })) as never)
})

it('shows the conversation choice and saves a new one at once', async () => {
  vi.mocked(api.put).mockImplementation(((_url: string, body: object) =>
    Promise.resolve({ data: { ...body, chosen: true } })) as never)
  render(<ModelSettings conversationId="c1" />)
  const toggle = await screen.findByRole('button', { name: '模型与思考强度：V4 Flash，思考关' })
  fireEvent.click(toggle)
  fireEvent.click(screen.getByRole('button', { name: '高' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/apps/assistant/conversations/c1/model-settings',
    { model: 'deepseek-v4-flash', reasoning_effort: 'high' }))
  expect(screen.getByRole('button', { name: '高' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: '模型与思考强度：V4 Flash，思考高' })).toHaveClass('is-thinking')
  expect(screen.getByText('只对当前会话生效；开启思考回答更慢、用量更多。')).toBeInTheDocument()
})

it('restores a saved choice and rolls back one that failed to save', async () => {
  saved = { model: 'deepseek-v4-pro', reasoning_effort: 'max', chosen: true }
  vi.mocked(api.put).mockRejectedValue(new Error('down'))
  render(<ModelSettings conversationId="c1" />)
  fireEvent.click(await screen.findByRole('button', { name: '模型与思考强度：V4 Pro，思考最高' }))
  fireEvent.click(screen.getByRole('button', { name: 'V4 Flash' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('设置未保存，请重试')
  expect(screen.getByRole('button', { name: 'V4 Pro' })).toHaveAttribute('aria-pressed', 'true')
})

it('shows nothing before a conversation exists', () => {
  const { container } = render(<ModelSettings conversationId={null} />)
  expect(container).toBeEmptyDOMElement()
})
