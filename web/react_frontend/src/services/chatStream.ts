// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import type { ChatRequest, ChatResponse } from './api'
import { API_BASE_URL, getAccessToken } from '../config/api'

export interface TraceStep {
  node: string
  label: string
  status?: string
  skills?: string[]
  skill?: string
}

export interface StreamChatHandlers {
  onEvent?: (event: string, data: Record<string, unknown>) => void
  onStep?: (step: TraceStep) => void
  onStatus?: (message: string) => void
  signal?: AbortSignal
}

function parseSSEBlock(block: string): { event: string; data: Record<string, unknown> } | null {
  let event = 'message'
  let dataStr = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataStr += line.slice(5).trim()
    }
  }
  if (!dataStr) return null
  try {
    return { event, data: JSON.parse(dataStr) as Record<string, unknown> }
  } catch {
    return null
  }
}

export async function streamChatMessage(
  payload: ChatRequest,
  handlers: StreamChatHandlers = {}
): Promise<ChatResponse> {
  const token = getAccessToken()
  const response = await fetch(`${API_BASE_URL}/chat/stream/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal: handlers.signal,
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `Stream request failed: ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('浏览器不支持流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let finalAnswer: ChatResponse | null = null

  let streamDone = false
  while (!streamDone) {
    const { done, value } = await reader.read()
    streamDone = done
    if (streamDone) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''

    for (const part of parts) {
      const parsed = parseSSEBlock(part.trim())
      if (!parsed) continue

      const { event, data } = parsed
      handlers.onEvent?.(event, data)

      if (event === 'status' && typeof data.message === 'string') {
        handlers.onStatus?.(data.message)
      }

      if (event === 'node_start' || event === 'skill_execute') {
        handlers.onStep?.({
          node: String(data.node || ''),
          label: String(data.label || data.node || '执行中'),
          status: String(data.status || ''),
          skills: Array.isArray(data.skills) ? (data.skills as string[]) : undefined,
          skill: data.skill ? String(data.skill) : undefined,
        })
      }

      if (event === 'error') {
        throw new Error(String(data.message || '流式执行失败'))
      }

      if (event === 'final_answer') {
        finalAnswer = {
          response: String(data.response || ''),
          thread_id: String(data.thread_id || payload.thread_id || ''),
          agent_type: data.agent_type ? String(data.agent_type) : undefined,
          celery_task_id: data.celery_task_id ? String(data.celery_task_id) : undefined,
          download_url: data.download_url ? String(data.download_url) : undefined,
          references: data.references as ChatResponse['references'],
        }
      }
    }
  }

  if (!finalAnswer) {
    throw new Error('未收到最终回答')
  }
  return finalAnswer
}
