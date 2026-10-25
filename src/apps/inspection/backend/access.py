"""One application admission; business validation remains in the services."""

from src.platform.authorization.client import authorize_worker, require_app

APP_ID = "inspection"
app_user = require_app(APP_ID)


def authorize(user_id):
    if not user_id:
        from fastapi import HTTPException

        raise HTTPException(403, "历史任务缺少执行用户，请由有应用权限的用户重新提交")
    return authorize_worker(str(user_id), APP_ID)


def require_running():
    """System maintenance may run only for the enabled deployed package."""
    import httpx
    from fastapi import HTTPException

    from src.platform.authorization.client import authority_url
    from src.platform.kernel.runtime import snapshot, token

    try:
        response = httpx.post(
            authority_url() + "/api/platform/apps/runtime/",
            json={"app_id": APP_ID, "digest": snapshot().get(APP_ID)},
            headers={"Authorization": "Bearer " + token()},
            timeout=5,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "巡检应用未启用或授权服务不可用") from exc


def maintenance(fn):
    from functools import wraps

    @wraps(fn)
    def run(*args, **kwargs):
        with execution_lock():
            require_running()
            return fn(*args, **kwargs)

    return run


from contextlib import contextmanager


@contextmanager
def execution_lock(*, exclusive=False):
    """Shared execution / exclusive drain lock, including generator subprocesses."""
    from sqlalchemy import text

    from src.infrastructure.db.postgres import engine

    with engine.begin() as connection:
        sql = (
            "SELECT pg_try_advisory_xact_lock(728031925)"
            if exclusive
            else "SELECT pg_try_advisory_xact_lock_shared(728031925)"
        )
        if not connection.scalar(text(sql)):
            raise ValueError("巡检仍在执行或正在排空，请稍后重试")
        yield


def inspection_batch(batch_id):
    from src.infrastructure.db.models import PatrolBatchRecord
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as db:
        batch = db.get(PatrolBatchRecord, batch_id)
        return bool(batch and not (batch.selector_json or {}).get("rollout_stage_execution_id"))


def authorize_batch(batch_id):
    from fastapi import HTTPException

    from src.infrastructure.db.models import PatrolBatchRecord
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as db:
        batch = db.get(PatrolBatchRecord, batch_id)
        if not batch or (batch.selector_json or {}).get("rollout_stage_execution_id"):
            raise HTTPException(403, "任务不属于巡检应用")
        actor = ((batch.selector_json or {}).get("inspection") or {}).get("user_id")
    return authorize(actor)


def authorize_authority_request(request_id):
    from src.infrastructure.db.models import PatrolCurrentAuthorityRequestRecord
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as db:
        row = db.get(PatrolCurrentAuthorityRequestRecord, request_id)
        actor = row.requested_by_principal_id if row else None
    return authorize(actor)


@contextmanager
def diagnostic_admission(plan_id):
    from fastapi import HTTPException

    from src.infrastructure.db.models import CollectionExecutionPlanRecord
    from src.infrastructure.db.postgres import get_db_session

    with execution_lock():
        with get_db_session() as db:
            plan = db.get(CollectionExecutionPlanRecord, plan_id) if plan_id else None
            if plan is None or plan.mode == "production":
                raise HTTPException(403, "正式巡检必须使用已冻结的固定执行任务")
            batch_id = plan.patrol_batch_id
        yield authorize_batch(batch_id)
