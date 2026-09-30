"""One application admission; business validation remains in the services."""

from src.platform.authorization.client import authorize_worker, require_app

APP_ID = "rule-governance"
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
        raise HTTPException(503, "规则治理应用未启用或授权服务不可用") from exc


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
            "SELECT pg_try_advisory_xact_lock(728031924)"
            if exclusive
            else "SELECT pg_try_advisory_xact_lock_shared(728031924)"
        )
        if not connection.scalar(text(sql)):
            raise ValueError("规则治理仍在执行或正在排空，请稍后重试")
        yield


def capture_admission(fn):
    """Apply the rule App boundary only to its inventory capture requests."""
    from functools import wraps
    from inspect import signature

    @wraps(fn)
    def run(*args, **kwargs):
        plan = signature(fn).bind(*args, **kwargs).arguments.get("execution_plan") or {}
        if "rule_governance_actor_id" not in plan:
            from src.apps.inspection.backend.access import diagnostic_admission
            plan_id = signature(fn).bind(*args, **kwargs).arguments.get("execution_plan_id")
            with diagnostic_admission(plan_id) as actor:
                kwargs["ssh_actor_id"] = str(getattr(actor, "user_id", "system"))
                kwargs["ssh_source_app"] = "inspection"
                return fn(*args, **kwargs)
        from src.infrastructure.db.models import RolloutStageExecutionRecord
        from src.infrastructure.db.postgres import get_db_session

        with execution_lock():
            with get_db_session() as db:
                stage = db.get(RolloutStageExecutionRecord, plan.get("rule_governance_stage_id"))
                if stage is None or stage.status not in {"running", "capturing"}:
                    raise ValueError("规则灰度采集已结束或取消")
                principal = (stage.selection_manifest_json or {}).get("execution_user_id")
            actor = authorize(principal)
            kwargs["ssh_actor_id"] = str(getattr(actor, "user_id", principal))
            kwargs["ssh_source_app"] = APP_ID
            return fn(*args, **kwargs)

    return run
