"""Line-by-line text comparison of a retained baseline and a live read; no parsing.

Both sides are cleaned the same way (escapes, carriage returns, trailing blanks,
the command echo and the closing prompt) and every remaining line is compared,
as collected: nothing is masked on either side.
"""

import difflib
import re

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
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_DIFF_LINES = 400


def comparable_lines(text: str, command: str = "") -> list[str]:
    """The output minus transport noise."""
    clean = _ANSI.sub("", str(text)).replace("\x00", "")
    lines = [line.rstrip() for line in re.split(r"\r*\n|\r", clean)]
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
    return lines


def unusable(text: str, command: str = "") -> str | None:
    """Why an output cannot be compared at all: an empty one is not "all removed"."""
    if _UNUSABLE.search(_ANSI.sub("", str(text))):
        return "cli_error_or_paging"
    if not comparable_lines(text, command):
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
    old, new = comparable_lines(before, command), comparable_lines(after, command)
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
    }
