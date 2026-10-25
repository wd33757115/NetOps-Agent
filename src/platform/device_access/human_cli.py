"""Complete-line commands over a private SSH channel (batch execution).

Prompt-changing network CLI commands are supported; interactive questions and
paging stop the channel. The human terminal uses terminal_gate instead.
"""

import codecs
import re
import time

from .contracts import DeviceAccessError
from .errors import CommandExecutionError

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PROMPT = re.compile(r"(?m)^(?:[^\r\n]{1,128}[#>$]|\[[^\r\n\[\]]{1,128}\]) *$")
_INTERACTIVE = re.compile(
    r"(?im)(?:--\s*more\s*--|---- More ----|(?:password|passphrase)\s*:|"
    r"\[(?:y/n|yes/no)\]|\((?:y/n|yes/no)\)|press (?:enter|return|any key))\s*[:?]?\s*$"
)
_CLI_ERROR = re.compile(
    r"(?im)^\s*(?:%\s*(?:invalid|error|unknown|incomplete|ambiguous|wrong|unrecognized)|"
    r"(?:unrecognized command|wrong parameter|incomplete command|error:|unknown command))"
)


class HumanCLI:
    def __init__(
        self, channel, operation, guard, *, on_output=None, before_send=None, max_bytes=256 * 1024
    ):
        self.channel = channel
        self.operation = operation
        self.guard = guard
        self.on_output = on_output or (lambda value: None)
        self.before_send = before_send or guard
        self.max_bytes = max_bytes
        self.usable = True
        self.prompt_pattern = _PROMPT
        self.last_prompt = None

    def _read(self, timeout):
        deadline = time.monotonic() + timeout
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        output, size = "", 0
        candidate = None
        while True:
            try:
                self.guard()
            except DeviceAccessError as exc:
                raise CommandExecutionError(exc.failure_code, str(exc), output) from None
            if time.monotonic() >= deadline:
                raise CommandExecutionError(
                    "timeout", "等待设备提示符超时，命令完成状态未知", output
                )
            if self.channel.recv_ready():
                raw = self.channel.recv(min(8192, self.max_bytes - size + 1))
                if not raw:
                    raise CommandExecutionError("connection_closed", "设备连接已关闭", output)
                size += len(raw)
                if size > self.max_bytes:
                    raise CommandExecutionError("output_limit", "命令输出超过上限", output)
                chunk = decoder.decode(raw)
                output += chunk
                self.on_output(chunk)
                normalized = _ANSI.sub("", output).replace("\x00", "").replace("\r", "")
                if _INTERACTIVE.search(normalized):
                    raise CommandExecutionError(
                        "interaction_required",
                        "设备要求交互输入，已停止；不会自动确认或输入密码",
                        output,
                    )
                matches = list(self.prompt_pattern.finditer(normalized))
                if matches and not normalized[matches[-1].end() :].strip():
                    match = matches[-1]
                    candidate = (
                        time.monotonic(),
                        normalized[: match.start()],
                        match.group().strip(),
                    )
                else:
                    candidate = None
            elif candidate and (time.monotonic() - candidate[0] >= 0.1):
                self.last_prompt = candidate[2]
                return candidate[1]
            elif self.channel.closed or self.channel.exit_status_ready():
                if candidate:
                    self.last_prompt = candidate[2]
                    return candidate[1]
                raise CommandExecutionError("connection_closed", "设备连接已关闭", output)
            else:
                time.sleep(0.02)

    def initialize(self):
        output = self._read(15)
        prompt = self.last_prompt
        if prompt.startswith("<") and prompt.endswith(">"):
            name = re.escape(prompt[1:-1])
            pattern = rf"(?:<{name}>|\[[*~]?{name}(?:-[^\r\n\[\]]+)?\])"
        elif prompt.startswith("[") and prompt.endswith("]"):
            name = re.escape(prompt[1:-1].lstrip("*~"))
            pattern = rf"(?:<{name}>|\[[*~]?{name}(?:-[^\r\n\[\]]+)?\])"
        elif prompt[-1:] in {"#", ">"}:
            name = re.escape(prompt[:-1].split("(", 1)[0])
            pattern = rf"{name}(?:\([^\r\n]*\))?[#>]"
        else:
            pattern = re.escape(prompt)
        self.prompt_pattern = re.compile(rf"(?m)^{pattern} *$")
        return output

    def execute(self, command, *, command_id=None, timeout=30):
        if not self.usable:
            raise DeviceAccessError("connection_closed", "终端连接不可继续使用")
        self.guard()
        ticket = self.operation.before_command(command, command_id=command_id)
        sent = False
        try:
            self.operation.check_command(command)
            self.before_send()
            self.channel.settimeout(2)
            sent = True  # A failed send may have reached the remote peer in part.
            self.channel.sendall((command + "\n").encode("utf-8"))
            output = self._read(timeout)
            lines = output.splitlines(keepends=True)
            if lines and lines[0].strip() == command.strip():
                output = "".join(lines[1:])
            error = "设备返回命令错误" if _CLI_ERROR.search(output) else None
            status = "failed" if error else "succeeded"
            self.operation.after_command(
                ticket,
                output=output,
                status=status,
                failure_code="command_failed" if error else None,
            )
            return {
                "output": self.operation.redact(output),
                "status": status,
                "error": error,
                "output_complete": True,
            }
        except Exception as exc:
            self.usable = False
            exc.sent = sent
            self.operation.after_command(
                ticket,
                output=getattr(exc, "output", ""),
                status="failed" if sent else "not_executed",
                failure_code=getattr(exc, "failure_code", "connection_failed"),
                output_complete=False,
                sent=sent,
            )
            raise
