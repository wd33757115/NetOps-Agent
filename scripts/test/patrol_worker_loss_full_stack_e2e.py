"""Prove RabbitMQ redelivery and PatrolRun fencing after a hard worker loss."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery as app
from src.core.patrol.run_coordinator import PatrolRunCoordinator
from src.core.patrol.run_repository import PatrolRunRepository
from src.infrastructure.db.models import PatrolRun
from src.infrastructure.db.postgres import get_db_session

TASK_NAME = "netops.test.patrol_worker_loss_probe"


@app.task(
    bind=True,
    name=TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
)
def patrol_worker_loss_probe(self, first_attempt_hold_seconds: int = 120):
    """Hold the first delivery and complete only after broker redelivery."""

    decision = PatrolRunCoordinator().prepare_delivery(
        self.request,
        mode="production",
    )
    task_id = str(self.request.id)
    logical_run_id = f"patrol-{task_id}"
    run_id = str(decision.get("run_id") or logical_run_id)
    owner_token = str(decision.get("owner_token") or "")
    worker_id = str(getattr(self.request, "hostname", "") or "")
    attempt_number = int(decision.get("attempt_number") or 0)
    repository = PatrolRunRepository()
    repository.create_run(
        run_id=run_id,
        source="device-patrol",
        mode="production",
        logical_run_id=logical_run_id,
        attempt_number=attempt_number,
        worker_id=worker_id,
        owner_token=owner_token,
        owner_generation=attempt_number + 1,
        lease_seconds=300,
        metadata={
            "fault_injection": "worker_loss",
            "delivery_redelivered": bool(
                dict(getattr(self.request, "delivery_info", None) or {}).get(
                    "redelivered"
                )
            ),
        },
    )
    if attempt_number == 0:
        time.sleep(max(1, int(first_attempt_hold_seconds)))
        raise RuntimeError("first delivery unexpectedly survived the hard kill")

    repository.finish_run(
        run_id,
        collection_status="completed",
        analysis_status="completed",
        baseline_eligible=False,
        metadata={"fault_injection_recovered": True},
        owner_token=owner_token,
    )
    PatrolRunCoordinator(repository).complete(
        run_id,
        owner_token=owner_token,
    )
    return {
        "success": True,
        "logical_run_id": logical_run_id,
        "run_id": run_id,
        "attempt_number": attempt_number,
        "worker_id": worker_id,
    }


def _delete_queue(queue: str) -> None:
    try:
        with app.connection_for_write() as connection:
            channel = connection.channel()
            channel.queue_delete(queue=queue)
    except Exception:
        pass


def _start_worker(*, queue: str, hostname: str, log_path: Path) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "scripts.test.patrol_worker_loss_full_stack_e2e:app",
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=WARNING",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
        "--queues",
        queue,
        "--hostname",
        hostname,
    ]
    log_handle = log_path.open("ab")
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=(
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ),
    )
    process._netops_log_handle = log_handle  # type: ignore[attr-defined]
    return process


def _stop_worker(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
    except Exception:
        pass
    handle = getattr(process, "_netops_log_handle", None)
    if handle is not None:
        handle.close()


def _runs(logical_run_id: str) -> list[dict[str, Any]]:
    with get_db_session() as db:
        rows = (
            db.query(PatrolRun)
            .filter(PatrolRun.logical_run_id == logical_run_id)
            .order_by(PatrolRun.attempt_number)
            .all()
        )
        return [row.to_dict() for row in rows]


def _wait_for(
    predicate,
    *,
    timeout_seconds: float,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {description}")


def main() -> int:
    suffix = uuid.uuid4().hex[:12]
    queue = f"netops.test.worker-loss.{suffix}"
    task_id = f"worker-loss-{suffix}"
    logical_run_id = f"patrol-{task_id}"
    output_dir = ROOT / ".tmp" / "patrol-worker-loss-full-stack-e2e"
    output_dir.mkdir(parents=True, exist_ok=True)
    first_log = output_dir / f"worker-a-{suffix}.log"
    second_log = output_dir / f"worker-b-{suffix}.log"
    worker_a: subprocess.Popen | None = None
    worker_b: subprocess.Popen | None = None
    summary: dict[str, Any] = {
        "schema_version": "patrol-worker-loss-e2e/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": queue,
        "task_id": task_id,
        "logical_run_id": logical_run_id,
        "success": False,
    }
    try:
        _delete_queue(queue)
        worker_a = _start_worker(
            queue=queue,
            hostname=f"worker-loss-a-{suffix}@%h",
            log_path=first_log,
        )
        time.sleep(2)
        app.send_task(
            TASK_NAME,
            args=[120],
            task_id=task_id,
            queue=queue,
        )
        first = _wait_for(
            lambda: next(
                (
                    row
                    for row in _runs(logical_run_id)
                    if int(row.get("attempt_number") or 0) == 0
                    and row.get("status") == "running"
                ),
                None,
            ),
            timeout_seconds=45,
            description="the first worker to own attempt 0",
        )
        summary["first_attempt_before_kill"] = first
        _stop_worker(worker_a)
        worker_a = None

        worker_b = _start_worker(
            queue=queue,
            hostname=f"worker-loss-b-{suffix}@%h",
            log_path=second_log,
        )
        recovered = _wait_for(
            lambda: next(
                (
                    row
                    for row in _runs(logical_run_id)
                    if int(row.get("attempt_number") or 0) >= 1
                    and row.get("status") == "completed"
                ),
                None,
            ),
            timeout_seconds=60,
            description="the redelivered attempt to complete",
        )
        final_runs = _runs(logical_run_id)
        if len(final_runs) != 2:
            raise AssertionError(f"expected two attempts, got {len(final_runs)}")
        if final_runs[0].get("status") != "abandoned":
            raise AssertionError("the killed worker attempt was not abandoned")
        if (
            dict(final_runs[0].get("metadata_json") or {}).get(
                "abandoned_reason"
            )
            != "celery_worker_lost_redelivery"
        ):
            raise AssertionError("the abandoned attempt has the wrong reason")
        if int(recovered.get("attempt_number") or 0) != 1:
            raise AssertionError("redelivery did not allocate attempt 1")
        summary.update(
            {
                "success": True,
                "final_runs": final_runs,
                "worker_logs": [str(first_log), str(second_log)],
            }
        )
        return 0
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["final_runs"] = _runs(logical_run_id)
        return 1
    finally:
        _stop_worker(worker_a)
        _stop_worker(worker_b)
        _delete_queue(queue)
        with get_db_session() as db:
            db.query(PatrolRun).filter(
                PatrolRun.logical_run_id == logical_run_id
            ).delete(synchronize_session=False)
        summary["cleanup"] = {
            "queue_deleted": True,
            "patrol_run_count": len(_runs(logical_run_id)),
        }
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
