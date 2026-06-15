# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Split raw network-device CLI captures into command output blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

H3C_PROMPT_RE = re.compile(r"^<(?P<device>[^>]+)>\s*(?P<command>.*)$")
CISCO_PROMPT_RE = re.compile(r"^(?P<device>[A-Za-z0-9_.:/-]+)#\s*(?P<command>.*)$")
BEGIN_RE = re.compile(r"^\[BEGIN\]\s*(?P<ts>.+)$", re.IGNORECASE)
REPORT_COMMAND_RE = re.compile(r"^\s*命令\s*[:：]\s*(?P<command>.+?)\s*$", re.IGNORECASE)
REPORT_OUTPUT_RE = re.compile(r"^\s*输出\s*[:：]\s*$", re.IGNORECASE)
REPORT_COMMAND_RE = re.compile(
    r"^\s*(?:命令|鍛戒护|Command)\s*[:：]\s*(?P<command>.+?)\s*$",
    re.IGNORECASE,
)
REPORT_OUTPUT_RE = re.compile(
    r"^\s*(?:输出|杈撳嚭|Output)\s*[:：]\s*$",
    re.IGNORECASE,
)
SEPARATOR_RE = re.compile(r"^\s*-{10,}\s*$")


@dataclass(frozen=True)
class CommandBlock:
    device_name: str
    command: str
    command_canonical: str
    raw_output: str
    start_line: int
    end_line: int
    prompt_style: str


@dataclass(frozen=True)
class RawCaptureMetadata:
    device_name: str | None
    observed_at_text: str | None
    prompt_style: str | None


def canonicalize_command(command: str) -> str:
    """Normalize command whitespace while preserving pipes and arguments."""
    normalized = re.sub(r"\s+", " ", command.strip()).lower()
    if normalized.startswith("dis "):
        normalized = f"display {normalized[4:]}"
    return normalized


def _parse_prompt(line: str) -> tuple[str, str, str] | None:
    h3c = H3C_PROMPT_RE.match(line)
    if h3c:
        return h3c.group("device"), h3c.group("command").strip(), "h3c_angle"
    cisco = CISCO_PROMPT_RE.match(line)
    if cisco:
        return cisco.group("device"), cisco.group("command").strip(), "cisco_hash"
    return None


def _looks_like_command(command: str) -> bool:
    cmd = command.strip().lower()
    if not cmd:
        return False
    if cmd in {"quit", "exit"}:
        return True
    return cmd.startswith(("display", "dis ", "show", "terminal", "screen-length"))


def _clean_body(lines: list[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        if BEGIN_RE.match(line.strip()):
            continue
        parsed = _parse_prompt(line.strip())
        if parsed and not parsed[1]:
            continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def inspect_raw_capture(text: str) -> RawCaptureMetadata:
    observed_at_text = None
    device_name = None
    prompt_style = None
    for line in text.splitlines():
        report = REPORT_COMMAND_RE.match(line.replace("\x00", ""))
        if report:
            return RawCaptureMetadata(
                device_name=None,
                observed_at_text=observed_at_text,
                prompt_style="command_report",
            )
        if observed_at_text is None:
            begin = BEGIN_RE.match(line.strip())
            if begin:
                observed_at_text = begin.group("ts").strip()
        parsed = _parse_prompt(line.strip())
        if parsed:
            device_name, _command, prompt_style = parsed
            break
    return RawCaptureMetadata(
        device_name=device_name,
        observed_at_text=observed_at_text,
        prompt_style=prompt_style,
    )


def _split_command_report(text: str, device_name: str | None) -> list[CommandBlock]:
    lines = [line.replace("\x00", "") for line in text.splitlines()]
    markers: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = REPORT_COMMAND_RE.match(line)
        if match:
            markers.append((idx, match.group("command").strip()))

    blocks: list[CommandBlock] = []
    for pos, (line_idx, command) in enumerate(markers):
        next_idx = markers[pos + 1][0] if pos + 1 < len(markers) else len(lines)
        body_lines = lines[line_idx + 1 : next_idx]
        while body_lines and (
            not body_lines[0].strip() or REPORT_OUTPUT_RE.match(body_lines[0])
        ):
            body_lines.pop(0)
        while body_lines and (
            not body_lines[-1].strip() or SEPARATOR_RE.match(body_lines[-1])
        ):
            body_lines.pop()
        body = "\n".join(body_lines).strip()
        if not body or not _looks_like_command(command):
            continue
        blocks.append(
            CommandBlock(
                device_name=device_name or "unknown",
                command=command,
                command_canonical=canonicalize_command(command),
                raw_output=body,
                start_line=line_idx + 1,
                end_line=next_idx,
                prompt_style="command_report",
            )
        )
    return blocks


def split_cli_capture(text: str, *, device_name: str | None = None) -> list[CommandBlock]:
    """Return command blocks from a raw terminal capture.

    H3C command completion often emits consecutive prompt lines such as
    ``dis`` -> ``display ver`` -> ``display version``. Blocks with no real
    command output are ignored, leaving the final expanded command.
    """
    if any(REPORT_COMMAND_RE.match(line.replace("\x00", "")) for line in text.splitlines()):
        return _split_command_report(text, device_name)

    lines = [line.replace("\x00", "") for line in text.splitlines()]
    markers: list[tuple[int, str, str, str]] = []
    fallback_device = device_name
    for idx, line in enumerate(lines):
        parsed = _parse_prompt(line.strip())
        if not parsed:
            continue
        prompt_device, command, style = parsed
        fallback_device = fallback_device or prompt_device
        if _looks_like_command(command):
            markers.append((idx, prompt_device, command, style))

    blocks: list[CommandBlock] = []
    for pos, (line_idx, prompt_device, command, style) in enumerate(markers):
        next_idx = markers[pos + 1][0] if pos + 1 < len(markers) else len(lines)
        body = _clean_body(lines[line_idx + 1 : next_idx])
        if not body:
            continue
        blocks.append(
            CommandBlock(
                device_name=fallback_device or prompt_device,
                command=command.strip(),
                command_canonical=canonicalize_command(command),
                raw_output=body,
                start_line=line_idx + 1,
                end_line=next_idx,
                prompt_style=style,
            )
        )
    return blocks


def infer_device_from_filename(path: str | Path) -> tuple[str | None, str | None]:
    """Infer ``(device_name, ip)`` from patrol file names used in field exports."""
    stem = Path(path).stem
    match = re.match(
        r"^(?P<device>.+?)[-_](?P<ip>\d{1,3}(?:\.\d{1,3}){3})"
        r"(?:_\d{4}-\d{2}-\d{2}_.*)?$",
        stem,
    )
    if not match:
        return None, None
    return match.group("device"), match.group("ip")
