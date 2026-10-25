"""Version 1 of the in-process rule-consumption contract.

Inspection authorizes its own caller before using these read operations. Reading
published assets or a previously frozen version does not require admission to the
rule-authoring App. This module deliberately exposes no approval, publication,
generation or lifecycle operations. It is not an untrusted-code sandbox.

Existing serialized bundles and their digest algorithms remain unchanged. A new
publication only changes future selection; historical runs resolve their exact
version IDs, including deprecated versions, and validate the stored digests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from .backend.parser.identity import DeviceIdentity, DeviceIdentityResolver
from .backend.parser.runtime_bundle import ParserRuntimeBundle, ParserTemplateRef
from .backend.parser.runtime_repository import ParserRuntimeRepository

CONTRACT_VERSION = 1

__all__ = [
    "CONTRACT_VERSION",
    "CheckItemRuntimeReader",
    "CheckItemSelectionReader",
    "ParsedRows",
    "Selection",
    "SelectionError",
    "DeviceIdentity",
    "DeviceIdentityResolver",
    "ParserRuntimeBundle",
    "ParserTemplateRef",
    "ParserRuntimeRepository",
    "classify_output",
]


class CheckItemRuntimeReader:
    """Read exact asset versions without exposing the authoring service API."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> None:
        from .backend.check_items.runtime_bundle import CheckItemRuntimeBundleService

        self._service = (
            CheckItemRuntimeBundleService(session_scope=session_scope)
            if session_scope is not None
            else CheckItemRuntimeBundleService()
        )

    def get_active(
        self, *, check_item_version_id: str, implementation_version_id: str
    ) -> dict[str, Any] | None:
        return self._service.get_active(
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
        )

    def get_active_many(
        self, pairs: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self._service.get_active_many(pairs)

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        return self._service.get_version(version_id)

    def get_rule_assets(self, version_id: str) -> dict[str, Any] | None:
        return self._service.get_rule_assets(version_id)


class SelectionError(ValueError):
    """The published implementation cannot serve its check item (e.g. Value mismatch)."""


@dataclass(frozen=True)
class Selection:
    """The published way to collect one check item from one device."""

    check_item_id: str
    check_item_version_id: str
    implementation_id: str
    implementation_version_id: str
    command: str
    scoped_command: str | None  # per-instance form; "{vrf}" is filled by the consumer
    fields: dict[str, dict[str, Any]]  # same-name Value -> {value_type, required, identity}
    template_ref: str
    template_text: str
    output_quality_policy: dict[str, Any]
    template_sha256: str = ""
    # The line by which a per-instance output names its instance (first group), if any.
    scope_header: str | None = None
    # {Value: unit} for Values the device prints as bare numbers ("Age (min)").
    units: dict[str, str] = field(default_factory=dict)
    # {Value: value} for Values the device never prints because they are fixed for the
    # platform (an IOS ACL's implicit deny); a row that has the Value keeps its own.
    defaults: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedRows:
    """``status``: complete (rows may be empty), partial, unsupported or failed.

    ``lines[i]`` are the 1-based input lines row i was read from: the lines since the
    previous row up to the one that emitted it, without rejected lines. A line the
    template rejects (its ``Error`` action) is listed in ``rejected`` and parsing goes on
    with the next line (``rejected_reasons``: the template's message for it, as a slug).
    ``problems`` are output-quality findings: a required line that
    is missing, or a count the output advertises that differs from what was read.
    ``incomplete_output``: the output itself was cut off (paging, truncation).

    Any rejected line, problem or cut-off output makes the result partial; without a
    single row it is failed. Whether a partial table is still usable is the consumer's
    decision.
    """

    status: str
    rows: list[dict[str, Any]]
    reason: str = ""
    lines: list[list[int]] = ()
    rejected: tuple[int, ...] = ()
    rejected_reasons: tuple[str, ...] = ()  # the template's Error message per rejected line
    problems: tuple[str, ...] = ()
    incomplete_output: bool = False


class CheckItemSelectionReader:
    """Select and parse published check items for one device; read-only.

    The implementation is chosen by the catalog resolver (model, then family, platform
    and vendor); its command's template must emit exactly the check item's same-name
    Values (all required ones, nothing outside the contract). Output is classified with
    the implementation's output-quality policy before the template runs.
    """

    def __init__(
        self,
        catalog_loader: Callable[[], Any] | None = None,
        *,
        source: str = "published",
    ) -> None:
        """``source``: "published" reads the active catalog; "shipped" reads the catalog
        assets installed with the App (offline tools and tests)."""
        if source not in {"published", "shipped"}:
            raise ValueError("source must be published or shipped")
        self._catalog_loader = catalog_loader or (
            _active_catalog if source == "published" else _shipped_catalog
        )
        self._catalog = None

    def _resolved_catalog(self):
        if self._catalog is None:
            self._catalog = self._catalog_loader()
        return self._catalog

    def select(self, check_item_ids: Iterable[str], device: dict[str, Any]) -> dict[str, Selection]:
        from src.core.check_items.resolver import CheckItemImplementationResolver

        catalog = self._resolved_catalog()
        resolver = CheckItemImplementationResolver(catalog)
        selected = {}
        for check_item_id in check_item_ids:
            decision = resolver.resolve(device=device, check_item_id=check_item_id)
            if decision.status != "resolved" or not decision.implementation_id:
                continue
            implementation = next(
                item for item in catalog.implementations if item.id == decision.implementation_id
            )
            check_item = catalog.check_item(check_item_id)
            selected[check_item_id] = _selection(check_item, implementation)
        return selected

    def commands(self, check_item_ids: Iterable[str]) -> list[dict[str, Any]]:
        """What every active implementation of these check items runs, for a consumer's
        exact command grants: ``{implementation_id, vendor, platforms, command,
        scoped_command}``. ``vendor`` and ``platforms`` are the implementation's match
        (empty platforms: any platform)."""
        from .backend.check_items.system_items import templates

        wanted = set(check_item_ids)
        bound = templates()
        return [
            {
                "implementation_id": implementation.id,
                "vendor": implementation.match.get("vendor"),
                "platforms": tuple(implementation.match.get("platforms") or ()),
                "command": implementation.commands[0].command,
                "scoped_command": (bound.get(implementation.id) or {}).get("scoped_command"),
            }
            for implementation in self._resolved_catalog().implementations
            if implementation.status == "active"
            and wanted.intersection(implementation.check_item_ids)
            and implementation.commands
        ]

    def parse(self, selection: Selection, raw_text: str) -> ParsedRows:
        import io
        from types import SimpleNamespace

        import textfsm

        from .backend.parser.output_classifier import OutputClassifier

        classified = OutputClassifier().classify(
            raw_text,
            decision=SimpleNamespace(canonical_command=selection.command, empty_semantics={}),
            profile=selection.output_quality_policy,
        )
        if classified.status == "unsupported_command":
            return ParsedRows("unsupported", [], classified.reason)
        if classified.status in {"command_error", "empty_unverified"}:
            return ParsedRows("failed", [], classified.status)
        if classified.status == "valid_empty":
            return ParsedRows("complete", [], classified.reason)
        fsm = textfsm.TextFSM(io.StringIO(selection.template_text))
        emitted = fsm.ParseText("", eof=False)  # the FSM appends its records to this list
        rows, lines, rejected, reasons, pending, seen = [], [], [], [], [], 0
        text = raw_text.splitlines()

        # Scalar Values hold what the FSM has read so far (a List Value only its last item).
        scalar = [i for i, value in enumerate(fsm.values) if "List" not in value.OptionNames()]

        def values():
            return ["" if value.value is None else value.value for value in fsm.values]

        def take(number, before, *, read=True, end=False):
            """Give each emitted row the lines that assigned its Values.

            ``read``: the line was read (not rejected, not the end of the output). At the
            ``end``, a row no line assigned anything to only carries Filldown Values over
            from the rows before it; it is not a row of its own.
            """
            nonlocal pending, seen
            if end and not pending and rows:
                del emitted[seen:]
            new, assigned = emitted[seen:], read and values() != before
            # A row emitted when the next one starts ("Continue.Record") holds only what
            # was read before this line; the line's Values belong to the next row.
            owns = read and any(
                [row[i] for i in scalar] != [before[i] for i in scalar] for row in new
            )
            for row in new:
                rows.append(
                    {name: value for name, value in zip(fsm.header, row) if value not in ("", [])}
                )
                lines.append(pending + ([number] if owns else []))
            if new:
                pending = []
            pending += [number] if assigned and not owns else []
            seen = len(emitted)

        problems = []
        for number, line in enumerate(text, 1):
            before = values()
            try:
                fsm.ParseText(line + "\n", eof=False)
            except textfsm.TextFSMError as exc:
                rejected.append(number)  # the FSM keeps its state; the next line goes on
                reasons.append(_rejection(exc))
                take(number, before, read=False)
                continue
            take(number, before)
        before = values()
        try:
            fsm.ParseText("", eof=True)
        except textfsm.TextFSMError as exc:
            problems.append(f"template_rejected_end_of_output: {exc}")
        take(len(text), before, read=False, end=True)
        problems.extend(_policy_problems(selection, raw_text, rows))
        incomplete = classified.status in {"truncated", "paged"}
        if not (rejected or problems or incomplete):
            status = "complete"
        else:
            status = "partial" if rows else "failed"
        return ParsedRows(
            status,
            rows,
            classified.status,
            lines,
            rejected=tuple(rejected),
            rejected_reasons=tuple(reasons),
            problems=tuple(problems),
            incomplete_output=incomplete,
        )


def _rejection(exc: Exception) -> str:
    """The message a template's ``Error "..."`` gives, as a slug ("template_rejected")."""
    import re

    match = re.match(r'Error: "?(.*?)"?\. Rule Line', str(exc))
    words = re.findall(r"[a-z0-9]+", (match[1] if match else "").lower())
    return "_".join(words) or "template_rejected"


def _policy_problems(selection: Selection, raw_text: str, rows: list[dict[str, Any]]):
    """Output-quality checks the implementation declares for a data output.

    ``required_patterns``: lines every such output has (e.g. the table header).
    ``count_patterns``: ``{pattern, of}`` where the pattern's first group is a count the
    device prints; ``of: rows`` compares it with the rows read, ``of: keys`` with the
    distinct entries (the check item's identity Values).
    """
    import re

    policy = selection.output_quality_policy
    problems = []
    for pattern in policy.get("required_patterns") or ():
        if not re.search(pattern, raw_text, re.MULTILINE):
            problems.append(f"required_pattern_missing: {pattern}")
    identity = [name for name, field in selection.fields.items() if field.get("identity")]
    actual = {
        "rows": len(rows),
        "keys": len({tuple(str(row.get(name)) for name in identity) for row in rows}),
    }
    for count in policy.get("count_patterns") or ():
        for match in re.finditer(count["pattern"], raw_text, re.MULTILINE):
            if int(match.group(1)) != actual[count["of"]]:
                problems.append(
                    f"count_mismatch: {count['of']} {actual[count['of']]} != {match.group(1)}"
                )
    return problems


def _active_catalog():
    from .backend.check_items.catalog import CheckItemCatalogRepository

    return CheckItemCatalogRepository().load_active()


def _shipped_catalog():
    from .backend.check_items.catalog import load_catalog

    return load_catalog()


def _selection(check_item, implementation) -> Selection:
    import hashlib
    import io

    import textfsm

    from .backend.check_items.system_items import template_text, templates

    contract = check_item.canonical_contract
    fields = {
        name: {"value_type": rule.get("value_type"), "required": True, "identity": True}
        for name, rule in (contract.get("identity_dimension_rules") or {}).items()
    }
    for name in contract.get("identity_dimensions") or ():
        fields.setdefault(name, {"value_type": None, "required": True, "identity": True})
    for row in contract.get("observations") or ():
        fields[row["canonical_field"]] = {
            "value_type": row.get("value_type"),
            "required": row.get("required", True) is not False,
            "identity": False,
        }
    bound = templates().get(implementation.id)
    if bound is None:
        raise SelectionError(f"{implementation.id} has no bound template")
    text = template_text(bound["template"])
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != bound["sha256"]:
        raise SelectionError(
            f"{implementation.id}: template {bound['template']} changed (sha256 {digest}) "
            "without a reviewed pin"
        )
    header = set(textfsm.TextFSM(io.StringIO(text)).header)
    outside = sorted(header - set(fields))
    missing = sorted({name for name, field in fields.items() if field["required"]} - header)
    if outside or missing:
        raise SelectionError(
            f"{implementation.id}: template Values differ from {check_item.id} "
            f"(outside the contract: {outside}, required but absent: {missing})"
        )
    if undeclared := sorted(set(bound.get("defaults") or {}) - set(fields)):
        raise SelectionError(
            f"{implementation.id}: defaults for Values {check_item.id} does not declare: "
            f"{undeclared}"
        )
    return Selection(
        check_item_id=check_item.id,
        check_item_version_id=check_item.version_id,
        implementation_id=implementation.id,
        implementation_version_id=implementation.version_id,
        command=implementation.commands[0].command,
        scoped_command=bound.get("scoped_command"),
        fields=fields,
        template_ref=bound["template"],
        template_text=text,
        output_quality_policy=dict(implementation.output_quality_policy or {}),
        template_sha256=digest,
        scope_header=bound.get("scope_header"),
        units=dict(bound.get("units") or {}),
        defaults={name: str(value) for name, value in (bound.get("defaults") or {}).items()},
    )


def classify_output(
    raw_text: str, *, command: str = "", unsupported_patterns: Iterable[str] = ()
) -> tuple[str, str]:
    """(status, reason) of a command output before any template runs.

    status: unsupported_command, command_error, truncated, paged, valid_empty,
    empty_unverified or data. ``unsupported_patterns`` add to the default ones.
    """
    from types import SimpleNamespace

    from .backend.parser.output_classifier import UNSUPPORTED_PATTERNS, OutputClassifier

    classified = OutputClassifier().classify(
        raw_text,
        decision=SimpleNamespace(canonical_command=command, empty_semantics={}),
        profile={"unsupported_patterns": [*UNSUPPORTED_PATTERNS, *unsupported_patterns]},
    )
    return classified.status, classified.reason
