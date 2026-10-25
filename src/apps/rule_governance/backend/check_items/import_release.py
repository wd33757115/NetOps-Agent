"""Adapters that arrive with a check item from a rule package go into force with it.

An import writes every asset as a draft. Under the one-release rule nothing
else should need an approval of its own: when the imported check item version
is released, the adapters linked to it and the parser templates they read come
into force in the same transaction, and their runtime bundles are assembled.

A template from a package used to wait for a separate parser review. It is
replayed instead, and the release does not go ahead unless it passes: the
samples the package carried must parse to what they parsed to when exported,
and the outputs this environment's patrols already parsed with the adapter must
still yield records. With neither there is nothing to judge it on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    ParserTemplate,
)


def incoming_adapters(db: Any, check_item_version_id: str) -> list[CollectionImplementationVersionRecord]:
    """Adapter versions still in draft that are linked to this check item version: what an import brought."""

    return (
        db.query(CollectionImplementationVersionRecord)
        .join(
            CheckItemImplementationLinkRecord,
            CheckItemImplementationLinkRecord.implementation_version_id == CollectionImplementationVersionRecord.id,
        )
        .filter(
            CheckItemImplementationLinkRecord.check_item_version_id == check_item_version_id,
            CollectionImplementationVersionRecord.status == "draft",
        )
        .order_by(CollectionImplementationVersionRecord.implementation_id)
        .all()
    )


def withdraw_incoming(db: Any, check_item_version_id: str, *, now: datetime) -> int:
    """The draft this version was is withdrawn: what rode in with it goes too.

    Imported adapters have no change request of their own; they would only
    ever have come into force with this version. Those that serve no other
    draft or version in force stand down, and so do the templates only they read.
    """

    withdrawn: list[CollectionImplementationVersionRecord] = []
    for version in incoming_adapters(db, check_item_version_id):
        elsewhere = (
            db.query(CheckItemImplementationLinkRecord)
            .join(CheckItemVersionRecord, CheckItemVersionRecord.id == CheckItemImplementationLinkRecord.check_item_version_id)
            .filter(
                CheckItemImplementationLinkRecord.implementation_version_id == str(version.id),
                CheckItemVersionRecord.id != str(check_item_version_id),
                CheckItemVersionRecord.status.in_(["draft", "active"]),
            )
            .count()
        )
        if elsewhere:
            continue
        version.status, version.deprecated_at = "deprecated", now
        root = db.get(CollectionImplementationRecord, version.implementation_id)
        if root is not None and str(root.status) == "draft" and int(root.current_version or 0) == 0:
            root.status, root.updated_at = "deprecated", now
        withdrawn.append(version)
    db.flush()
    withdraw_draft_templates(db, withdrawn, now=now)
    return len(withdrawn)


def withdraw_draft_templates(
    db: Any, versions: list[CollectionImplementationVersionRecord], *, now: datetime
) -> int:
    """Templates still in draft that only these withdrawn adapter versions read: none will ever approve them."""

    candidates = {template_id for version in versions for template_id in template_ids(db, version)}
    if not candidates:
        return 0
    still_read = {
        template_id
        for other in db.query(CollectionImplementationVersionRecord).filter(
            CollectionImplementationVersionRecord.status.in_(["draft", "active"])
        )
        for template_id in template_ids(db, other)
        if template_id in candidates
    }
    count = 0
    for template_id in sorted(candidates - still_read):
        template = db.get(ParserTemplate, template_id)
        if template is not None and str(template.status) == "draft":
            template.status, template.deprecated_at, template.updated_at = "deprecated", now, now
            count += 1
    db.flush()
    return count


def template_ids(db: Any, implementation_version: CollectionImplementationVersionRecord) -> list[str]:
    """Every parser template an adapter version reads, from its projection and its command bindings."""

    ids = [str(dict((implementation_version.adapter_spec_json or {}).get("parser") or {}).get("template_id") or "")]
    ids += [
        str(row.parser_template_id or "")
        for row in db.query(CollectionImplementationParserBindingRecord).filter(
            CollectionImplementationParserBindingRecord.implementation_version_id == str(implementation_version.id)
        )
    ]
    return sorted({value for value in ids if value})


def text_only_commands(version: CollectionImplementationVersionRecord) -> list[str]:
    """The adapter's commands whose output this platform keeps as text, so no template of theirs is ever read.

    Another environment may parse a command this one only compares as text (a
    log window, say). The adapter still goes in; the release says so up front.
    """

    from src.apps.rule_governance.backend.parser.policy import ParserPolicy, approved_template_may_serve

    selector = dict(version.match_json or {})
    policy = ParserPolicy()
    found: list[str] = []
    for row in version.commands_json or []:
        command = str(dict(row).get("command") or "") if isinstance(row, dict) else str(row or "")
        if not command.strip():
            continue
        decision = policy.decide(
            vendor=str(selector.get("vendor") or "") or None, model=None, family=None, command=command
        )
        if not approved_template_may_serve(policy, decision, command):
            found.append(command)
    return found


def text_only_warning(commands: list[str]) -> str:
    """The one wording of it, for the release preview and the import's trial alike."""

    # The command policy is a configuration file of this app: no page changes it.
    return (
        f"命令 {'、'.join(commands)} 在本平台只比较原文、不做解析，导入的解析模板不会被巡检使用，"
        "检查项从这些设备拿不到字段。要拿到字段，请改用本平台能解析的命令；"
        "或由平台维护者修改命令策略配置并重新部署"
    )


def _package_samples(db: Any, template: ParserTemplate) -> list[dict[str, Any]]:
    """The samples the template's own package carried, with what they parsed to at export."""

    sha = dict(template.validation_json or {}).get("import_package")
    if not sha:
        return []
    # The same file can be imported again after its drafts were withdrawn: each import named its own templates.
    records = db.query(AuditLogRecord).filter(
        AuditLogRecord.action == "rule_package_import", AuditLogRecord.resource_id == str(sha)
    )
    return [
        {"text": sample["text"], "expected": sample["expected"]}
        for record in records
        for sample in (record.detail or {}).get("samples") or []
        if sample.get("parser") == str(template.id)
    ]


def replay_template(
    db: Any, template: ParserTemplate, *, implementation_id: str, current_text: str | None
) -> dict[str, Any]:
    from src.apps.rule_governance.backend.check_items.adapter_fields import stored_outputs
    from src.apps.rule_governance.backend.transfer_validator import release_replay

    outputs = [output["text"] for output in stored_outputs(implementation_id)]
    return release_replay(str(template.template_text or ""), current_text, _package_samples(db, template), outputs)


def _in_force_text(db: Any, previous: CollectionImplementationVersionRecord | None, template: ParserTemplate) -> str | None:
    """The template the adapter reads today for the same command, to replay its outputs against."""

    if previous is None:
        return None
    for template_id in template_ids(db, previous):
        current = db.get(ParserTemplate, template_id)
        if current is not None and current.canonical_command == template.canonical_command:
            return str(current.template_text or "") or None
    return None


def bring_in(
    db: Any, incoming: list[CollectionImplementationVersionRecord], *, actor: str, now: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """Put imported adapters and their templates into force inside the caller's transaction.

    Returns one entry per adapter in the form the release reports adapters in,
    and the reasons any of them cannot go in. The caller decides to roll back.
    """

    entries: list[dict[str, Any]] = []
    blockers: list[str] = []
    for version in incoming:
        root = (
            db.query(CollectionImplementationRecord)
            .filter(CollectionImplementationRecord.id == version.implementation_id)
            .with_for_update()
            .one()
        )
        name = str(root.name)
        previous = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.implementation_id == root.id,
                CollectionImplementationVersionRecord.status == "active",
            )
            .first()
        )
        templates, problems = activate_templates(
            db, version, replaced=previous, implementation_id=str(root.id), actor=actor, now=now
        )
        blockers += [f"导入的适配器“{name}”{problem}" for problem in problems]
        if previous is not None:
            # Running bundles of the version being replaced must all have somewhere to go.
            # Counted by check item: the release itself moves the one being released to its new version.
            served = {
                str(item.check_item_id)
                for item in db.query(CheckItemVersionRecord)
                .join(
                    CheckItemImplementationLinkRecord,
                    CheckItemImplementationLinkRecord.check_item_version_id == CheckItemVersionRecord.id,
                )
                .filter(CheckItemImplementationLinkRecord.implementation_version_id == str(version.id))
            }
            running = {
                str(item.check_item_id)
                for item in db.query(CheckItemVersionRecord)
                .join(
                    CheckItemRuntimeBundleVersionRecord,
                    CheckItemRuntimeBundleVersionRecord.check_item_version_id == CheckItemVersionRecord.id,
                )
                .filter(
                    CheckItemRuntimeBundleVersionRecord.implementation_version_id == str(previous.id),
                    CheckItemRuntimeBundleVersionRecord.status == "active",
                )
            }
            stranded = running - served
            if stranded:
                names = sorted(stranded)
                blockers.append(
                    f"导入的适配器“{name}”不再服务 {'、'.join(names)}，而这些检查项正在用它巡检；"
                    "请连同这些检查项一起导出后再导入"
                )
                continue
            previous.status, previous.deprecated_at = "deprecated", now
            db.flush()
        version.status, version.activated_at = "active", now
        root.status, root.current_version, root.updated_at = "active", int(version.version), now
        db.flush()
        unparsed = text_only_commands(version)
        entries.append({
            "implementation_id": str(root.id),
            "name": name,
            "previous_version_id": str(previous.id) if previous is not None else None,
            "version_id": str(version.id),
            "repinned": False,
            "dropped_fields": [],
            "brought_in": True,
            "templates": templates,
            "text_only_commands": unparsed,
            "text_only_warning": text_only_warning(unparsed) if unparsed else None,
        })
    return entries, blockers


def activate_templates(
    db: Any,
    version: CollectionImplementationVersionRecord,
    *,
    replaced: CollectionImplementationVersionRecord | None,
    implementation_id: str,
    actor: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Put the draft templates an adapter version reads into force, each after a replay it passed."""

    templates: list[dict[str, Any]] = []
    problems: list[str] = []
    for template_id in template_ids(db, version):
        template = db.query(ParserTemplate).filter(ParserTemplate.id == template_id).with_for_update().first()
        if template is None:
            problems.append("引用的解析模板不存在")
            continue
        if template.status == "active":
            templates.append({"template_id": template_id, "replay": None})
            continue
        if template.status != "draft":
            problems.append("引用的解析模板已停用，不能随之生效")
            continue
        replay = replay_template(
            db, template, implementation_id=implementation_id, current_text=_in_force_text(db, replaced, template)
        )
        templates.append({"template_id": template_id, "replay": replay})
        if not replay.get("passed"):
            problems.append(f"的解析模板没有通过回放：{replay.get('reason')}")
            continue
        blocker = _activate_template(
            db, template, serves=version, replaced=replaced, replay=replay, actor=actor, now=now
        )
        if blocker:
            problems.append(f"：{blocker}")
    return templates, problems


def _compatibility(version: CollectionImplementationVersionRecord, template: ParserTemplate, replay: dict[str, Any]) -> dict[str, Any]:
    """The devices the template is shown to fit: those of the adapter whose outputs and samples it passed on.

    A package carries no compatibility evidence (it would be an approval smuggled
    across); the replay against this adapter's outputs is the evidence here.
    """

    def values(*keys: str) -> list[str]:
        # The selector's own spelling is kept; the assembly compares case-insensitively.
        found: list[str] = []
        for key in keys:
            value = selector.get(key)
            found += [value] if isinstance(value, str) else [str(item) for item in value or []]
        return sorted({item.strip() for item in found if item and item.strip()})

    selector = dict(version.match_json or {})
    models, patterns, families = values("model", "models"), values("model_patterns"), values("family", "families")
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "scope": "model" if models or patterns else "family" if families else "vendor",
        "vendor": template.vendor,
        "models": models,
        "model_patterns": patterns,
        "families": families,
        "canonical_command": template.canonical_command,
        "evidence_source": "release_replay",
        "replayed_samples": int(replay.get("samples") or 0),
        "replayed_outputs": int(replay.get("outputs") or 0),
    }
    for key in ("os_version_patterns", "capabilities"):
        if selector.get(key):
            manifest[key] = values(key)
    return manifest


def assemble_bundles(
    db: Any, implementation_version_id: str, *, session_scope: Any, actor: str, reason: str, now: datetime
) -> list[str]:
    """Assemble and start a runtime bundle for every version in force this adapter version serves."""

    from src.apps.rule_governance.backend.check_items.runtime_bundle import (
        CheckItemRuntimeBundleService,
        RuntimeBundleValidationError,
    )

    service = CheckItemRuntimeBundleService(session_scope)
    started, blockers = [], []
    for row in (
        db.query(CheckItemImplementationLinkRecord)
        .join(CheckItemVersionRecord, CheckItemVersionRecord.id == CheckItemImplementationLinkRecord.check_item_version_id)
        .filter(
            CheckItemImplementationLinkRecord.implementation_version_id == implementation_version_id,
            CheckItemVersionRecord.status == "active",
        )
    ):
        selection = {"check_item_version_id": str(row.check_item_version_id),
                     "implementation_version_id": implementation_version_id}
        try:
            preview = service.preview_assembly(**selection)
            if preview.get("blockers"):
                raise RuntimeBundleValidationError(
                    [str(item.get("message") or item.get("code")) for item in preview["blockers"]]
                )
            draft = service.assemble(**selection, preview_digest=preview["preview_digest"], actor=actor)
            service.activate_in_session(
                db, draft["version_id"], actor=actor, reason=reason, now=now, allow_same_actor=True,
                audit_action="runtime_bundle_adapter_release",
            )
        except RuntimeBundleValidationError as exc:
            blockers.append("；".join(exc.blockers))
            continue
        started.append(str(draft["version_id"]))
    if blockers:
        raise ValueError("运行包无法启用：" + "；".join(blockers))
    return started


def _activate_template(
    db: Any,
    template: ParserTemplate,
    *,
    serves: CollectionImplementationVersionRecord,
    replaced: CollectionImplementationVersionRecord | None,
    replay: dict[str, Any],
    actor: str,
    now: datetime,
) -> str | None:
    """Bring an imported template into force; only the adapter it replaces may lose the one in force."""

    in_force = (
        db.query(ParserTemplate)
        .filter(
            ParserTemplate.vendor == template.vendor,
            ParserTemplate.profile == template.profile,
            ParserTemplate.canonical_command == template.canonical_command,
            ParserTemplate.status == "active",
            ParserTemplate.id != template.id,
        )
        .with_for_update()
        .first()
    )
    if in_force is not None:
        users = [
            row
            for row in db.query(CollectionImplementationVersionRecord).filter(
                CollectionImplementationVersionRecord.status == "active"
            )
            if str(in_force.id) in template_ids(db, row) and (replaced is None or str(row.id) != str(replaced.id))
        ]
        if users:
            return (
                f"同一设备和命令的解析模板正被本地适配器“{users[0].implementation_id}”使用，"
                "导入的模板会替换它；请连同那个适配器一起导出后再导入"
            )
        in_force.status, in_force.deprecated_at, in_force.updated_at = "deprecated", now, now
        # The one-active-template index is checked row by row: the old one stands down first.
        db.flush()
    validation = dict(template.validation_json or {})
    validation.update(
        status="passed",
        semantic_review={"status": "passed", "method": "release_replay", "by": actor, "at": now.isoformat()},
        regression_validation={
            "passed": True,
            "source": "release_replay",
            "sample_count": int(replay.get("samples") or 0) + int(replay.get("outputs") or 0),
            "passed_samples": int(replay.get("samples_passed") or 0) + int(replay.get("outputs_parsed") or 0),
        },
        release_replay=replay,
    )
    if not isinstance(validation.get("compatibility"), dict):
        validation["compatibility"] = _compatibility(serves, template, replay)
    template.validation_json = validation
    template.status, template.activated_at, template.updated_at = "active", now, now
    db.flush()
    return None


__all__ = [
    "activate_templates", "assemble_bundles", "bring_in", "incoming_adapters", "replay_template", "template_ids",
    "text_only_commands", "text_only_warning", "withdraw_draft_templates", "withdraw_incoming",
]
