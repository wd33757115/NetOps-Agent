import { describe, expect, it } from 'vitest'
import { parseSseBuffer } from './authenticatedSse'

describe('parseSseBuffer', () => {
  it('parses named, default, comment and multiline SSE frames', () => {
    const parsed = parseSseBuffer(
      ': heartbeat\r\n'
      + 'event: timeline\r\n'
      + 'data: {"status":"running"}\r\n\r\n'
      + 'data: first line\n'
      + 'data: second line\n\n',
    )

    expect(parsed.rest).toBe('')
    expect(parsed.events).toEqual([
      {
        event: 'timeline',
        data: '{"status":"running"}',
      },
      {
        event: 'message',
        data: 'first line\nsecond line',
      },
    ])
  })

  it('preserves an incomplete frame for the next network chunk', () => {
    const first = parseSseBuffer(
      'event: progress\ndata: {"step":',
    )
    expect(first.events).toEqual([])

    const second = parseSseBuffer(
      `${first.rest}2}\n\n`,
    )
    expect(second.events).toEqual([
      {
        event: 'progress',
        data: '{"step":2}',
      },
    ])
  })
})
