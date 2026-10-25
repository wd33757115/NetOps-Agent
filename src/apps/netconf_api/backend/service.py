"""NETCONF API definitions: drafts, published versions, device capabilities and trials.

A definition is edited as a draft and published as an immutable version; consumers use
the published versions only (``published``). Publishing checks the definition against
its check item's Values in rule governance, so a published definition always yields the
same Value rows the CLI implementation does. A trial runs a definition (published or
draft) against one inventory device over the platform's read-only NETCONF session and
keeps the rows and problems it gave.

Devices come from the platform inventory: the App passes the device's username and
credential reference, and the platform resolves the password server-side. A first-seen
830 host key is recorded the way topology discovery records port 22 keys; a changed key
stops the session until it is confirmed in the SSH terminal App.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import mapping
from .models import Capability, Definition, DefinitionVersion, Trial, now

APP_ID = "netconf-api"
SEEDS = Path(__file__).resolve().parent / "seeds"
MAX_REPLY_BYTES = 16 * 1024 * 1024
TRIAL_ROWS = 5000  # rows kept per trial; the counts are always whole
RULES_SOURCE = "published"  # tests read the catalog shipped with rule governance ("shipped")


class Draft(BaseModel):
    body: dict[str, Any]
    revision: int = Field(ge=0)  # 0 creates a new definition


class Publish(BaseModel):
    revision: int = Field(ge=1)
    note: str = Field(default="", max_length=500)


class Discard(BaseModel):
    revision: int = Field(ge=1)


class BrowseRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=300)
    path: list[str] = Field(min_length=1, max_length=20)  # from the top element down
    datastore: str = "state"  # state: <get>; running: <get-config> of running


class TrialRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    definition_id: str = Field(min_length=1, max_length=120)
    draft: bool = False  # run the draft instead of the published version


@contextmanager
def sessions():
    from sqlalchemy.orm import Session

    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def digest(body):
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(body):
    try:
        return mapping.load(body)
    except mapping.DefinitionError as exc:
        raise HTTPException(422, f"定义不可用：{exc}") from None


def _published_bodies(session):
    rows = session.execute(
        select(DefinitionVersion)
        .join(
            Definition,
            (Definition.id == DefinitionVersion.definition_id)
            & (Definition.published_version == DefinitionVersion.version),
        )
        .order_by(DefinitionVersion.definition_id)
    ).scalars()
    return {row.definition_id: row for row in rows}


def published(session=None):
    """The published definitions by id (what consumers read)."""
    if session is None:
        with sessions() as session:
            return published(session)
    return mapping.load_all(row.body for row in _published_bodies(session).values())


def published_versions(session=None):
    """{id: (definition, version, sha256)} of the published definitions."""
    if session is None:
        with sessions() as session:
            return published_versions(session)
    rows = _published_bodies(session)
    definitions = mapping.load_all(row.body for row in rows.values())
    return {key: (definitions[key], row.version, row.sha256) for key, row in rows.items()}


def _owned(session, definition_id, *, lock=False):
    row = session.get(Definition, definition_id, with_for_update=lock)
    if row is None:
        raise HTTPException(404, "定义不存在")
    return row


def _stale(row, revision):
    if row.revision != revision:
        raise HTTPException(409, "定义已被修改，请刷新后重试")


def _summary(row, version):
    body = row.draft or (version.body if version else {})
    return {
        "id": row.id,
        "check_item": body.get("check_item"),
        "vendor": body.get("vendor"),
        "platforms": body.get("platforms") or [],
        "description": body.get("description") or "",
        "module": body.get("module"),
        "published_version": row.published_version,
        "has_draft": row.draft is not None,
        "revision": row.revision,
    }


def list_definitions(actor):
    with sessions() as session:
        versions = _published_bodies(session)
        rows = session.scalars(select(Definition).order_by(Definition.id))
        return [_summary(row, versions.get(row.id)) for row in rows]


def _version_view(row):
    return {
        "version": row.version,
        "sha256": row.sha256,
        "check_item": row.check_item,
        "published_by": row.published_by,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "note": row.note or "",
    }


def read_definition(actor, definition_id):
    with sessions() as session:
        row = _owned(session, definition_id)
        versions = list(
            session.scalars(
                select(DefinitionVersion)
                .where(DefinitionVersion.definition_id == definition_id)
                .order_by(DefinitionVersion.version.desc())
            )
        )
        current = next((v for v in versions if v.version == row.published_version), None)
        return {
            **_summary(row, current),
            "draft": row.draft,
            "draft_by": row.draft_by,
            "draft_at": row.draft_at.isoformat() if row.draft_at else None,
            "published": {**_version_view(current), "body": current.body} if current else None,
            "versions": [_version_view(v) for v in versions],
        }


def read_version(actor, definition_id, version):
    """One published version with its body (the history keeps every version whole)."""
    with sessions() as session:
        _owned(session, definition_id)
        row = session.get(DefinitionVersion, (definition_id, version))
        if row is None:
            raise HTTPException(404, "该版本不存在")
        return {**_version_view(row), "body": row.body}


def save_draft(actor, definition_id, payload: Draft):
    definition = _load(payload.body)
    if definition.id != definition_id:
        raise HTTPException(422, "定义的 id 与地址不一致")
    with sessions() as session:
        row = session.get(Definition, definition_id, with_for_update=True)
        if row is None:
            if payload.revision != 0:
                raise HTTPException(409, "定义已被删除或尚未创建，请刷新后重试")
            row = Definition(id=definition_id, revision=0)
            session.add(row)
        else:
            _stale(row, payload.revision)
        row.draft, row.draft_by, row.draft_at = payload.body, actor.user_id, now()
        row.revision += 1
        session.flush()
        return {"id": row.id, "revision": row.revision}


def discard_draft(actor, definition_id, payload: Discard):
    with sessions() as session:
        row = _owned(session, definition_id, lock=True)
        _stale(row, payload.revision)
        if row.published_version is None:
            raise HTTPException(409, "从未发布的定义没有可回退的版本")
        row.draft = row.draft_by = row.draft_at = None
        row.revision += 1
        return {"id": row.id, "revision": row.revision}


def _fields(check_item, vendor, platform, model=None):
    from src.apps.rule_governance.runtime import CheckItemSelectionReader, SelectionError

    device = {"vendor": vendor, "platform": platform}
    if model:
        device["model"] = model
    try:
        chosen = (
            CheckItemSelectionReader(source=RULES_SOURCE)
            .select([check_item], device)
            .get(check_item)
        )
    except SelectionError:
        return None
    return chosen.fields if chosen else None


def contract_fields(definition, platform, model=None):
    """The check item's Values (``Selection.fields``) for one device family, or None
    when rule governance publishes no implementation of it there."""
    return _fields(definition.check_item, definition.vendor, platform, model)


def read_contract(actor, check_item, vendor, platform):
    """The Values a definition for this check item must write on one device family, as
    publishing checks them: name, type, required, part of the entry's identity."""
    fields = _fields(check_item, vendor, platform)
    if fields is None:
        raise HTTPException(404, f"规则治理没有为 {vendor} {platform} 发布检查项 {check_item}")
    return {
        "check_item": check_item,
        "vendor": vendor,
        "platform": platform,
        "values": [
            {
                "name": name,
                "value_type": spec.get("value_type"),
                "required": bool(spec.get("required")),
                "identity": bool(spec.get("identity")),
            }
            for name, spec in fields.items()
        ],
    }


def publish(actor, definition_id, payload: Publish):
    with sessions() as session:
        row = _owned(session, definition_id, lock=True)
        _stale(row, payload.revision)
        if row.draft is None:
            raise HTTPException(409, "没有待发布的草稿")
        definition = _load(row.draft)
        bodies = {key: v.body for key, v in _published_bodies(session).items()}
        bodies[definition_id] = row.draft
        try:
            mapping.load_all(bodies.values())
        except mapping.DefinitionError as exc:
            raise HTTPException(422, f"定义不可用：{exc}") from None
        if definition.check_item:
            # One definition serves a check item on a device family: consumers pick it.
            for other in mapping.load_all(bodies.values()).values():
                if (
                    other.id != definition_id
                    and other.check_item == definition.check_item
                    and other.vendor.casefold() == definition.vendor.casefold()
                    and set(other.platforms) & set(definition.platforms)
                ):
                    raise HTTPException(
                        409, f"{other.id} 已为同一设备平台提供 {definition.check_item}"
                    )
            gaps = []
            for platform in definition.platforms:
                fields = contract_fields(definition, platform)
                if fields is None:
                    gaps.append(f"no_contract:{platform}")
                else:
                    gaps += [
                        f"{gap}:{platform}" for gap in mapping.contract_gaps(definition, fields)
                    ]
            if gaps:
                raise HTTPException(409, {"message": "定义与检查项的值不一致", "gaps": gaps})
        latest = session.scalar(
            select(func.max(DefinitionVersion.version)).where(
                DefinitionVersion.definition_id == definition_id
            )
        )
        version = DefinitionVersion(
            definition_id=definition_id,
            version=(latest or 0) + 1,
            body=row.draft,
            sha256=digest(row.draft),
            check_item=definition.check_item,
            published_by=actor.user_id,
            published_at=now(),
            note=payload.note or None,
        )
        session.add(version)
        row.published_version = version.version
        row.draft = row.draft_by = row.draft_at = None
        row.revision += 1
        session.flush()
        return {"id": row.id, "revision": row.revision, **_version_view(version)}


# --- devices ---------------------------------------------------------------------------


def _device(device_id):
    from src.platform.inventory.repository import DeviceRepository

    found = DeviceRepository().get_devices_by_filter(device_id=device_id, status="active")
    if not found:
        raise HTTPException(404, "设备不存在或未启用")
    device = found[0]
    if not device.get("ip") or not device.get("username") or not device.get("credential_ref"):
        raise HTTPException(409, "设备缺少管理地址、用户名或凭据引用")
    return device


def _access(device):
    """One device over NETCONF, logged in with its inventory credential reference."""
    from src.apps.device_access.backend.host_keys import HostKeyStore
    from src.apps.device_access.backend.models import DOMAIN
    from src.platform.device_access.contracts import ConnectionProfile, Target
    from src.platform.device_access.netconf import NETCONF_PORT, NetconfConnector
    from src.platform.device_access.service import DeviceAccessService

    trust = HostKeyStore()

    def verify(target, port, key):
        return trust.verify(target.host, port, key, auto_trust=True)

    ref = device["device_id"]
    target = Target(
        ref,
        device["ip"],
        device_id=ref,
        management_domain=DOMAIN,
        display_name=device.get("device_name"),
    )
    profile = ConnectionProfile(
        ref,
        device["username"],
        credential_ref=device["credential_ref"],
        port=NETCONF_PORT,
        max_output_bytes=MAX_REPLY_BYTES,
        platform=device.get("platform"),
    )
    return DeviceAccessService(
        target_resolver=lambda _: target,
        profile_resolver=lambda _: profile,
        netconf=NetconfConnector(host_key_verifier=verify),
    )


@contextmanager
def _netconf(actor, device):
    from src.platform.device_access.contracts import AccessContext, AccessGrant

    ref = device["device_id"]
    context = AccessContext(
        actor.user_id, APP_ID, (AccessGrant(ref, ref, action="device.netconf"),)
    )
    with _access(device).netconf(context, ref, ref) as (session, guard):
        yield session, guard


def _inventory():
    from src.platform.inventory.repository import DeviceRepository

    return DeviceRepository().get_devices_by_filter(status="active")


def _module_list(capabilities):
    """[{name, revision, namespace}] the device announces (RFC 6020 module capabilities)."""
    found = {}
    for capability in capabilities:
        namespace, _, query = capability.partition("?")
        params = parse_qs(query)
        for module in params.get("module", []):
            found.setdefault(
                module,
                {
                    "name": module,
                    "revision": (params.get("revision") or [None])[0],
                    "namespace": namespace,
                },
            )
    return [found[name] for name in sorted(found)]


def list_devices(actor):
    """The active inventory devices with their last capability discovery, if any: the
    modules announced, and for the device's vendor and platform which published
    definitions it can serve (their module announced) and which not. No session is
    opened; discovery is ``discover``."""
    devices = _inventory()
    with sessions() as session:
        recorded = {row.device_id: row for row in session.scalars(select(Capability))}
        definitions = published(session)
        found = []
        for device in devices:
            vendor = str(device.get("vendor") or "").casefold()
            platform = str(device.get("platform") or "")
            row = recorded.get(device["device_id"])
            capability = None
            if row is not None:
                capability = _capability_view(row, definitions)
                modules = _module_list(row.capabilities or ())
                announced = {module["name"] for module in modules}
                capability["modules"] = modules
                capability["unavailable"] = sorted(
                    (
                        {"id": definition.id, "module": definition.module}
                        for definition in definitions.values()
                        if definition.module
                        and definition.vendor.casefold() == vendor
                        and platform in definition.platforms
                        and definition.module not in announced
                    ),
                    key=lambda item: item["id"],
                )
            found.append(
                {
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name"),
                    "ip": device.get("ip"),
                    "vendor": device.get("vendor"),
                    "platform": device.get("platform"),
                    "model": device.get("model"),
                    "capability": capability,
                }
            )
        return found


def announced_modules(capabilities):
    """{module: namespace} the device announces (RFC 6020 module capabilities)."""
    found = {}
    for capability in capabilities:
        namespace, _, query = capability.partition("?")
        for module in parse_qs(query).get("module", []):
            found.setdefault(module, namespace)
    return found


def _record_capabilities(actor, device_id, capabilities, failure_code=None):
    with sessions() as session:
        row = session.get(Capability, device_id, with_for_update=True)
        if row is None:
            row = Capability(device_id=device_id)
            session.add(row)
        row.status = "unavailable" if failure_code else "available"
        row.failure_code = failure_code
        if not failure_code:
            row.capabilities = list(capabilities)
        elif row.capabilities is None:
            row.capabilities = []
        row.checked_by, row.checked_at = actor.user_id, now()


def _capability_view(row, definitions):
    modules = announced_modules(row.capabilities or ())
    return {
        "device_id": row.device_id,
        "status": row.status,
        "failure_code": row.failure_code,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "capability_count": len(row.capabilities or ()),
        "module_count": len(modules),
        # Published definitions whose module the device announces.
        "definitions": sorted(
            definition.id
            for definition in definitions.values()
            if definition.module and definition.module in modules
        ),
    }


def discover(actor, device_id):
    """Read the device's NETCONF hello and keep what it announces."""
    from src.platform.device_access.contracts import DeviceAccessError

    device = _device(device_id)
    try:
        with _netconf(actor, device) as (session, _):
            capabilities = session.capabilities
    except DeviceAccessError as exc:
        _record_capabilities(actor, device_id, (), exc.failure_code)
    else:
        _record_capabilities(actor, device_id, capabilities)
    return read_capabilities(actor, device_id)


def browse_filter(namespace, path):
    """The subtree filter selecting everything under one path: element names only, built
    as XML here (the same checks as a definition's), so nothing else reaches the RPC."""
    names = mapping._path(path, "path")
    namespace = mapping._text(namespace, "namespace", mapping.NAMESPACE)
    root = Element(names[0], xmlns=namespace)
    node = root
    for name in names[1:]:
        node = SubElement(node, name)
    return tostring(root, encoding="unicode")


def browse(actor, device_id, payload: BrowseRequest):
    """Read one subtree of a device as it is, to see what a definition would map. Read
    only: the platform session sends nothing but a filtered <get> or <get-config>."""
    from src.platform.device_access.contracts import DeviceAccessError

    if payload.datastore not in mapping.DATASTORES:
        raise HTTPException(422, f"数据源只能是 {'、'.join(sorted(mapping.DATASTORES))}")
    try:
        subtree = browse_filter(payload.namespace, payload.path)
    except mapping.DefinitionError as exc:
        raise HTTPException(422, f"路径不可用：{exc}") from None
    device = _device(device_id)
    started = now()
    try:
        with _netconf(actor, device) as (session, guard):
            guard()
            if payload.datastore == "running":
                data = session.get_config(subtree, source="running")
            else:
                data = session.get(subtree)
            capabilities = session.capabilities
    except DeviceAccessError as exc:
        return {
            "status": "failed",
            "failure_code": exc.failure_code,
            "message": str(exc)[:500],
            "filter": subtree,
        }
    _record_capabilities(actor, device_id, capabilities)
    finished = now()
    return {
        "status": "succeeded",
        "filter": subtree,
        "datastore": payload.datastore,
        "data": data,
        "reply_bytes": len(data.encode("utf-8")),
        "elapsed_ms": round((finished - started).total_seconds() * 1000),
    }


def read_capabilities(actor, device_id):
    with sessions() as session:
        row = session.get(Capability, device_id)
        if row is None:
            raise HTTPException(404, "尚未读取该设备的 NETCONF 能力")
        return _capability_view(row, published(session))


# --- trials ----------------------------------------------------------------------------


def _trial_view(row, *, rows=True):
    view = {
        key: getattr(row, key)
        for key in (
            "id",
            "device_id",
            "definition_id",
            "version",
            "body_sha256",
            "status",
            "failure_code",
            "message",
            "read_count",
            "row_count",
            "problem_count",
            "reply_bytes",
        )
    }
    view["started_at"] = row.started_at.isoformat() if row.started_at else None
    view["finished_at"] = row.finished_at.isoformat() if row.finished_at else None
    if rows:
        view.update(rows=row.rows, problems=row.problems, filters=row.filters)
    return view


def trial(actor, payload: TrialRequest):
    """Run one definition against one device and keep what it gave."""
    from src.platform.device_access.contracts import DeviceAccessError

    device = _device(payload.device_id)
    with sessions() as session:
        bodies = {key: v.body for key, v in _published_bodies(session).items()}
        version = None
        if payload.draft:
            row = _owned(session, payload.definition_id)
            if row.draft is None:
                raise HTTPException(409, "没有草稿可试运行")
            bodies[payload.definition_id] = row.draft
        elif payload.definition_id not in bodies:
            raise HTTPException(404, "定义尚未发布")
        else:
            version = _published_bodies(session)[payload.definition_id].version
    try:
        definitions = mapping.load_all(bodies.values())
    except mapping.DefinitionError as exc:
        raise HTTPException(422, f"定义不可用：{exc}") from None
    definition = definitions[payload.definition_id]
    vendor, platform = str(device.get("vendor") or ""), str(device.get("platform") or "")
    if vendor.casefold() != definition.vendor.casefold() or platform not in definition.platforms:
        raise HTTPException(
            409,
            f"定义适用于 {definition.vendor} {'/'.join(definition.platforms)}，"
            f"设备是 {vendor} {platform}",
        )
    fields = None
    if definition.check_item:
        fields = contract_fields(definition, platform, device.get("model"))
        if fields is None:
            raise HTTPException(
                409, f"规则治理没有为 {platform} 发布检查项 {definition.check_item}"
            )
    record = Trial(
        id=uuid4().hex,
        owner_id=actor.user_id,
        device_id=payload.device_id,
        definition_id=payload.definition_id,
        version=version,
        body_sha256=digest(bodies[payload.definition_id]),
        started_at=now(),
    )
    filters = []
    received = 0  # bytes of the <data> replies, lookups included
    capabilities = None
    try:
        with _netconf(actor, device) as (session, guard):
            capabilities = session.capabilities
            read = mapping.fetcher(session)

            def fetch(table, subtree):
                nonlocal received
                guard()
                filters.append({"definition": table.id, "filter": subtree})
                data = read(table, subtree)
                received += len(data.encode("utf-8"))
                return data

            mapped = mapping.collect(definitions, definition.id, fetch, fields=fields)
    except DeviceAccessError as exc:
        record.status, record.failure_code, record.message = (
            "failed",
            exc.failure_code,
            str(exc)[:500],
        )
        if capabilities is None:
            _record_capabilities(actor, payload.device_id, (), exc.failure_code)
    except mapping.MappingError as exc:
        record.status, record.failure_code, record.message = (
            "failed",
            "reply_unreadable",
            str(exc)[:500],
        )
    except mapping.DefinitionError as exc:
        record.status, record.failure_code, record.message = (
            "failed",
            "definition_invalid",
            str(exc)[:500],
        )
    else:
        record.status, record.read_count = "succeeded", mapped.read
        record.row_count, record.problem_count = len(mapped.rows), len(mapped.problems)
        record.rows = [{"ref": ref, **row} for ref, row in zip(mapped.refs, mapped.rows)][
            :TRIAL_ROWS
        ]
        record.problems = [asdict(problem) for problem in mapped.problems][:TRIAL_ROWS]
    if capabilities is not None:
        _record_capabilities(actor, payload.device_id, capabilities)
    record.filters, record.finished_at = filters, now()
    record.reply_bytes = received if filters else None
    record.rows, record.problems = record.rows or [], record.problems or []
    with sessions() as session:
        session.add(record)
        session.flush()
        return _trial_view(record)


def read_trial(actor, trial_id):
    with sessions() as session:
        row = session.get(Trial, trial_id)
        if row is None or row.owner_id != actor.user_id:
            raise HTTPException(404, "试运行记录不存在")
        return _trial_view(row)


def list_trials(actor, definition_id=None, limit=50):
    with sessions() as session:
        query = select(Trial).where(Trial.owner_id == actor.user_id)
        if definition_id:
            query = query.where(Trial.definition_id == definition_id)
        rows = session.scalars(query.order_by(Trial.started_at.desc()).limit(min(limit, 200)))
        return [_trial_view(row, rows=False) for row in rows]


# --- seeds -----------------------------------------------------------------------------


def seed_bodies():
    import yaml

    bodies = []
    for path in sorted(SEEDS.glob("*.yaml")):
        bodies += yaml.safe_load(path.read_text(encoding="utf-8"))["definitions"]
    mapping.load_all(bodies)  # a broken seed fails the bootstrap, not a later trial
    return bodies


def import_seeds(session):
    """Publish each seed definition that is new, or whose published version is an older
    seed; a version someone published themselves is kept."""
    added = []
    for body in seed_bodies():
        definition = mapping.load(body)
        row = session.get(Definition, definition.id, with_for_update=True)
        if row is None:
            row = Definition(id=definition.id, revision=0)
            session.add(row)
        current = (
            session.get(DefinitionVersion, (definition.id, row.published_version))
            if row.published_version
            else None
        )
        if current is not None and (
            current.published_by != "system" or current.sha256 == digest(body)
        ):
            continue
        latest = session.scalar(
            select(func.max(DefinitionVersion.version)).where(
                DefinitionVersion.definition_id == definition.id
            )
        )
        version = DefinitionVersion(
            definition_id=definition.id,
            version=(latest or 0) + 1,
            body=body,
            sha256=digest(body),
            check_item=definition.check_item,
            published_by="system",
            published_at=now(),
            note="seed",
        )
        session.add(version)
        session.flush()
        row.published_version = version.version
        row.revision = (row.revision or 0) + 1
        added.append(definition.id)
    return added
