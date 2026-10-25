"""Durable policy/ticket runs; admission authorization is provided by the platform."""

import json
import logging
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from . import storage as blobs
from .models import Run, Upload, now

APP_ID = "firewall-policy"
log = logging.getLogger(__name__)
DEFAULT_TOPOLOGY = Path(__file__).parent / "engine/policy/scripts/topology.json"
BLOCK_VENDORS = {"华为", "H3C", "H3CF1000"}


@contextmanager
def sessions():
    from sqlalchemy.orm import Session

    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def owned(session, model, identity, owner):
    row = session.get(model, identity)
    if not row or row.owner_id != str(owner):
        raise HTTPException(404, "记录不存在")
    return row


def describe(row):
    artifacts = (
        {
            key: {
                "filename": item["filename"],
                "sha256": item["sha256"],
                "download_url": f"/api/apps/{APP_ID}/runs/{row.id}/artifacts/{key}",
            }
            for key, item in (row.artifacts_json or {}).items()
        }
        if row.status == "completed"
        else {}
    )
    return dict(
        id=row.id,
        kind=row.kind,
        parent_id=row.parent_id,
        status=row.status,
        input=row.input_json.get("request", {}),
        error=row.error,
        attempts=row.attempts,
        created_at=row.created_at,
        updated_at=row.updated_at,
        result=row.result_json if row.status == "completed" else None,
        artifacts=artifacts,
    )


def dispatch(identity):
    from .tasks import execute

    try:
        execute.apply_async(args=[identity], queue="app.firewall-policy")
    except Exception:
        log.warning("Firewall run %s remains queued for reconciliation", identity, exc_info=True)


def save_upload(owner, filename, kind, data, request_id):
    blobs.validate(filename, kind, data)
    sha = blobs.digest(data)
    identity = uuid4().hex
    with sessions() as session:
        query = select(Upload).where(Upload.owner_id == str(owner), Upload.request_id == request_id)
        row = session.scalar(query)
        if row is None:
            key = f"{blobs.PREFIX}uploads/{identity}/{sha}"
            blobs.put(key, data)
            try:
                with session.begin_nested():
                    row = Upload(
                        id=identity,
                        owner_id=str(owner),
                        request_id=request_id,
                        kind=kind,
                        filename=filename,
                        sha256=sha,
                        size=len(data),
                        object_key=key,
                    )
                    session.add(row)
                    session.flush()
            except IntegrityError:
                row = session.scalar(query)
        if row.sha256 != sha or row.kind != kind or row.filename != filename:
            raise HTTPException(409, "上传请求标识已用于其他文件")
        return dict(
            id=row.id, filename=row.filename, kind=row.kind, sha256=row.sha256, size=row.size
        )


def _submit(owner, request_id, kind, request, prepare, parent_id=None):
    fingerprint = blobs.digest(
        json.dumps([kind, parent_id, request], sort_keys=True, ensure_ascii=False).encode()
    )
    with sessions() as session:
        query = select(Run).where(Run.owner_id == str(owner), Run.request_id == request_id)
        row = session.scalar(query)
        if row is None:
            inputs = prepare(session)
            try:
                with session.begin_nested():
                    row = Run(
                        id=uuid4().hex,
                        owner_id=str(owner),
                        request_id=request_id,
                        kind=kind,
                        parent_id=parent_id,
                        input_hash=fingerprint,
                        input_json={**inputs, "request": request},
                        status="queued",
                    )
                    session.add(row)
                    session.flush()
            except IntegrityError:
                row = session.scalar(query)
        if row.input_hash != fingerprint:
            raise HTTPException(409, "请求标识已用于其他内容")
        result = describe(row)
    if result["status"] == "queued":
        dispatch(result["id"])
    return result


def submit_policy(owner, payload):
    request = payload.model_dump(exclude={"request_id"})

    def prepare(session):
        policy = owned(session, Upload, payload.policy_upload_id, owner)
        if policy.kind != "policy":
            raise HTTPException(422, "请选择策略申请表")
        inputs = {
            "policy": {key: getattr(policy, key) for key in ["object_key", "sha256", "filename"]}
        }
        if payload.topology_upload_id:
            topology = owned(session, Upload, payload.topology_upload_id, owner)
            if topology.kind != "topology":
                raise HTTPException(422, "请选择拓扑文件")
            inputs["topology"] = {key: getattr(topology, key) for key in ["object_key", "sha256"]}
        else:
            # Freeze the configured topology at admission, even across package upgrades.
            inputs["default_topology"] = json.loads(
                DEFAULT_TOPOLOGY.read_text(encoding="utf-8-sig")
            )
        return inputs

    return _submit(owner, payload.request_id, "policy", request, prepare)


def submit_block(owner, payload):
    request = payload.model_dump(exclude={"request_id"})

    def prepare(session):
        inputs = {}
        for key, label in [("block", "封禁名单"), ("whitelist", "白名单")]:
            identity = getattr(payload, f"{key}_upload_id")
            if identity:
                upload = owned(session, Upload, identity, owner)
                if upload.kind != key:
                    raise HTTPException(422, f"请选择{label}文件")
                inputs[key] = {k: getattr(upload, k) for k in ["object_key", "sha256", "filename"]}
        return inputs

    return _submit(owner, payload.request_id, "block", request, prepare)


def block_devices():
    """Firewalls of the configured topology; blocking renders Huawei and H3C commands."""
    topology = json.loads(DEFAULT_TOPOLOGY.read_text(encoding="utf-8-sig"))
    return [
        dict(
            name=item["name"],
            vendor=item.get("type") or "",
            supported=item.get("type") in BLOCK_VENDORS,
        )
        for item in topology.get("firewalls", [])
        if item.get("name")
    ]


def submit_ticket(owner, parent_id, payload):
    def prepare(session):
        parent = owned(session, Run, parent_id, owner)
        if parent.kind not in {"policy", "block"} or parent.status != "completed":
            raise HTTPException(409, "请先完成策略或封禁命令生成")
        return {}  # Only the persisted parent plan supplies device commands.

    return _submit(
        owner,
        payload.request_id,
        "ticket",
        payload.model_dump(exclude={"request_id"}),
        prepare,
        parent_id,
    )


def read(owner, identity):
    with sessions() as session:
        result = describe(owned(session, Run, identity, owner))
        result["tickets"] = [
            describe(row)
            for row in session.scalars(
                select(Run)
                .where(Run.parent_id == identity, Run.kind == "ticket")
                .order_by(Run.created_at.desc())
                .limit(50)
            )
        ]
        return result


def history(owner, offset=0, limit=20, kind="policy"):
    with sessions() as session:
        query = select(Run).where(Run.owner_id == str(owner), Run.kind == kind)
        return {
            "items": [
                describe(row)
                for row in session.scalars(
                    query.order_by(Run.created_at.desc(), Run.id).offset(offset).limit(limit)
                )
            ],
            "total": session.scalar(select(func.count()).select_from(query.subquery())),
        }


def cancel(owner, identity):
    with sessions() as session:
        owned(session, Run, identity, owner)
        session.execute(
            update(Run)
            .where(Run.id == identity, Run.status.in_(["queued", "running"]))
            .values(status="cancelled", error="任务已取消", updated_at=now())
        )
    return read(owner, identity)


def retry(owner, identity, payload):
    with sessions() as session:
        row = owned(session, Run, identity, owner)
        if row.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "只有失败或取消的任务可以重试")
        inputs, kind, parent_id = row.input_json, row.kind, row.parent_id
    return _submit(
        owner,
        payload.request_id,
        kind,
        {**inputs["request"], "retry_of": identity},
        lambda session: inputs,
        parent_id,
    )


def download(owner, identity, key):
    with sessions() as session:
        row = owned(session, Run, identity, owner)
        item = (row.artifacts_json or {}).get(key) if row.status == "completed" else None
        if item is None:
            raise HTTPException(404, "产物不存在")
    return blobs.get(item["object_key"], item["sha256"]), item["filename"], item["content_type"]


def eligible():
    return or_(Run.status == "queued", (Run.status == "running") & (Run.lease_until < now()))


class StoppedError(Exception):
    pass


def execute_run(identity, authorize):
    from .engine_adapter import generate

    token = uuid4().hex
    with sessions() as session:
        row = session.get(Run, identity)
        if row is None or row.status not in {"queued", "running"}:
            return
        owner = row.owner_id
        try:
            authorize(owner, APP_ID)
        except HTTPException as exc:
            if exc.status_code != 503:
                session.execute(
                    update(Run)
                    .execution_options(synchronize_session=False)
                    .where(Run.id == identity, eligible())
                    .values(status="cancelled", error="应用权限已撤销", updated_at=now())
                )
            return
        session.execute(
            update(Run)
            .execution_options(synchronize_session=False)
            .where(Run.id == identity, eligible(), Run.attempts >= 3)
            .values(status="failed", error="执行多次中断，请重试", updated_at=now())
        )
        claimed = session.execute(
            update(Run)
            .execution_options(synchronize_session=False)
            .where(Run.id == identity, eligible(), Run.attempts < 3)
            .values(
                status="running",
                attempts=Run.attempts + 1,
                lease_token=token,
                lease_until=now() + timedelta(seconds=60),
                updated_at=now(),
            )
        )
        if claimed.rowcount != 1:
            return
        session.refresh(row)
        inputs, kind = row.input_json, row.kind
        parent = owned(session, Run, row.parent_id, owner) if row.parent_id else None
        parent_run = parent and dict(result=parent.result_json, artifacts=parent.artifacts_json)

    def active():
        return (Run.id == identity) & (Run.lease_token == token) & (Run.status == "running")

    def checkpoint():
        authorize(owner, APP_ID)
        with sessions() as session:
            count = session.execute(
                update(Run)
                .where(active())
                .values(lease_until=now() + timedelta(seconds=60), updated_at=now())
            ).rowcount
            if count != 1:
                raise StoppedError()

    try:
        checkpoint()
        result, files = generate(kind, inputs, parent_run, checkpoint)
        artifacts = {}
        for key, (filename, content_type, data) in files.items():
            checkpoint()
            sha = blobs.digest(data)
            object_key = f"{blobs.PREFIX}runs/{identity}/{token}/{sha}"
            blobs.put(object_key, data, content_type)
            artifacts[key] = dict(
                filename=filename, content_type=content_type, sha256=sha, object_key=object_key
            )
        checkpoint()
        with sessions() as session:
            session.execute(
                update(Run)
                .where(active())
                .values(
                    status="completed",
                    error=None,
                    result_json=result,
                    artifacts_json=artifacts,
                    lease_until=None,
                    updated_at=now(),
                )
            )
    except StoppedError:
        return
    except Exception as exc:
        if isinstance(exc, HTTPException) and exc.status_code == 503:
            return  # Reconciler recovers after the lease; bounded to three attempts.
        status = (
            "cancelled"
            if isinstance(exc, HTTPException) and exc.status_code in {401, 403, 404}
            else "failed"
        )
        error = (
            "应用权限已撤销" if status == "cancelled" else str(getattr(exc, "detail", exc))[:2000]
        )
        log.warning("Firewall run %s failed", identity, exc_info=True)
        with sessions() as session:
            session.execute(
                update(Run).where(active()).values(status=status, error=error, updated_at=now())
            )


def reconcile():
    with sessions() as session:
        identities = list(
            session.scalars(select(Run.id).where(eligible()).order_by(Run.created_at).limit(100))
        )
    for identity in identities:
        dispatch(identity)
    return len(identities)


def drain():
    with sessions() as session:
        session.execute(
            update(Run)
            .where(eligible())
            .values(status="cancelled", error="应用已停用", updated_at=now())
        )


def pending():
    with sessions() as session:
        return session.scalar(
            select(func.count()).select_from(Run).where(Run.status.in_(["queued", "running"]))
        )
