import type { ChatRequest, ChatResponse } from './api'
import { API_BASE_URL, getAccessToken } from '../config/api'
import type { AgentProgress } from '../components/TraceProgress'

export interface StreamChatHandlers {
  onProgress?: (progress: AgentProgress) => void
  signal?: AbortSignal
}

export async function streamChatMessage(payload: ChatRequest, handlers: StreamChatHandlers = {}): Promise<ChatResponse> {
  const token = getAccessToken()
  const response = await fetch(`${API_BASE_URL}/chat/stream/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload), signal: handlers.signal,
  })
  if (!response.ok) throw new Error(`问答服务暂不可用（${response.status}），请检查权限或稍后重试。`)
  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式响应')
  const decoder = new TextDecoder()
  let buffer = ''
  let finalAnswer: ChatResponse | null = null
  const consume = (block: string) => {
    let event = 'message'
    const lines: string[] = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart())
    }
    if (!lines.length) return
    const data = JSON.parse(lines.join('\n'))
    if (event === 'progress') handlers.onProgress?.(data as AgentProgress)
    if (event === 'error') throw new Error(String(data.message || '本次问答未完成'))
    if (event === 'final_answer') finalAnswer = data as ChatResponse
  }
  try {
    let finished = false
    while (!finished) {
      const { done, value } = await reader.read()
      finished = done
      buffer += decoder.decode(value, { stream: !done })
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() || ''
      parts.forEach(consume)
      if (done) { if (buffer.trim()) consume(buffer); break }
    }
    if (!finalAnswer) throw new Error('连接已中断，正在核对本轮状态；不会自动重复提交。')
    return finalAnswer
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
