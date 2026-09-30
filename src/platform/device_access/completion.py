"""Bounded Comware editing replies, never a generic shell completion client.

H3C CLI documentation distinguishes configurable Ctrl+U from editing keys:
https://www.h3c.com/en/d_202004/1283136_294551_0.htm
Only the older, explicitly system-reserved Ctrl+E/X/R bindings are accepted.
"""

import re
import time

from .contracts import DeviceAccessError

HOTKEY_COMMAND = "display hotkey | include (hotkeys|CTRL_[EXR])"
MAX_DRAFT = 256
MAX_LINE = 511
MAX_REPLY = 16 * 1024
EDIT_CHARACTERS = re.compile(r"[a-zA-Z0-9 _./:=-]+", re.ASCII)
CLEAR_AND_REDRAW = b"\x05\x18\x12"  # End, erase left, redraw; never Ctrl+U or Enter.
_FUNCTIONS = {
    "CTRL_E": "Move the cursor to the end of the line.",
    "CTRL_R": "Redisplay the current line.",
    "CTRL_X": "Delete all characters from the beginning of the line to the cursor.",
}


def platform(channel, prompt):
    try:
        banner = channel.get_transport().remote_version
    except (AttributeError, TypeError):
        return None
    if (
        isinstance(banner, str)
        and re.fullmatch(r"SSH-2\.0-Comware-7\.[\w.()-]+", banner)
        and isinstance(prompt, str)
        and prompt.isascii()
        and re.fullmatch(r"(?:<[^<>\[\]\r\n]{1,128}>|\[[^<>\[\]\r\n]{1,128}\])", prompt)
    ):
        return "comware"
    return None


def validate_draft(command, *, maximum=MAX_DRAFT):
    if (
        not isinstance(command, str)
        or not 0 < len(command) <= maximum
        or not command.strip()
        or not EDIT_CHARACTERS.fullmatch(command)
    ):
        raise DeviceAccessError("invalid_completion", "Tab 补全仅支持简短的单行 ASCII 设备命令")


def reserved_editing_keys(output):
    """Do not accept mutable 'Defined function hotkeys' with matching values."""
    reserved, found = False, set()
    for raw in output.splitlines():
        line = " ".join(raw.split())
        if re.fullmatch(r"-.*hotkeys-", line, re.IGNORECASE):
            reserved = line.casefold() == "-system-reserved hotkeys-"
        match = re.fullmatch(r"(CTRL_[EXR])\s+(.+)", line)
        if match:
            key, function = match.groups()
            if not reserved or key in found or function != _FUNCTIONS[key]:
                return False
            found.add(key)
    return found == set(_FUNCTIONS)


def redrawn_line(raw):
    """Accept a fresh full-line redraw, not an inferred remote edit buffer.

    Verified Comware replies use CR CR LF + prompt after Tab and Ctrl+R.
    Cursor-left/erase controls before that redraw are discarded; no terminal
    emulation is needed and an unfamiliar control sequence fails closed.
    """
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        raise DeviceAccessError("completion_ambiguous", "补全回显不是已验证的 ASCII 格式") from None
    if "\n" not in value:
        raise DeviceAccessError("completion_ambiguous", "设备补全回显无法确认，已停止连接")
    prefix, line = value.rsplit("\n", 1)
    prefix = re.sub(r"\x1b\[(?:[0-9]{1,3}D|[012]?[JK]|[0-9;]*m)", "", prefix)
    if re.search(r"[^\x07\x08\r\n\x20-\x7e]", prefix):
        raise DeviceAccessError("completion_ambiguous", "设备补全回显无法确认，已停止连接")
    if any(ord(character) < 32 for character in line):
        raise DeviceAccessError("completion_ambiguous", "设备未返回完整的新输入行")
    return line


def read_edit(channel, guard, *, deadline, on_received):
    chunks, received, last_received = [], 0, None
    while True:
        guard()
        now = time.monotonic()
        if now >= deadline:
            raise DeviceAccessError("completion_timeout", "等待补全回显超时，已停止连接")
        if channel.recv_ready():
            chunk = channel.recv(min(8192, MAX_REPLY - received + 1))
            if not chunk:
                raise DeviceAccessError("connection_closed", "设备连接已关闭")
            received += len(chunk)
            on_received(len(chunk))
            if received > MAX_REPLY:
                raise DeviceAccessError("output_limit", "补全回显超过上限")
            chunks.append(chunk)
            last_received = now
        elif channel.closed or channel.exit_status_ready():
            raise DeviceAccessError("connection_closed", "设备连接已关闭")
        elif last_received is not None and now - last_received >= 0.15:
            return redrawn_line(b"".join(chunks))
        else:
            time.sleep(0.01)
