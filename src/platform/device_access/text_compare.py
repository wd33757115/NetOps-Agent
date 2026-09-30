"""Line-by-line text comparison of a retained baseline and a live read; no parsing.

Both sides are cleaned the same way (escapes, carriage returns, trailing blanks,
the command echo and the closing prompt) and credential lines are masked before
comparison. Masked lines never count as changes: the two sides may use different
redaction markers, and a hidden value cannot be shown anyway.
"""

import difflib
import re

from .governance import redact as redact_ssh

_SENSITIVE = re.compile(
    r"\b(?:password|passwd|secret|community|authentication(?:-mode|-key)?|"
    r"key-string|keychain|key-chain|pre-shared-key|private-key|cipher|simple|"
    r"api[_-]?key|access[_-]?token|authorization)\b",
    re.I,
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
# A bare device prompt: Comware/VRP <name> or [name], Cisco-style name# / name>.
_PROMPT = re.compile(r"(?:<[^<>\s]{1,128}>|\[[^\[\]\s]{1,128}\]|[^\s<>\[\]#]{1,128}[#>])\s*")
_UNUSABLE = re.compile(
    r"(?im)^\s*(?:%\s*(?:invalid|error|unknown|incomplete|ambiguous|wrong|unrecognized)|"
    r"(?:unrecognized command|wrong parameter|incomplete command|error:|unknown command|"
    r"permission denied|authorization failed|insufficient privilege))|"
    r"--\s*more\s*--|---- More ----"
)
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@$")
_MASK = "[敏感配置已隐藏]"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_DIFF_LINES = 400


def redact_config(text: str) -> str:
    """Mask whole credential lines/private-key blocks, preserving source line numbers."""
    lines, in_key = [], False
    for raw_line in str(text).splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :]
        begins = bool(re.search(r"-----BEGIN .*PRIVATE KEY-----", line))
        ends = bool(re.search(r"-----END .*PRIVATE KEY-----", line))
        sensitive = in_key or begins or bool(_SENSITIVE.search(line)) or redact_ssh(line) != line
        lines.append(((re.match(r"\s*", line).group() + _MASK) if sensitive else line) + ending)
        in_key = (in_key or begins) and not ends
    return "".join(lines)


def comparable_lines(text: str, command: str = "") -> tuple[list[str], int]:
    """(visible lines, hidden line count): the output minus transport noise."""
    clean = _ANSI.sub("", str(text)).replace("\x00", "")
    lines = [line.rstrip() for line in re.split(r"\r*\n|\r", clean)]
    lines = redact_config("\n".join(lines)).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and command and " ".join(lines[0].split()).endswith(" ".join(command.split())):
        lines.pop(0)  # the command echo, with or without the prompt before it
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        # Collectors differ only in whether the whole output was stripped.
        lines[0] = lines[0].lstrip()
    while lines and (not lines[-1].strip() or _PROMPT.fullmatch(lines[-1])):
        lines.pop()
    visible = [line for line in lines if _MASK not in line and "[redacted" not in line]
    return visible, len(lines) - len(visible)


def unusable(text: str, command: str = "") -> str | None:
    """Why an output cannot be compared at all: an empty one is not "all removed"."""
    if _UNUSABLE.search(_ANSI.sub("", str(text))):
        return "cli_error_or_paging"
    visible, hidden = comparable_lines(text, command)
    if not visible and not hidden:
        return "empty_output"
    return None


def _with_sections(diff: list[str], old: list[str]) -> list[str]:
    """Like git's hunk context: name the section an indented hunk belongs to."""
    named = []
    for index, line in enumerate(diff):
        match = _HUNK.match(line)
        body = diff[index + 1] if index + 1 < len(diff) else ""
        if match and body[1:2].isspace():
            start = int(match.group(1))
            header = next(
                (
                    row
                    for row in reversed(old[: max(start - 1, 0) + 1])
                    if row and not row[0].isspace() and row.strip() != "#"
                ),
                None,
            )
            if header:
                line = f"{line} {header}"
        named.append(line)
    return named


def compare_text(before: str, after: str, *, command: str = "") -> dict:
    """Unified diff with one line of context; bounded, never silently empty."""
    (old, old_hidden), (new, new_hidden) = (
        comparable_lines(before, command),
        comparable_lines(after, command),
    )
    diff = list(difflib.unified_diff(old, new, "baseline", "current", n=1, lineterm=""))[2:]
    diff = _with_sections(diff, old)
    return {
        "status": "changed" if diff else "unchanged",
        "added_count": sum(line.startswith("+") for line in diff),
        "removed_count": sum(line.startswith("-") for line in diff),
        "baseline_lines": len(old),
        "current_lines": len(new),
        "diff": diff[:MAX_DIFF_LINES],
        "diff_truncated": len(diff) > MAX_DIFF_LINES,
        # Credential lines cannot be shown or compared; their count still can.
        "hidden_lines": {"baseline": old_hidden, "current": new_hidden},
        "quality_gaps": ["sensitive_lines_not_compared"] if old_hidden or new_hidden else [],
        "meaning": "逐行文本对比（+ 现场新增，- 基线有而现场没有）；敏感行已隐藏、不参与对比，"
        "只报告条数；差异不等于故障根因，基线不等于健康状态",
    }
