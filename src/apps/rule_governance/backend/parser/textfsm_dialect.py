"""Version-bound TextFSM dialect used by parser asset generation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

TEXTFSM_RUNTIME_VERSION = "2.1.0"
DIALECT_VERSION = "textfsm-2.1.0-platform-v3"
PROMPT_VERSION = "textfsm-writer-v6"


class TextFSMDialectError(RuntimeError):
    """Raised when runtime and generation dialect are not identical."""


@dataclass(frozen=True)
class DialectViolation:
    code: str
    message: str
    line_number: int | None = None
    evidence: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TextFSMDialectProfile:
    library: str = "textfsm"
    runtime_version: str = TEXTFSM_RUNTIME_VERSION
    dialect_version: str = DIALECT_VERSION
    prompt_version: str = PROMPT_VERSION
    allowed_value_options: tuple[str, ...] = ("Filldown", "List")
    allowed_actions: tuple[str, ...] = (
        "Record",
        "Clear",
        "Clearall",
        "StateName",
        "Record StateName",
        "Continue",
        "Continue.Record",
    )

    def canonical_payload(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_TEXTFSM_DIALECT = TextFSMDialectProfile()


def installed_textfsm_version() -> str:
    try:
        return importlib.metadata.version("textfsm")
    except importlib.metadata.PackageNotFoundError as exc:  # pragma: no cover
        raise TextFSMDialectError("textfsm is not installed") from exc


def assert_textfsm_runtime(
    profile: TextFSMDialectProfile = DEFAULT_TEXTFSM_DIALECT,
) -> str:
    installed = installed_textfsm_version()
    if installed != profile.runtime_version:
        raise TextFSMDialectError(
            "TextFSM runtime mismatch: "
            f"installed={installed}, required={profile.runtime_version}, "
            f"dialect={profile.dialect_version}"
        )
    return installed


def _prompt_path() -> Path:
    from src.apps.rule_governance.paths import RESOURCES

    return RESOURCES / "prompts" / "textfsm-2.1.0.md"


def load_dialect_prompt(
    profile: TextFSMDialectProfile = DEFAULT_TEXTFSM_DIALECT,
) -> str:
    text = _prompt_path().read_text(encoding="utf-8").strip()
    return (
        text.replace("{{TEXTFSM_VERSION}}", profile.runtime_version)
        .replace("{{DIALECT_VERSION}}", profile.dialect_version)
        .replace("{{PROMPT_VERSION}}", profile.prompt_version)
    )


def dialect_hash(
    profile: TextFSMDialectProfile = DEFAULT_TEXTFSM_DIALECT,
) -> str:
    payload = {
        "profile": profile.canonical_payload(),
        "system_prompt": load_dialect_prompt(profile),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_textfsm_system_prompt(
    profile: TextFSMDialectProfile = DEFAULT_TEXTFSM_DIALECT,
) -> str:
    assert_textfsm_runtime(profile)
    return f"Dialect hash: {dialect_hash(profile)}\n{load_dialect_prompt(profile)}"


def _value_options(line: str) -> tuple[str, ...] | None:
    prefix, separator, _regex = line.partition("(")
    if not separator:
        return None
    tokens = prefix.split()
    if len(tokens) == 2 and tokens[0] == "Value":
        return ()
    if len(tokens) == 3 and tokens[0] == "Value":
        return tuple(item for item in tokens[1].split(",") if item)
    return None


def _lint_action(action: str) -> str | None:
    tokens = action.split()
    if not tokens:
        return None
    if len(tokens) == 1:
        token = tokens[0]
        if token in {"Record", "Clear", "Clearall", "Continue", "Continue.Record"}:
            return None
        if token in {"Next", "Next.Record", "NoRecord", "Error"}:
            return f"action {token!r} is outside the platform dialect"
        if re.fullmatch(r"[A-Za-z_]\w*", token):
            return None
        return f"unsupported TextFSM actions: {action!r}"
    if len(tokens) == 2 and tokens[0] == "Record" and re.fullmatch(r"[A-Za-z_]\w*", tokens[1]):
        return None
    return f"unsupported TextFSM actions: {action!r}"


def lint_textfsm_template(
    template_text: str,
    *,
    profile: TextFSMDialectProfile = DEFAULT_TEXTFSM_DIALECT,
    check_runtime: bool = True,
) -> tuple[DialectViolation, ...]:
    """Compile and enforce the deliberately small generation dialect."""
    if check_runtime:
        try:
            assert_textfsm_runtime(profile)
        except TextFSMDialectError as exc:
            return (DialectViolation("dialect_version_mismatch", str(exc)),)

    violations: list[DialectViolation] = []
    lines = str(template_text or "").splitlines()
    has_filldown = False
    has_explicit_record = False
    state_names: list[str] = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("Value "):
            options = _value_options(stripped)
            if options is None:
                violations.append(
                    DialectViolation(
                        "template_syntax_error",
                        "invalid Value declaration",
                        line_number,
                        stripped,
                    )
                )
                continue
            option_set = set(options)
            if not option_set.issubset(set(profile.allowed_value_options)):
                violations.append(
                    DialectViolation(
                        "unsupported_platform_construct",
                        f"unsupported Value options: {','.join(options)}",
                        line_number,
                        stripped,
                    )
                )
            has_filldown = has_filldown or "Filldown" in option_set
            continue

        if line and not line[0].isspace() and re.fullmatch(r"\w+", stripped):
            state_names.append(stripped)

        if "->" not in line or not stripped.startswith("^"):
            continue
        action = line.rsplit("->", 1)[1].strip()
        if re.search(r"(?:^|\.)Record(?:\s|$)", action):
            has_explicit_record = True
        action_error = _lint_action(action)
        if action_error:
            violations.append(
                DialectViolation(
                    "unsupported_platform_construct",
                    action_error,
                    line_number,
                    stripped,
                )
            )

    non_terminal_states = [name for name in state_names if name not in {"EOF", "End"}]
    if not has_explicit_record:
        violations.append(
            DialectViolation(
                "implicit_eof_record",
                "template must explicitly Record every entity",
            )
        )
    if (has_filldown or len(non_terminal_states) > 1) and "EOF" not in state_names:
        violations.append(
            DialectViolation(
                "implicit_eof_record",
                "Filldown and multi-state templates require an explicit empty EOF state",
            )
        )

    try:
        import textfsm

        textfsm.TextFSM(io.StringIO(template_text))
    except Exception as exc:
        violations.append(DialectViolation("template_syntax_error", f"compile failed: {exc}"))

    unique: dict[tuple[object, ...], DialectViolation] = {}
    for item in violations:
        unique[(item.code, item.message, item.line_number, item.evidence)] = item
    return tuple(unique.values())
