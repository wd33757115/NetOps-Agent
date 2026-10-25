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


def _is_session_setup_command(command: str) -> bool:
    return command.strip().lower().startswith(("terminal", "screen-length"))


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


def _prompt_markers(lines: list[str]) -> list[tuple[int, str, str, str]]:
    markers: list[tuple[int, str, str, str]] = []
    for idx, line in enumerate(lines):
        parsed = _parse_prompt(line.strip())
        if not parsed:
            continue
        prompt_device, command, style = parsed
        if _looks_like_command(command):
            markers.append((idx, prompt_device, command, style))
    return markers


def _split_prompt_lines(
    lines: list[str],
    *,
    device_name: str | None,
    line_offset: int = 0,
) -> list[CommandBlock]:
    markers = _prompt_markers(lines)
    fallback_device = device_name or (markers[0][1] if markers else None)
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
                start_line=line_offset + line_idx + 1,
                end_line=line_offset + next_idx,
                prompt_style=style,
            )
        )
    return blocks


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
        body_start = line_idx + 1
        body_end = next_idx
        while body_start < body_end and (
            not lines[body_start].strip() or REPORT_OUTPUT_RE.match(lines[body_start])
        ):
            body_start += 1
        while body_end > body_start and (
            not lines[body_end - 1].strip() or SEPARATOR_RE.match(lines[body_end - 1])
        ):
            body_end -= 1
        body_lines = lines[body_start:body_end]

        # Some field-export reports wrap a raw terminal transcript inside the
        # output of the next labelled command. In that case the device prompts
        # are the authoritative command boundaries. Keep any real prefix as the
        # labelled command output, then split the embedded transcript normally.
        # Configuration exports own their entire labelled body. Prompt-like
        # lines inside them must not manufacture separate operational samples.
        configuration = canonicalize_command(command) in {
            "show running-config", "show startup-config",
            "display current-configuration", "display saved-configuration",
        }
        embedded_markers = [] if configuration else _prompt_markers(body_lines)
        if embedded_markers:
            prefix_end = embedded_markers[0][0]
            prefix_body = _clean_body(body_lines[:prefix_end])
            if prefix_body and _looks_like_command(command):
                blocks.append(
                    CommandBlock(
                        device_name=device_name or "unknown",
                        command=command,
                        command_canonical=canonicalize_command(command),
                        raw_output=prefix_body,
                        start_line=body_start + 1,
                        end_line=body_start + prefix_end,
                        prompt_style="command_report",
                    )
                )
            blocks.extend(
                _split_prompt_lines(
                    body_lines[prefix_end:],
                    device_name=device_name,
                    line_offset=body_start + prefix_end,
                )
            )
            continue

        body = "\n".join(body_lines).strip()
        # Report markers prove the command was executed; preserve a zero-line body so
        # OutputClassifier can distinguish valid empty from unsupported or uncertain.
        if not _looks_like_command(command) or (
            not body and _is_session_setup_command(command)
        ):
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

    # A later embedded transcript can supply the real output for an earlier
    # empty report placeholder. Once non-empty evidence exists, retaining that
    # placeholder would incorrectly classify the same command as empty.
    commands_with_output = {
        block.command_canonical for block in blocks if block.raw_output.strip()
    }
    return [
        block
        for block in blocks
        if block.raw_output.strip()
        or block.command_canonical not in commands_with_output
    ]


def split_cli_capture(text: str, *, device_name: str | None = None) -> list[CommandBlock]:
    """Return command blocks from a raw terminal capture.

    H3C command completion often emits consecutive prompt lines such as
    ``dis`` -> ``display ver`` -> ``display version``. Blocks with no real
    command output are ignored, leaving the final expanded command.
    """
    if any(REPORT_COMMAND_RE.match(line.replace("\x00", "")) for line in text.splitlines()):
        return _split_command_report(text, device_name)

    lines = [line.replace("\x00", "") for line in text.splitlines()]
    return _split_prompt_lines(lines, device_name=device_name)


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
