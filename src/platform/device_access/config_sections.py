"""Small, deterministic Comware configuration scopes; no device or database I/O.

Command syntax: H3C Comware 7 Fundamentals Command Reference, configuration file
management (https://www.h3c.com/en/d_202210/1709582_294551_0.htm); OSPF module example:
https://wwwsg.h3c.com/en/d_202409/2261619_294551_0.htm .
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.patrol.config_diff_bridge import compare_config_texts

from .governance import redact as redact_ssh

_INTERFACE = re.compile(
    r"(?:GigabitEthernet|Ten-GigabitEthernet|TenGigabitEthernet|FortyGigE|HundredGigE|"
    r"M-GigabitEthernet|Ethernet)[0-9]+(?:/[0-9]+){1,3}(?:\.[0-9]+)?|"
    r"(?:Vlan-interface|LoopBack|Bridge-Aggregation|Route-Aggregation)[0-9]+(?:\.[0-9]+)?"
)
_SENSITIVE = re.compile(
    r"\b(?:password|passwd|secret|community|authentication(?:-mode|-key)?|"
    r"key-string|keychain|key-chain|pre-shared-key|private-key|cipher|simple|"
    r"api[_-]?key|access[_-]?token|authorization)\b",
    re.I,
)
_ERROR = re.compile(
    r"(?im)^\s*(?:%\s*(?:invalid|error|unknown|incomplete|ambiguous|wrong|unrecognized)|"
    r"(?:unrecognized command|wrong parameter|incomplete command|error:|unknown command))|"
    r"--\s*more\s*--|---- More ----|\[.*(?:truncated|截断).*\]|"
    r"(?im:^\s*(?:permission denied|authorization failed|insufficient privilege|"
    r"command execution failed|"
    r"output (?:was )?truncated|press (?:any key|space|enter) to continue))"
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_MASK = "[敏感配置已隐藏]"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_FRAGMENT_BYTES = 6000
MAX_FRAGMENT_LINES = 60


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Literal["comware"]
    section: Literal["interface", "ospf"]
    target: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def supported_target(self):
        if self.section == "interface":
            if not _INTERFACE.fullmatch(self.target):
                raise ValueError(
                    "Use a supported complete Comware interface name, not an abbreviation"
                )
        elif not re.fullmatch(r"[1-9][0-9]{0,4}", self.target) or int(self.target) > 65535:
            raise ValueError("OSPF process ID must be 1 through 65535")
        return self


def build_command(scope: Scope) -> str:
    scope = Scope.model_validate(scope.model_dump())
    if scope.section == "interface":
        return f"display current-configuration interface {scope.target}"
    return "display current-configuration configuration ospf"


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


def _size(value):
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode("utf-8")
    )


def _source_command_matches(command, scope):
    normalized = " ".join(str(command).split()).casefold()
    expected = build_command(scope).casefold()
    # Some collectors retain the type/number separator accepted by Comware.
    separated = re.sub(r"([a-z-])([0-9])", r"\1 \2", expected, count=1)
    return normalized in {"display current-configuration", expected, separated}


def extract_section(
    text: str, scope: Scope, *, source_complete: bool, command: str | None = None
) -> dict:
    """Select exactly one closed block. Missing/ambiguous/incomplete is never empty-equal."""
    scope = Scope.model_validate(scope.model_dump())
    result = {
        **scope.model_dump(),
        "text": "",
        "lines": [],
        "start_line": None,
        "end_line": None,
        "complete": False,
        "redacted": False,
        "quality_gaps": [],
        "truncated": False,
    }
    gaps = result["quality_gaps"]
    if not source_complete:
        gaps.append("source_incomplete")
    if command is not None and not _source_command_matches(command, scope):
        gaps.append("source_command_not_supported_for_scope")
    if len(str(text).encode("utf-8")) > MAX_SOURCE_BYTES:
        gaps.append("source_too_large")
        result["truncated"] = True
        return result
    clean = _ANSI.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    if _ERROR.search(clean) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", clean):
        gaps.append("source_cli_error_paging_or_control")
        return result
    safe = redact_config(clean)
    original_lines, lines = clean.splitlines(), safe.splitlines()
    if scope.section == "interface":
        pattern = re.compile(r"^interface " + re.escape(scope.target) + r"\s*$", re.I)
    else:
        pattern = re.compile(r"^ospf " + re.escape(scope.target) + r"(?:\s|$)", re.I)
    starts = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(starts) != 1:
        gaps.append("section_missing" if not starts else "section_not_unique")
        return result
    start = starts[0]
    if scope.section == "ospf" and not re.fullmatch(
        r"ospf " + scope.target + r"(?: router-id (?:[0-9]{1,3}\.){3}[0-9]{1,3})?\s*",
        lines[start],
        re.I,
    ):
        gaps.append("ospf_process_context_not_supported")
        return result
    end = None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() == "#":
            end = index
            break
        if line and not line[0].isspace() and not line.startswith(("[redacted", _MASK)):
            gaps.append("section_boundary_invalid")
            break
    if end is None:
        gaps.append("section_not_closed")
        return result
    selected = lines[start:end]
    result["start_line"], result["end_line"] = start + 1, end
    result["redacted"] = selected != original_lines[start:end] or any(
        _MASK in line or "[redacted" in line for line in selected
    )
    # Redact before taking a bounded prefix; never split a secret across pages.
    for index, line in enumerate(selected):
        result["lines"].append({"line": start + index + 1, "text": line})
        result["text"] = "\n".join(row["text"] for row in result["lines"])
        if len(result["lines"]) > MAX_FRAGMENT_LINES or _size(result) > MAX_FRAGMENT_BYTES - 200:
            result["lines"].pop()
            result["text"] = "\n".join(row["text"] for row in result["lines"])
            result["truncated"] = True
            gaps.append("section_excerpt_truncated")
            break
    result["complete"] = not gaps
    if result["redacted"]:
        gaps.append("sensitive_lines_omitted")
    return result


def compare_sections(before: dict, after: dict) -> dict:
    """Reuse the existing config-diff tool only on complete same-scope fragments."""
    fields = ("platform", "section", "target")
    result = {
        "scope": {key: before.get(key) for key in fields},
        "status": "unavailable",
        "added": [],
        "removed": [],
        "added_count": 0,
        "removed_count": 0,
        "quality_gaps": [],
        "meaning": "仅比较所选配置块的可见文本；文本变化不是故障根因证明",
    }
    gaps = result["quality_gaps"]
    if any(before.get(key) != after.get(key) for key in fields):
        gaps.append("configuration_scope_mismatch")
    if (
        not before.get("complete")
        or not after.get("complete")
        or before.get("truncated")
        or after.get("truncated")
    ):
        gaps.append("incomplete_sections_cannot_compare")
    if not before.get("text") or not after.get("text"):
        gaps.append("missing_sections_cannot_mean_addition_or_deletion")
    if gaps:
        return result

    # SSH audit and retained baselines can use different redaction markers and
    # indentation. Hidden lines are outside the comparison, not configuration edits.
    def visible(text):
        return "\n".join(
            line for line in text.splitlines() if _MASK not in line and "[redacted" not in line
        )

    comparison = compare_config_texts(
        visible(before["text"]), visible(after["text"]), device_type="comware"
    )
    if not comparison.get("success"):
        gaps.append("config_diff_failed")
        return result
    data = comparison["detail"]
    result.update({key: data[key] for key in ("added", "removed", "added_count", "removed_count")})
    changed = bool(data["added_count"] or data["removed_count"])
    result["status"] = "changed" if changed else "unchanged"
    if before.get("redacted") or after.get("redacted"):
        gaps.append("sensitive_lines_not_compared")
        if not changed:
            result["status"] = "unavailable"
    return result
