import type { Terminal } from '@xterm/xterm'
import { expect, it, vi } from 'vitest'
import { LocalCommandLine } from './LocalCommandLine'

function setup(prefix = '<VSR-1>') {
  const callbacks: (() => void)[] = []
  const marker = { line: 0, isDisposed: false, dispose: vi.fn() }
  const terminal = {
    cols: 80, rows: 24, options: { cursorBlink: false },
    buffer: { active: {
      baseY: 0, cursorY: 0, cursorX: prefix.length,
      getLine: vi.fn(() => ({ translateToString: vi.fn(() => prefix) })),
    } },
    registerMarker: vi.fn(() => marker),
    focus: vi.fn(), scrollToBottom: vi.fn(),
    write: vi.fn((_data: string, callback?: () => void) => { if (callback) callbacks.push(callback) }),
  }
  const submitted: string[] = []
  const onNotice = vi.fn()
  const onSubmit = vi.fn((command: string) => { submitted.push(command); editor.suspend(); return true })
  const editor = new LocalCommandLine(terminal as unknown as Terminal, { onSubmit, onNotice })
  const flush = () => { while (callbacks.length) callbacks.shift()!() }
  const ready = (command = '') => { editor.activate(command); flush() }
  const displayed = () => terminal.write.mock.calls.at(-1)?.[0] || ''
  return { terminal, marker, editor, submitted, onSubmit, onNotice, flush, ready, displayed }
}

it('waits for output parsing, edits locally, then erases before submitting one complete command', () => {
  const { editor, terminal, flush, submitted, displayed } = setup()
  editor.activate()
  editor.handleData('ignored before the prompt is ready')
  flush()
  editor.handleData('display clock')
  expect(submitted).toEqual([])
  expect(displayed()).toContain('<VSR-1>display clock')
  editor.handleData('\r')
  expect(submitted).toEqual(['display clock'])
  expect(displayed()).toBe('\x1b[1;1H\x1b[J<VSR-1>')
  editor.handleData('\r')
  editor.handleData('not accepted while executing')
  expect(submitted).toHaveLength(1)
  expect(terminal.options.cursorBlink).toBe(false)
})

it('supports insertion, arrows, Home/End, Backspace and Delete without sending keys', () => {
  const { editor, ready, submitted } = setup()
  ready()
  editor.handleData('disply clock')
  editor.handleData('\x1b[H')
  for (let index = 0; index < 5; index++) editor.handleData('\x1b[C')
  editor.handleData('a')
  editor.handleData('\x1b[F')
  editor.handleData('x')
  editor.handleData('\x7f')
  editor.handleData('\x1b[H')
  editor.handleData('x')
  editor.handleData('\x1b[D')
  editor.handleData('\x1b[3~')
  expect(submitted).toEqual([])
  editor.handleData('\r')
  expect(submitted).toEqual(['display clock'])
})

it('rejects an entire multiline or control-character paste without executing a prefix', () => {
  const { editor, ready, submitted, onNotice } = setup()
  ready()
  for (const raw of ['display clock\nsystem-view', 'display clock\r', '\r', '\t', 'show\x1b[A', 'display\u2028clock', 'display\u200bclock']) editor.handlePaste(raw)
  editor.handleData('display clock\rsystem-view')
  editor.handleData('\r')
  expect(submitted).toEqual([])
  expect(onNotice).toHaveBeenLastCalledWith('请一次输入一条完整命令，不支持控制字符或多行粘贴。')
  editor.handlePaste('display clock')
  expect(submitted).toEqual([])
  editor.handleData('\r')
  expect(submitted).toEqual(['display clock'])
})

it('keeps Tab and Ctrl+C local, with Ctrl+C clearing the draft', () => {
  const { editor, ready, submitted, onNotice, displayed } = setup()
  ready('display clock')
  editor.handleData('\t')
  expect(onNotice).toHaveBeenLastCalledWith('暂不支持远端 Tab 补全，请输入完整命令。')
  editor.handleData('\x03')
  expect(displayed()).not.toContain('display clock')
  editor.handleData('\r')
  expect(submitted).toEqual([])
})

it('preserves the unsent draft when browsing history and can edit a rejected command', () => {
  const { editor, ready, submitted } = setup()
  ready('display clock')
  editor.handleData('\r')
  ready()
  editor.handleData('display version')
  editor.handleData('\x1b[A')
  editor.handleData('\x1b[B')
  editor.handleData('\r')
  expect(submitted).toEqual(['display clock', 'display version'])
  ready('system-vie')
  editor.handleData('w')
  editor.handleData('\r')
  expect(submitted.at(-1)).toBe('system-view')
})

it('keeps only 50 submitted commands in session history', () => {
  const { editor, ready, submitted } = setup()
  for (let index = 0; index < 52; index++) {
    ready(`display ${index}`)
    editor.handleData('\r')
  }
  ready()
  for (let index = 0; index < 60; index++) editor.handleData('\x1b[A')
  editor.handleData('\r')
  expect(submitted.at(-1)).toBe('display 2')
})

it('retains an editable command when the caller declines to submit it', () => {
  const { editor, ready, onSubmit, displayed } = setup()
  onSubmit.mockImplementationOnce(() => false)
  ready('display cloc')
  editor.handleData('\r')
  editor.handleData('k')
  expect(displayed()).toContain('display clock')
  expect(onSubmit).toHaveBeenCalledTimes(1)
})

it('clips the display horizontally while submitting the entire command after resize', () => {
  const { editor, ready, terminal, flush, displayed, submitted } = setup()
  terminal.cols = 20
  const command = 'display interface GigabitEthernet1/0/1'
  ready(command)
  expect(displayed()).toContain('net1/0/1')
  expect(displayed()).not.toContain(command)
  terminal.cols = 10
  editor.resize()
  flush()
  expect(displayed()).toContain('…')
  editor.handleData('\r')
  expect(submitted).toEqual([command])
})

it('does not split Chinese or combining-character edits, including separately typed accents', () => {
  const { editor, ready, submitted } = setup()
  ready()
  editor.handleData('测试e')
  editor.handleData('\u0301')
  editor.handleData('\x7f')
  editor.handleData('\x1b[D')
  editor.handleData('中')
  editor.handleData('\r')
  expect(submitted).toEqual(['测中试'])
})

it('limits full command code points without splitting surrogate pairs', () => {
  const { editor, ready, submitted, onNotice } = setup()
  ready()
  editor.handlePaste('𠀀'.repeat(4096))
  editor.handlePaste('x')
  expect(onNotice).toHaveBeenLastCalledWith('命令最多支持 4096 个字符。')
  editor.handleData('\r')
  expect(Array.from(submitted[0])).toHaveLength(4096)
})

it('cannot reactivate from a queued callback after stop or disposal', () => {
  const { editor, flush, terminal, submitted } = setup()
  editor.activate('display clock')
  editor.suspend()
  flush()
  editor.handleData('\r')
  expect(submitted).toEqual([])
  expect(terminal.focus).not.toHaveBeenCalled()
  editor.activate('display clock')
  editor.dispose()
  flush()
  editor.handlePaste('display clock')
  editor.handleData('\r')
  expect(submitted).toEqual([])
  expect(terminal.focus).not.toHaveBeenCalled()
})

it('clears wrapped local remnants from the prompt marker after shrinking the terminal', () => {
  const { editor, ready, marker, terminal, flush, displayed } = setup()
  marker.line = 4
  terminal.buffer.active.cursorY = 4
  ready('display interface GigabitEthernet1/0/1')
  terminal.cols = 20
  terminal.buffer.active.cursorY = 6
  editor.resize()
  flush()
  expect(displayed().startsWith('\x1b[5;1H\x1b[J')).toBe(true)
  expect(displayed()).not.toContain('\n')
})

it('uses stable xterm APIs to retain one prompt across real resize, submit and remote echo', async () => {
  const canvas = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  const { Terminal: Xterm } = await import('@xterm/xterm')
  canvas.mockRestore()
  const terminal = new Xterm({ cols: 40, rows: 10, allowProposedApi: false })
  const onSubmit = vi.fn(() => { editor.suspend(); return true })
  const editor = new LocalCommandLine(terminal, { onSubmit, onNotice: vi.fn() })
  const line = (row: number) => terminal.buffer.active.getLine(row)?.translateToString(true)
  terminal.write('previous output\r\n<VSR-1>')
  editor.activate('display interface GigabitEthernet1/0/1')
  try {
    await vi.waitFor(() => expect(line(1)).toContain('GigabitEthernet1/0/1'))
    terminal.resize(20, 10)
    editor.resize()
    await vi.waitFor(() => expect(line(1)).toBe('<VSR-1>hernet1/0/1'))
    expect(line(0)).toBe('previous output')
    expect(line(2)).toBe('')
    editor.handleData('\r')
    expect(onSubmit).toHaveBeenCalledWith('display interface GigabitEthernet1/0/1')
    terminal.write('display clock\r\n12:00\r\n<VSR-1>')
    editor.activate()
    await vi.waitFor(() => expect(line(1)).toBe('<VSR-1>display clock'))
    expect(line(2)).toBe('12:00')
    expect(line(3)).toBe('<VSR-1>')
  } finally {
    editor.dispose()
    terminal.dispose()
  }
})
