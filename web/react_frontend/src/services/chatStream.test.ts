import { afterEach, expect, it, vi } from 'vitest'
import { streamChatMessage } from './chatStream'
vi.mock('../config/api', () => ({ API_BASE_URL: '/api', getAccessToken: () => null }))
afterEach(() => vi.unstubAllGlobals())
function mockStream(text: string) {
  const bytes = new TextEncoder().encode(text)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({
    start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() },
  }))))
}
it('handles fragmented UTF-8, CRLF and a final block without a delimiter', async () => {
  mockStream('event: progress\r\ndata: {"message":"阅读原文","steps":[]}\r\n\r\nevent: final_answer\r\ndata: {"response":"回答","thread_id":"c"}')
  const progress = vi.fn()
  const result = await streamChatMessage({ query: '问题' }, { onProgress: progress })
  expect(progress).toHaveBeenCalledWith({ message: '阅读原文', steps: [] })
  expect(result.response).toBe('回答')
  expect(fetch).toHaveBeenCalledOnce()
})
it('preserves terminal progress before reporting failure without resubmitting', async () => {
  mockStream('event: progress\ndata: {"status":"failed","steps":[]}\n\nevent: error\ndata: {"message":"已中断"}\n\n')
  const progress = vi.fn()
  await expect(streamChatMessage({ query: '问题' }, { onProgress: progress })).rejects.toThrow('已中断')
  expect(progress).toHaveBeenCalledWith({ status: 'failed', steps: [] })
  expect(fetch).toHaveBeenCalledOnce()
})
