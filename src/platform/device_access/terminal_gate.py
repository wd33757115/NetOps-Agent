"""Human SSH terminal: keystrokes go straight to the device, Enter is checked.

Bastion hosts do it this way (JumpServer KoKo): the device keeps its own line
editing, Tab completion, ``?`` help and history. The server replays the device
output on a virtual screen. When Enter is pressed on a device prompt line, the
command shown on that line is matched against the administrator's SSH whitelist.
Allowed: Enter goes on and the output is recorded until the prompt returns.
Denied: the device line is cleared and the user is told.

The screen can be bypassed (device hotkeys that run commands without Enter, prompts
of unknown shape), so this prevents mistakes. The device account's role or TACACS
command authorization is the real read-only boundary.
"""

import re
import time

import pyte
from pyte.screens import Margins
from wcwidth import wcwidth

from .contracts import DeviceAccessError
from .human_cli import _CLI_ERROR

# A device prompt at the start of a line: Comware/VRP <name> or [name-view],
# Cisco-style name# or name(config-if)#, shell-style user@host:~$.
PROMPT = re.compile(
    r"<[^<>\r\n]{1,128}>|\[[^\[\]\r\n]{1,128}\][#$>]?"
    r"|[^\s<>\[\]#$%()]{1,128}(?:\([^()\r\n]{0,64}\))?[#$>%]"
)
QUIET = 0.15  # no device output for this long: the echo has settled
SETTLE_LIMIT = 1.0  # a keystroke that echoes nothing must not hold Enter longer
MAX_OUTPUT = 512 * 1024
# Refusals the user can recover from; anything else (audit down, cancelled) ends it.
DENIALS = frozenset(
    {"policy_denied", "policy_match_failed", "policy_unavailable", "invalid_command"}
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_ERASE = re.compile(r"\x1b\[\d*D *\x1b\[\d*D")  # Comware erases its pager this way
_BACKSPACE = re.compile(r"[^\x08]\x08")
_MORE = re.compile(r"[ \t]*-{2,4} ?More ?-{2,4}")


class _Screen(pyte.Screen):
    """Counts scrolled lines so a row found earlier can be found again."""

    scrolled = 0

    def index(self):
        if self.cursor.y == (self.margins or Margins(0, self.lines - 1)).bottom:
            self.scrolled += 1
        super().index()

    def reverse_index(self):
        if self.cursor.y == (self.margins or Margins(0, self.lines - 1)).top:
            self.scrolled -= 1
        super().reverse_index()


def transcript(text):
    """Readable command output for the audit record: no escapes, pager or echo."""
    text = _ANSI.sub("", _ERASE.sub("", text)).replace("\x00", "").replace("\x07", "")
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.rstrip("\r")
        if "\r" in line:  # a carriage return overwrites from the line start
            line = line.rsplit("\r", 1)[-1]
        for _ in range(64):
            if "\x08" not in line:
                break
            line = _BACKSPACE.sub("", line)
        lines.append(_MORE.sub("", line.replace("\x08", "")).rstrip())
    if lines and not lines[0]:
        lines.pop(0)  # the echo of Enter
    while lines and not lines[-1]:
        lines.pop()
    if lines and PROMPT.match(lines[-1]):
        lines.pop()  # the prompt that came back, perhaps with the next command typed
    return "\n".join(lines)


class TerminalGate:
    def __init__(self, channel, operation, show, *, before_send=None, cols=80, rows=24):
        self.channel, self.operation, self.show = channel, operation, show
        self.before_send = before_send or (lambda: None)
        self.screen = _Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        # Chunks received so far and when the last came. Whether an echo arrived
        # after a send is decided by order, not by a coarse clock.
        self.chunks, self.last_output = 0, time.monotonic()
        self.sent_chunks = self.checked_chunks = 0
        self.anchor, self.anchor_prompt = None, ""  # where typing began at a prompt
        self.waiting = None  # ("enter" | "clear", since)
        self.denied = None
        self.ticket, self.ticket_chunks, self.output, self.output_size = None, 0, [], 0

    @property
    def busy(self):
        return self.waiting is not None

    def _row(self, y):
        line, cells, wide = self.screen.buffer[y], [], False
        for x in range(self.screen.columns):
            if wide:
                wide = False
                continue
            data = line[x].data
            wide = bool(data) and wcwidth(data[0]) == 2
            cells.append(data)
        return "".join(cells).rstrip()

    def _at_cursor(self):
        row = self._row(self.screen.cursor.y)
        match = PROMPT.match(row)
        return (match.group(), row[match.end() :].strip()) if match else None

    def _send(self, text):
        self.channel.sendall(text.encode("utf-8"))
        self.sent_chunks = self.chunks

    def received(self, text):
        self.stream.feed(text)
        self.show(text)
        self.chunks += 1
        self.last_output = time.monotonic()
        if self.ticket is not None and self.output_size < MAX_OUTPUT:
            self.output.append(text)
            self.output_size += len(text)

    def resize(self, cols, rows):
        self.screen.resize(rows, cols)
        self.anchor = None

    def typed(self, data):
        """One browser input frame. Returns a notice for the user, or None."""
        body = data.rstrip("\r\n")
        if body == data:
            self._mark()
            self._send(data)
            return None
        if "\r" in body or "\n" in body or data[len(body) :] not in {"\r", "\n", "\r\n"}:
            return "多行粘贴未发送：请逐条粘贴并回车"
        if body:
            self._mark()
            self._send(body)
        self.waiting = ("enter", time.monotonic())
        return None

    def _mark(self):
        y = self.screen.cursor.y
        match = PROMPT.match(self._row(y))
        if match and self.ticket is not None and self.chunks > self.ticket_chunks:
            self.finish()  # typing on a new prompt: the last command is over
        if self.anchor is None and match:
            self.anchor, self.anchor_prompt = y + self.screen.scrolled, match.group()

    def _line(self):
        """(prompt, command) on the Enter line, or None when it is not a device prompt."""
        y = self.screen.cursor.y
        if self.anchor is None:
            first = y
        else:
            # The lowest prompt row since typing began: ``?`` help and device log
            # messages redraw the prompt, and a long command wraps onto more rows.
            top = max(0, self.anchor - self.screen.scrolled)
            first = next((r for r in range(y, top - 1, -1) if PROMPT.match(self._row(r))), None)
            if first is None:
                return (self.anchor_prompt, None)  # typing began at a prompt now gone
        wrapped = (self._row(r).ljust(self.screen.columns) for r in range(first, y))
        text = "".join(wrapped) + self._row(y)
        match = PROMPT.match(text)
        if match is None:
            return None
        return match.group(), text[match.end() :].strip()

    def tick(self):
        now = time.monotonic()
        if self.waiting is None:
            if (
                self.ticket is not None
                and self.chunks > max(self.ticket_chunks, self.checked_chunks)
                and now - self.last_output >= QUIET
            ):
                self.checked_chunks = self.chunks
                found = self._at_cursor()
                if found is not None and not found[1]:
                    self.finish()  # the prompt came back
            return
        kind, since = self.waiting
        settled = self.chunks > self.sent_chunks and now - self.last_output >= QUIET
        if not settled and now - since < SETTLE_LIMIT:
            return
        self.waiting = None
        if kind == "enter":
            self._enter()
        else:
            self._cleared()

    def _enter(self):
        found, self.anchor = self._line(), None
        if found is None:  # password, [Y/N], pager: not a device command line
            self._send("\r")
            return
        prompt, command = found
        self.finish()
        if command == "":
            self._send("\r")
            return
        try:
            if command is None:
                self.operation.event(
                    "command_denied", status="denied", failure_code="invalid_command"
                )
                raise DeviceAccessError("invalid_command", "无法识别当前命令行")
            self.before_send()
            ticket = self.operation.before_command(command)
        except DeviceAccessError as exc:
            if exc.failure_code not in DENIALS:
                raise
            self.denied = (command or "", str(exc))
            comware = prompt[:1] in {"<", "["} and prompt[-1:] in {">", "]"}
            # Ctrl+E, then Ctrl+X (Comware/VRP) or Ctrl+U: delete the whole line.
            self._send("\x05\x18" if comware else "\x05\x15")
            self.waiting = ("clear", time.monotonic())
            return
        self.ticket, self.output, self.output_size = ticket, [], 0
        self._send("\r")
        self.ticket_chunks = self.chunks

    def _cleared(self):
        found = self._at_cursor()
        if found is None or found[1]:
            raise DeviceAccessError("terminal_unsafe", "被拦截的命令未能从设备输入行清除，已断开")
        command, reason = self.denied
        self.denied = None
        shown = f"「{command}」" if command else ""
        notice = f"\r\n\x1b[33m[NetOps] 已拦截{shown}：{reason}\x1b[0m"
        self.stream.feed(notice)
        self.show(notice)
        self._send("\r")  # the device prints a fresh prompt below the notice

    def close(self):
        """The session ends: a command whose prompt has not returned is incomplete."""
        if self.ticket is not None:
            found = self._at_cursor()
            done = found is not None and not found[1] and self.chunks > self.ticket_chunks
            self.finish(None if done else "terminal_closed")

    def finish(self, failure_code=None):
        """Record the running command with its output."""
        if self.ticket is None:
            return
        ticket, text = self.ticket, "".join(self.output)
        self.ticket, self.output, self.output_size = None, [], 0
        output = transcript(text)
        error = failure_code or ("command_failed" if _CLI_ERROR.search(output) else None)
        detail = {"output_complete": False} if failure_code else {}
        self.operation.after_command(
            ticket,
            output=output,
            status="failed" if error else "succeeded",
            failure_code=error,
            **detail,
        )
