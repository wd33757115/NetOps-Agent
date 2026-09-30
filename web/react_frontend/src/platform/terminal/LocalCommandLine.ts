import type { IMarker, Terminal } from '@xterm/xterm'

const MAX_COMMAND_LENGTH = 4096
const CONTROL_CHARACTERS = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u
const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : undefined
const characters = (text: string): string[] => segmenter ? Array.from(segmenter.segment(text), part => part.segment) : Array.from(text)

// Match the terminal's default cell widths for combining marks and East Asian text.
const cells = (text: string) => Array.from(text).reduce((width, character) => {
  const point = character.codePointAt(0)!
  if (/\p{M}/u.test(character)) return width
  const wide = point >= 0x1100 && (
    point <= 0x115f || point === 0x2329 || point === 0x232a ||
    (point >= 0x2e80 && point <= 0xa4cf && point !== 0x303f) ||
    (point >= 0xac00 && point <= 0xd7a3) || (point >= 0xf900 && point <= 0xfaff) ||
    (point >= 0xfe10 && point <= 0xfe19) || (point >= 0xfe30 && point <= 0xfe6f) ||
    (point >= 0xff00 && point <= 0xff60) || (point >= 0xffe0 && point <= 0xffe6) ||
    (point >= 0x20000 && point <= 0x3fffd)
  )
  return width + (wide ? 2 : 1)
}, 0)

export interface LocalCommandLineOptions {
  onSubmit: (command: string) => boolean
  onNotice: (message: string) => void
}

/** Edits locally; only onSubmit can send a complete command to the server. */
export class LocalCommandLine {
  private active = false
  private disposed = false
  private revision = 0
  private draft: string[] = []
  private cursor = 0
  private prefix = ''
  private marker?: IMarker
  private history: string[] = []
  private historyPosition = 0
  private historyDraft = ''

  constructor(private terminal: Terminal, private options: LocalCommandLineOptions) {}

  activate(command = '') {
    if (this.disposed) return
    const revision = ++this.revision
    this.active = false
    // ready/finished can arrive before xterm has parsed the last output chunk.
    this.terminal.write('', () => {
      if (this.disposed || revision !== this.revision) return
      this.marker?.dispose()
      const buffer = this.terminal.buffer.active
      this.prefix = buffer.getLine(buffer.baseY + buffer.cursorY)?.translateToString(false, 0, buffer.cursorX) || ''
      this.marker = this.terminal.registerMarker(0)
      this.draft = characters(command)
      this.cursor = this.draft.length
      this.historyPosition = this.history.length
      this.historyDraft = command
      this.active = true
      this.terminal.options.cursorBlink = true
      this.render()
      this.terminal.focus()
    })
  }

  handleData(data: string) {
    if (!this.active || this.disposed) return
    switch (data) {
      case '\r': {
        const command = this.draft.join('')
        if (!command.trim()) return
        // The caller suspends before socket.send, so remote echo cannot race the erase.
        if (this.options.onSubmit(command)) {
          if (this.history[this.history.length - 1] !== command) this.history = [...this.history.slice(-49), command]
          this.historyPosition = this.history.length
          this.historyDraft = ''
        }
        return
      }
      case '\x1b[A': case '\x1bOA': this.browseHistory(-1); return
      case '\x1b[B': case '\x1bOB': this.browseHistory(1); return
      case '\x1b[D': case '\x1bOD': this.cursor = Math.max(0, this.cursor - 1); break
      case '\x1b[C': case '\x1bOC': this.cursor = Math.min(this.draft.length, this.cursor + 1); break
      case '\x1b[H': case '\x1bOH': case '\x1b[1~': case '\x01': this.cursor = 0; break
      case '\x1b[F': case '\x1bOF': case '\x1b[4~': case '\x05': this.cursor = this.draft.length; break
      case '\x7f': case '\b':
        if (this.cursor > 0) this.draft.splice(--this.cursor, 1)
        break
      case '\x1b[3~': this.draft.splice(this.cursor, 1); break
      case '\x03': this.draft = []; this.cursor = 0; this.options.onNotice(''); break
      case '\t': this.options.onNotice('暂不支持远端 Tab 补全，请输入完整命令。'); return
      default:
        this.insert(data)
        return
    }
    this.render()
  }

  handlePaste(raw: string) {
    if (!this.active || this.disposed) return
    this.insert(raw)
  }

  /** Erases only the local draft; the device will provide the canonical command echo. */
  suspend() {
    if (this.disposed) return
    ++this.revision
    const wasActive = this.active
    this.active = false
    this.terminal.options.cursorBlink = false
    if (wasActive) this.terminal.write(this.erase() + this.prefix)
  }

  resize() {
    if (!this.active || this.disposed) return
    const revision = ++this.revision
    this.active = false
    this.terminal.write('', () => {
      if (this.disposed || revision !== this.revision) return
      this.active = true
      this.render()
    })
  }

  dispose() {
    ++this.revision
    this.active = false
    this.disposed = true
    this.marker?.dispose()
    this.history = []
    this.draft = []
  }

  private insert(text: string) {
    if (CONTROL_CHARACTERS.test(text)) {
      this.options.onNotice('请一次输入一条完整命令，不支持控制字符或多行粘贴。')
      return
    }
    const before = this.draft.slice(0, this.cursor).join('')
    const after = this.draft.slice(this.cursor).join('')
    if (Array.from(before + text + after).length > MAX_COMMAND_LENGTH) {
      this.options.onNotice('命令最多支持 4096 个字符。')
      return
    }
    // Re-segment so a combining mark entered separately joins its preceding base.
    this.draft = characters(before + text + after)
    this.cursor = characters(before + text).length
    this.options.onNotice('')
    this.render()
  }

  private browseHistory(direction: number) {
    if (this.historyPosition === this.history.length) this.historyDraft = this.draft.join('')
    this.historyPosition = Math.max(0, Math.min(this.history.length, this.historyPosition + direction))
    this.draft = characters(this.historyPosition === this.history.length ? this.historyDraft : this.history[this.historyPosition])
    this.cursor = this.draft.length
    this.render()
  }

  private row() {
    const buffer = this.terminal.buffer.active
    const line = this.marker && !this.marker.isDisposed ? this.marker.line : buffer.baseY + buffer.cursorY
    return Math.max(1, Math.min(this.terminal.rows, line - buffer.baseY + 1))
  }

  private erase() {
    // Nothing follows the active prompt. Clear its wrapped remnants after a resize too.
    return `\x1b[${this.row()};1H\x1b[J`
  }

  private render() {
    if (!this.active) return
    const columns = Math.max(2, this.terminal.cols)
    const prompt = characters(this.prefix)
    const promptLimit = Math.max(0, columns - 5)
    let shortened = false
    while (prompt.length && cells(prompt.join('')) + (shortened ? 1 : 0) > promptLimit) { prompt.shift(); shortened = true }
    const visiblePrompt = (shortened && promptLimit ? '…' : '') + prompt.join('')
    const promptWidth = cells(visiblePrompt)
    const available = columns - promptWidth - 1 // Reserve the last cell to prevent wrapping.
    let start = this.cursor
    let leftWidth = 0
    while (start > 0 && leftWidth + cells(this.draft[start - 1]) < available) leftWidth += cells(this.draft[--start])
    let end = this.cursor
    let shownWidth = leftWidth
    while (end < this.draft.length && shownWidth + cells(this.draft[end]) <= available) shownWidth += cells(this.draft[end++])
    const text = this.draft.slice(start, end).join('')
    this.terminal.scrollToBottom()
    this.terminal.write(`${this.erase()}${visiblePrompt}${text}\x1b[${this.row()};${promptWidth + leftWidth + 1}H`)
  }
}
