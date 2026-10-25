"""Trying fields on an adapter in the background, and putting the confirmed ones into force.

A trial calls a model and takes from twenty seconds to a few minutes, so it runs
in a worker. Its state is one row per adapter and check item: a dialog that was
closed finds the result when it is opened again.

A trial saves nothing. Saving is a separate act by a person who has looked at
each new field and the output line it came from. It decides everything again on
the outputs as stored at that moment, then switches the template, the adapter
version and every runtime bundle of that adapter in one transaction, or nothing.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.apps.rule_governance.backend.check_items import adapter_fields
from src.apps.rule_governance.backend.check_items.adapter_fields import (
    AdapterFieldsError,
    check_extension,
    drop_values,
    missing_fields,
)
from src.apps.rule_governance.backend.check_items.catalog import build_collection_implementation
from src.apps.rule_governance.backend.parser.parser_bundle import build_parser_bundle, parse_parser_bundle
from src.apps.rule_governance.backend.parser.repository import contract_digest
from src.apps.rule_governance.backend.parser_generation import TextFSMValidationBackend
from src.core.check_items.command_adapter import validate_adapter_spec
# The patrol reads the fields found absent from the same rows, so the key has one definition.
from src.core.check_items.field_absence import PREFIX, task_key
from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    ParserControlSetting,
    ParserTemplate,
)
from src.infrastructure.db.postgres import get_db_session

ACTIVE = {"queued", "running"}
# A worker that died leaves its row active; past this the trial is taken as lost.
STALE_AFTER = timedelta(minutes=15)
_FINISHED = {"ready", "no_data", "regressed", "failed", "unsupported"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lost(state: dict[str, Any]) -> bool:
    """An active trial nobody is running any more."""

    if state.get("status") not in ACTIVE:
        return False
    since = state.get("started_at") or state.get("queued_at")
    try:
        return _now() - datetime.fromisoformat(str(since)) > STALE_AFTER
    except ValueError:
        return True


def active_trials() -> int:
    """Trials queued or running, for the deployment that must not interrupt them."""

    with get_db_session() as db:
        rows = db.query(ParserControlSetting).filter(ParserControlSetting.key.like(PREFIX + "%")).all()
        return sum(1 for row in rows if row.value_json.get("status") in ACTIVE and not _lost(row.value_json))


def cancel_active(db: Any) -> None:
    """End every trial under way; a trial has saved nothing, so there is nothing to look after."""

    for row in db.query(ParserControlSetting).filter(ParserControlSetting.key.like(PREFIX + "%")).with_for_update():
        if row.value_json.get("status") in ACTIVE:
            row.value_json = {**row.value_json, "status": "cancelled", "token": None,
                              "error": "应用停用中断了这次试算，请重新开始。"}


def recorded(check_item_id: str) -> dict[str, dict[str, Any]]:
    """Per adapter of a check item: the fields its device was found not to print, and a trial under way."""

    with get_db_session() as db:
        rows = db.query(ParserControlSetting).filter(ParserControlSetting.key.like(PREFIX + "%")).all()
        return {
            str(row.value_json.get("implementation_id")): {
                "absent_fields": sorted(dict(row.value_json.get("absent") or {})),
                "trial_status": "failed" if _lost(row.value_json) else str(row.value_json.get("status") or "idle"),
            }
            for row in rows
            if row.value_json.get("check_item_id") == check_item_id
        }


class AdapterFieldService:
    def __init__(
        self,
        session_scope: Callable[[], Any] = get_db_session,
        dispatch: Callable[[str, str], bool] | None = None,
        trial: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.session_scope = session_scope
        self._dispatch = dispatch
        self._trial = trial

    # -- reading ---------------------------------------------------------

    def get(self, implementation_id: str, check_item_id: str) -> dict[str, Any]:
        """What the dialog shows: the fields that can be asked for, and the last trial if there is one."""

        context = adapter_fields.extension_context(implementation_id, check_item_id)
        with self.session_scope() as db:
            row = db.get(ParserControlSetting, task_key(implementation_id, check_item_id))
            state = copy.deepcopy(row.value_json) if row else {}
        return self._public(state, context)

    def _public(self, state: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        adapter, item = context["adapter"], context["item"]
        absent = dict(state.get("absent") or {})
        rows = {row["canonical_field"]: row for row in item.canonical_contract.get("observations") or []}
        result = copy.deepcopy(state.get("result")) if state.get("status") in _FINISHED else None
        if result is not None:
            # The template is the server's to keep; the dialog shows what it adds.
            result.pop("template_text", None)
            result["stale"] = result.get("base") != context["base"]
        status = str(state.get("status") or "idle")
        if _lost(state):
            status = "failed"
        missing = missing_fields(adapter, item)
        keys = set(item.canonical_contract.get("identity_dimensions") or [])
        # Counted the way the check item's own page counts: its fields, without the keys that identify an object.
        total = len([name for name in rows if name not in keys])
        return {
            "implementation_id": adapter.id,
            "implementation_name": str(adapter.name),
            "check_item_id": item.id,
            "status": status,
            "requested_fields": list(state.get("requested_fields") or []),
            "started_at": state.get("started_at") or state.get("queued_at"),
            "finished_at": state.get("finished_at"),
            "sample_count": len(context["outputs"]),
            "field_count": total,
            "provided_count": total - len(missing),
            "missing_fields": [
                {
                    "name": name,
                    "description": str(rows[name].get("description") or ""),
                    "use": str((rows[name].get("assessment_roles") or ["evidence"])[0]),
                    "absent": name in absent,
                }
                for name in missing
            ],
            "result": result,
            "saved": state.get("saved"),
            "error": "试算没有在预期时间内结束，可能已经中断。请重新开始。" if _lost(state) else state.get("error"),
            "error_detail": None if _lost(state) else state.get("error_detail"),
        }

    # -- the trial -------------------------------------------------------

    def start(
        self,
        implementation_id: str,
        check_item_id: str,
        *,
        fields: list[str] | None,
        actor: str,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        context = adapter_fields.extension_context(implementation_id, check_item_id)
        missing = missing_fields(context["adapter"], context["item"])
        wanted = [name for name in (fields or missing) if name in missing]
        if not wanted:
            raise AdapterFieldsError("这个适配器已经提供了所选字段")
        if not context["outputs"]:
            raise AdapterFieldsError(
                "这个适配器还没有在巡检中解析过任何输出，没有样本可用来验证。先让匹配的设备巡检一次。"
            )
        key, token = task_key(implementation_id, check_item_id), uuid.uuid4().hex
        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            old = copy.deepcopy(row.value_json) if row else {}
            if old.get("status") in ACTIVE and not _lost(old):
                return self._public(old, context)
            state = {
                "implementation_id": implementation_id,
                "check_item_id": check_item_id,
                "status": "queued",
                "token": token,
                "requested_fields": wanted,
                "actor": actor,
                "actor_user_id": actor_user_id,
                "queued_at": _now().isoformat(),
                "absent": dict(old.get("absent") or {}),
                "saved": old.get("saved"),
            }
            if row is None:
                db.add(ParserControlSetting(key=key, value_json=state))
            else:
                row.value_json = state
        if not self._send(key, token):
            with self.session_scope() as db:
                row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
                if row is not None and row.value_json.get("token") == token:
                    row.value_json = {**row.value_json, "status": "failed", "token": None,
                                      "error": "后台任务队列暂时不可用，试算没有开始。请稍后再试。"}
        return self.get(implementation_id, check_item_id)

    def _send(self, key: str, token: str) -> bool:
        if self._dispatch is not None:
            return self._dispatch(key, token)
        from src.apps.rule_governance.backend.adapter_field_tasks import dispatch

        return dispatch(key, token)

    def run(self, key: str, token: str, *, guard: Callable[[Any], Any] | None = None) -> None:
        """What the worker does: mark the trial running, make it, and keep its result.

        ``guard`` is given the user who asked and raises when that user may no longer use the app.
        """

        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            if row is None or row.value_json.get("status") != "queued" or row.value_json.get("token") != token:
                return
            state = copy.deepcopy(row.value_json)
            row.value_json = {**state, "status": "running", "started_at": _now().isoformat()}
        try:
            if guard is not None:
                guard(state.get("actor_user_id"))
            trial = self._trial or adapter_fields.trial_add_fields
            result = trial(state["implementation_id"], state["check_item_id"], list(state["requested_fields"]))
            status, error, detail = str(result["status"]), None, None
        except AdapterFieldsError as exc:
            result, status, error, detail = None, "failed", str(exc), None
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, the row must not stay running
            result, status, detail = None, "failed", f"{type(exc).__name__}: {exc}"[:500]
            error = (
                "AI 模型没有配置好，试算没有开始。请联系管理员检查模型设置。"
                if "not configured" in str(exc)
                else "试算出错，没有完成。可以再试一次；仍然不行时把下面的技术细节交给管理员。"
            )
        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            if row is None or row.value_json.get("token") != token or row.value_json.get("status") != "running":
                return
            absent = dict(row.value_json.get("absent") or {})
            if result is not None and status in {"ready", "no_data"}:
                at = _now().isoformat()
                for name in result["provided_fields"]:
                    absent.pop(name, None)
                for name in result["absent_fields"]:
                    absent[name] = {"at": at, "outputs": int(result["sample_count"])}
            row.value_json = {
                **row.value_json,
                "status": status,
                "token": None,
                "finished_at": _now().isoformat(),
                "result": result,
                "absent": absent,
                "error": error,
                "error_detail": detail,
            }

    # -- saving ----------------------------------------------------------

    def save(
        self,
        implementation_id: str,
        check_item_id: str,
        *,
        fields: list[str],
        actor: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Put the confirmed fields of the last trial into force. ``dry_run`` does it and rolls back."""

        key = task_key(implementation_id, check_item_id)
        with self.session_scope() as db:
            try:
                row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
                state = copy.deepcopy(row.value_json) if row else {}
                result = state.get("result") or {}
                if state.get("status") != "ready" or not result.get("template_text"):
                    raise AdapterFieldsError("没有可以保存的试算结果，请先试算")
                chosen = [name for name in result["provided_fields"] if name in set(fields)]
                if not chosen or len(chosen) != len(set(fields)):
                    raise AdapterFieldsError("只能保存这次试算取到值的字段")
                context = adapter_fields.extension_context(implementation_id, check_item_id)
                if context["base"] != result.get("base"):
                    raise AdapterFieldsError("试算之后，适配器、检查项或解析模板已经变了。请重新试算。")
                template_text = str(result["template_text"])
                left_out = [name for name in result["provided_fields"] if name not in chosen]
                if left_out:
                    template_text = drop_values(template_text, left_out)
                checked = check_extension(
                    TextFSMValidationBackend(), context["current_text"], template_text, context["outputs"], chosen
                )
                problems = [
                    *checked["differences"][:3],
                    *(f"已有字段 {name} 的定义被改动" for name in checked["rewritten"]),
                    *(f"已有规则被改动：{rule}" for rule in checked["rules_lost"][:3]),
                    *(f"模板里多了没有选择的字段 {name}" for name in checked["undeclared"]),
                    *(f"字段 {name} 在现有输出里取不到值" for name in chosen if not checked["found"][name]["records"]),
                ]
                if problems:
                    raise AdapterFieldsError("保存前的复核没有通过，没有做任何改动：" + "；".join(problems))
                now = _now()
                switched = _switch(db, context, template_text, chosen, actor=actor, now=now)
                summary = {
                    **switched,
                    "fields": chosen,
                    "left_out_fields": left_out,
                    "outputs_replayed": len(context["outputs"]),
                    "records_replayed": int(checked["record_count"]),
                    "at": now.isoformat(),
                    "by": actor,
                }
                db.add(AuditLogRecord(
                    id=str(uuid.uuid4()),
                    idempotency_key=f"adapter-fields:{switched['implementation_version_id']}",
                    user_id=actor,
                    username=actor,
                    action="adapter_fields_added",
                    resource_type="collection_implementation",
                    resource_id=implementation_id,
                    detail={"check_item_id": check_item_id, **summary},
                    status="success",
                    created_at=now,
                ))
                row.value_json = {
                    **state,
                    "status": "saved",
                    "result": {**result, "template_text": None},
                    "saved": summary,
                    "error": None,
                }
                db.flush()
                if dry_run:
                    db.rollback()
                return {**summary, "dry_run": dry_run}
            except Exception:
                db.rollback()
                raise


def _switch(
    db: Any, context: dict[str, Any], template_text: str, fields: list[str], *, actor: str, now: datetime
) -> dict[str, Any]:
    """Template, adapter version and runtime bundles move together inside the caller's transaction."""

    from src.apps.rule_governance.backend.check_items.catalog_governance import _joined
    from src.apps.rule_governance.backend.check_items.runtime_bundle import (
        CheckItemRuntimeBundleService,
        RuntimeBundleValidationError,
    )

    implementation_id, check_item_id = context["adapter"].id, context["item"].id
    adapter = (
        db.query(CollectionImplementationRecord)
        .filter(CollectionImplementationRecord.id == implementation_id)
        .with_for_update()
        .one()
    )
    old = (
        db.query(CollectionImplementationVersionRecord)
        .filter(
            CollectionImplementationVersionRecord.implementation_id == implementation_id,
            CollectionImplementationVersionRecord.status == "active",
        )
        .one()
    )
    template = (
        db.query(ParserTemplate)
        .filter(ParserTemplate.id == context["base"]["template_id"])
        .with_for_update()
        .one()
    )
    # Someone else may have moved the adapter or its template since the outputs were replayed.
    if str(old.id) != context["base"]["implementation_version_id"] or template.status != "active":
        raise AdapterFieldsError("适配器或解析模板刚刚被别的操作改动了。请重新试算。")

    # 1. The extended template takes the place of the one in force. One template serves one adapter.
    record_set = context["base"]["record_set"]
    bundle = parse_parser_bundle(str(template.template_text))
    stored_text = (
        build_parser_bundle({
            entry.name: template_text if entry.name == record_set else entry.template_text
            for entry in bundle.record_sets
        }).serialize()
        if bundle is not None
        else template_text
    )
    validation = deepcopy(dict(template.validation_json or {}))
    for entry in validation.get("record_sets") or []:
        if entry.get("name") == record_set:
            entry["optional_fields"] = sorted({*(entry.get("optional_fields") or []), *fields})
    validation.update(
        source="adapter_field_extension",
        field_extension={
            "extends_template_id": str(template.id),
            "check_item_id": check_item_id,
            "fields": list(fields),
            "outputs_replayed": len(context["outputs"]),
        },
        regression_validation={
            "passed": True,
            "sample_count": len(context["outputs"]),
            "passed_samples": len(context["outputs"]),
            "source": "adapter_field_extension",
        },
        contract_approval_status="approved",
        contract_approved_by=actor,
        contract_approved_at=now.isoformat(),
        contract_approval_kind="adapter_field_extension",
        contract_approval_reason=f"补字段：{'、'.join(fields)}",
    )
    digest = contract_digest(validation)
    contract_version = int(template.contract_version or 1) + (0 if digest == template.contract_sha256 else 1)
    validation.update(contract_version=contract_version, contract_sha256=digest)
    new_template_id = str(uuid.uuid4())
    template.status, template.deprecated_at, template.updated_at = "deprecated", now, now
    # The one-active-template index is checked row by row: the old one has to stand down first.
    db.flush()
    db.add(ParserTemplate(
        id=new_template_id,
        vendor=template.vendor,
        model=template.model,
        family=template.family,
        profile=template.profile,
        canonical_command=template.canonical_command,
        category=template.category,
        version=f"fields-{now:%Y%m%d%H%M%S}",
        asset_path=f"database://parser/{new_template_id}",
        template_text=stored_text,
        sha256=hashlib.sha256(stored_text.encode("utf-8")).hexdigest(),
        status="active",
        validation_json=validation,
        contract_version=contract_version,
        contract_sha256=digest,
        source_job_id=None,
        created_at=now,
        updated_at=now,
        activated_at=now,
    ))
    db.flush()

    # 2. The adapter gets a version that reads the new template and writes the new fields.
    served = {
        str(version.check_item_id): version
        for version in db.query(CheckItemVersionRecord)
        .join(
            CheckItemImplementationLinkRecord,
            CheckItemImplementationLinkRecord.check_item_version_id == CheckItemVersionRecord.id,
        )
        .filter(
            CheckItemImplementationLinkRecord.implementation_version_id == str(old.id),
            CheckItemVersionRecord.status == "active",
        )
    }
    if check_item_id not in served:
        raise AdapterFieldsError("这个适配器已经不再服务于这个检查项的当前版本。请重新试算。")
    rows = {
        row["canonical_field"]: row
        for row in dict(served[check_item_id].canonical_contract_json or {}).get("observations") or []
    }
    spec = deepcopy(dict(old.adapter_spec_json or {}))
    spec["parser"] = {**dict(spec.get("parser") or {}), "template_id": new_template_id}
    for output in spec.get("outputs") or []:
        if output.get("check_item_id") != check_item_id or output.get("source_record_set") != record_set:
            continue
        bindings = dict(dict(output.get("projection") or {}).get("fields") or {})
        for name in fields:
            binding: dict[str, Any] = {"source": name}
            if rows[name].get("dimensions"):
                binding["dimensions"] = {dimension: {"field": dimension} for dimension in rows[name]["dimensions"]}
            bindings[name] = binding
        output["projection"] = {**dict(output.get("projection") or {}), "fields": bindings}
    try:
        spec = validate_adapter_spec(
            spec,
            check_item_ids=tuple(sorted(served)),
            check_items={
                item_id: {"version_id": str(version.id), "canonical_contract": dict(version.canonical_contract_json or {})}
                for item_id, version in served.items()
            },
            require_pinned_check_item_versions=True,
        )
        latest = max(
            int(row.version)
            for row in db.query(CollectionImplementationVersionRecord.version).filter(
                CollectionImplementationVersionRecord.implementation_id == implementation_id
            )
        )
        candidate = build_collection_implementation(
            {
                "id": implementation_id,
                "version": latest + 1,
                "name": str(adapter.name),
                "description": str(adapter.description or ""),
                "check_item_ids": sorted(served),
                "match": dict(old.match_json or {}),
                "commands": [dict(command) for command in old.commands_json or []],
                "collector": str(old.collector),
                "parser_requirement": str(old.parser_requirement),
                "template_scope": old.template_scope,
                "adapter_spec": spec,
                "output_quality_policy": dict(old.output_quality_policy_json or {}),
                "selection_priority": int(old.selection_priority),
                "status": "active",
            },
            known_check_item_ids=set(served),
        )
    except ValueError as exc:
        raise AdapterFieldsError(f"适配器无法写出这些字段：{exc}") from exc
    db.add(CollectionImplementationVersionRecord(
        id=candidate.version_id,
        implementation_id=candidate.id,
        version=candidate.version,
        collector=candidate.collector,
        match_json=candidate.match,
        commands_json=[{"command": item.command, "priority": item.priority} for item in candidate.commands],
        parser_requirement=candidate.parser_requirement,
        template_scope=candidate.template_scope,
        adapter_spec_json=candidate.adapter_spec or None,
        output_quality_policy_json=candidate.output_quality_policy or None,
        selection_priority=candidate.selection_priority,
        status="active",
        digest=candidate.digest,
        created_by=actor,
        created_at=now,
        activated_at=now,
    ))
    links = (
        db.query(CheckItemImplementationLinkRecord)
        .filter(CheckItemImplementationLinkRecord.implementation_version_id == str(old.id))
        .all()
    )
    old.status, old.deprecated_at = "deprecated", now
    adapter.current_version, adapter.updated_at = candidate.version, now
    db.flush()
    in_force = {str(version.id) for version in served.values()}
    for link in links:
        if str(link.check_item_version_id) in in_force:
            db.add(CheckItemImplementationLinkRecord(
                id=str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/check-item-link/{link.check_item_version_id}/{candidate.version_id}",
                )),
                check_item_version_id=link.check_item_version_id,
                implementation_version_id=candidate.version_id,
                priority=int(link.priority),
                required=bool(link.required),
                created_at=now,
            ))
    db.flush()

    # 3. Every runtime bundle the old adapter version carried is rebuilt on the new one.
    carried = (
        db.query(CheckItemRuntimeBundleVersionRecord)
        .filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            CheckItemRuntimeBundleVersionRecord.implementation_version_id == str(old.id),
        )
        .all()
    )
    service = CheckItemRuntimeBundleService(lambda: _joined(db))
    bundles, blockers = [], []
    for bundle_row in carried:
        selection = {
            "check_item_version_id": str(bundle_row.check_item_version_id),
            "implementation_version_id": candidate.version_id,
        }
        try:
            preview = service.preview_assembly(**selection)
            if preview.get("blockers"):
                raise RuntimeBundleValidationError(
                    [str(item.get("message") or item.get("code")) for item in preview["blockers"]]
                )
            draft = service.assemble(**selection, preview_digest=preview["preview_digest"], actor=actor)
            service.activate_in_session(
                db,
                draft["version_id"],
                actor=actor,
                reason=f"补字段：{'、'.join(fields)}",
                now=now,
                allow_same_actor=True,
                audit_action="runtime_bundle_adapter_fields",
            )
        except RuntimeBundleValidationError as exc:
            blockers.append("；".join(exc.blockers))
            continue
        bundles.append(str(draft["version_id"]))
    if blockers:
        raise AdapterFieldsError("运行包无法切到新的适配器版本，没有做任何改动：" + "；".join(blockers))
    db.flush()
    return {
        "template_id": new_template_id,
        "previous_template_id": str(template.id),
        "implementation_version_id": candidate.version_id,
        "implementation_version": int(candidate.version),
        "previous_implementation_version": int(old.version),
        "runtime_bundle_version_ids": bundles,
    }


__all__ = [
    "ACTIVE", "AdapterFieldService", "PREFIX", "active_trials", "cancel_active", "recorded", "task_key",
]
