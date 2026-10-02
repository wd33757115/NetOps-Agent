"""Comparison of a retained baseline and a live read of the same command.

Both sides are cleaned the same way (escapes, carriage returns, trailing blanks,
the command echo and the closing prompt), as collected: nothing is masked. Tables
are compared entry by entry and summarised (text_table); every other line, such as
configuration, is compared as a unified diff. No parser is written per command.
"""

import difflib
import re

from . import text_table

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


def tabular(command: str) -> bool:
    """Configuration is text, compared by line; everything else may hold tables."""
    words = " ".join(command.lower().split())
    return not any(
        dump in words
        for dump in ("current-configuration", "saved-configuration", "running-config",
                     "startup-config", "display this")
    )


def compare_text(before: str, after: str, *, command: str = "") -> dict:
    """Tables by entry (text_table), all other lines as a unified diff; bounded."""
    old, new = comparable_lines(before, command), comparable_lines(after, command)
    pairs, rest_old, rest_new = (
        text_table.pair_all(old, new) if tabular(command) else ([], old, new)
    )
    tables = [table for table in (text_table.compare(a, b) for a, b in pairs) if table]
    diff = list(
        difflib.unified_diff(rest_old, rest_new, "baseline", "current", n=1, lineterm="")
    )[2:]
    diff = _with_sections(diff, rest_old)
    rows = {
        label: sum(table.get(label, {}).get("count", 0) for table in tables)
        for label in ("added", "removed", "changed")
    }
    return {
        "status": "changed" if diff or tables else "unchanged",
        # Table entries plus text lines; a table entry counts once however wide.
        "added_count": rows["added"] + sum(line.startswith("+") for line in diff),
        "removed_count": rows["removed"] + sum(line.startswith("-") for line in diff),
        "changed_rows": rows["changed"],
        "tables": tables,
        "unchanged_tables": len(pairs) - len(tables),
        "baseline_lines": len(old),
        "current_lines": len(new),
        "diff": diff[:MAX_DIFF_LINES],
        "diff_truncated": len(diff) > MAX_DIFF_LINES,
    }
