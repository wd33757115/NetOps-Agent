import { getAccessToken } from '../config/api'

export interface AuthenticatedSseEvent {
  event: string
  data: string
}

interface ParsedSseBuffer {
  events: AuthenticatedSseEvent[]
  rest: string
}

interface AuthenticatedSseOptions {
  onEvent: (event: AuthenticatedSseEvent) => void
  onOpen?: () => void
  onError?: (error: Error) => void
}

export function parseSseBuffer(
  input: string,
  flush = false,
): ParsedSseBuffer {
  const normalized = input.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  let rest = blocks.pop() || ''
  if (flush && rest.trim()) {
    blocks.push(rest)
    rest = ''
  }
  const events = blocks.flatMap((block) => {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator >= 0 ? line.slice(0, separator) : line
      const value = separator >= 0
        ? line.slice(separator + 1).replace(/^ /, '')
        : ''
      if (field === 'event' && value) event = value
      if (field === 'data') data.push(value)
    }
    return data.length ? [{ event, data: data.join('\n') }] : []
  })
  return { events, rest }
}

export function openAuthenticatedEventStream(
  url: string,
  options: AuthenticatedSseOptions,
): () => void {
  const controller = new AbortController()
  void (async () => {
    const token = getAccessToken()
    const response = await fetch(url, {
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(`SSE request failed with status ${response.status}`)
    }
    if (!response.body) {
      throw new Error('SSE response body is unavailable')
    }
    options.onOpen?.()
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!controller.signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parsed = parseSseBuffer(buffer)
      buffer = parsed.rest
      parsed.events.forEach(options.onEvent)
    }
    buffer += decoder.decode()
    parseSseBuffer(buffer, true).events.forEach(options.onEvent)
  })().catch((error: unknown) => {
    if (controller.signal.aborted) return
    options.onError?.(
      error instanceof Error ? error : new Error(String(error)),
    )
  })
  return () => controller.abort()
}
