"""Backend-only, policy-checked SSH sessions with bounded lifetime and durable audit."""

import hashlib
import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from threading import Lock, RLock, Timer
from uuid import uuid4

from .catalog import TERMINAL_PREPARATION
from .command_policy import classify
from .contracts import CommandResult, DeviceAccessError
from .errors import failure_code
from .governance import operation_for
from .policy import validate_profile


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionBudget:
    total_seconds: float = 600
    idle_seconds: float = 60
    max_commands: int = 60
    max_probes: int = 10

    def validate(self):
        for value, maximum in ((self.total_seconds, 600), (self.idle_seconds, 60)):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise DeviceAccessError("invalid_request", "invalid session time budget")
        for value, maximum in ((self.max_commands, 60), (self.max_probes, 10)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise DeviceAccessError("invalid_request", "invalid session command budget")


@dataclass(frozen=True)
class SessionCommandResult(CommandResult):
    policy_level: str = "L4"
    normalized_command: str = ""


class SessionHandle:
    """An opaque ID may cross the tool boundary; this object and transport may not."""

    def __init__(
        self,
        service,
        context,
        target,
        profile,
        *,
        platform,
        purpose,
        budget,
        audit,
        should_continue=None,
        required_vrf=None,
    ):
        self.session_id = uuid4().hex
        self._service, self._context = service, context
        self._target, self._profile = target, profile
        self._platform, self._purpose = platform, purpose
        self._budget, self._audit = budget, audit
        self._continue, self._vrf = should_continue, required_vrf
        self._deadline = time.monotonic() + budget.total_seconds
        self._state, self._run_lock = RLock(), Lock()
        self._closed, self._running = False, False
        self._reason = "session_closed"
        self._commands, self._probes = 0, 0
        self._connection, self._lease, self._check_lease = None, None, None
        self._idle_timer, self._total_timer = None, None
        self._operation = operation_for(context, target, profile, session_id=self.session_id)

    def _redact(self, text):
        text = str(text or "")
        redact = getattr(self._connection, "redact", None)
        if callable(redact):
            text = redact(text)
        if self._profile.password:
            text = text.replace(self._profile.password, "[redacted]")
        return self._operation.redact(text)

    def _emit(self, kind, **payload):
        event = dict(
            id=uuid4().hex,
            session_id=self.session_id,
            owner_id=self._context.actor_id,
            source_app=self._context.source_app,
            target_ref=self._target.target_ref,
            connection_profile_id=self._profile.profile_id,
            platform=self._platform,
            # Vault redaction material is available only after the handshake.
            purpose=self._redact(self._purpose) if self._connection is not None else None,
            kind=kind,
            observed_at=now(),
            **payload,
        )
        try:
            return self._audit(event)
        except Exception:
            raise DeviceAccessError("audit_unavailable", "session audit is unavailable") from None

    def _guard(self):
        with self._state:
            if self._closed:
                raise DeviceAccessError(self._reason, "SSH session is closed")
        if time.monotonic() >= self._deadline:
            raise DeviceAccessError("budget_exceeded", "SSH session time budget exhausted")
        if self._check_lease is not None:
            self._check_lease()
        if self._continue is not None and self._continue() is False:
            raise DeviceAccessError("cancelled", "SSH session cancelled")

    def _open(self):
        try:
            self._guard()
            self._operation.event("connecting", mode="diagnosis")
            self._emit(
                "opening",
                approved_setup_commands=list(TERMINAL_PREPARATION[self._platform]),
            )  # Audit failure must prevent network activity.
            self._lease = self._service.admission.hold(self._target, self._profile)
            try:
                self._check_lease = self._lease.__enter__()
            except Exception:
                self._lease = None
                raise
            self._guard()
            remaining = self._deadline - time.monotonic()
            profile = replace(
                self._profile,
                platform=self._platform,
                connection_attempts=1,
                conn_timeout=min(self._profile.conn_timeout, remaining),
                auth_timeout=min(self._profile.auth_timeout, remaining),
                banner_timeout=min(self._profile.banner_timeout, remaining),
                prompt_timeout=min(self._profile.prompt_timeout, remaining),
                session_setup_timeout=min(self._profile.session_setup_timeout, remaining),
            )
            with self._operation.activate():
                self._connection = self._service.driver.connect(self._target, profile)
            self._guard()
            self._operation.event("connected", mode="diagnosis")
            self._emit(
                "opened",
                prompt=self._redact(self._connection.find_prompt()),
                setup_commands=list(getattr(self._connection, "initialization_commands", ())),
            )
            with self._state:
                self._total_timer = Timer(
                    max(0, self._deadline - time.monotonic()),
                    self._expire,
                    args=("budget_exceeded",),
                )
                self._total_timer.daemon = True
                self._total_timer.start()
                self._arm_idle()
            return self
        except Exception as exc:
            code = failure_code(exc)
            try:
                try:
                    self._operation.event("connection_failed", status="failed", failure_code=code)
                finally:
                    self.close(code)
            except Exception:
                pass
            raise DeviceAccessError(code, "SSH session could not be opened") from None

    def _arm_idle(self):
        # Called with state lock; an old timer may fire after cancellation.
        if self._closed:
            return
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_deadline = time.monotonic() + self._budget.idle_seconds
        self._idle_timer = Timer(self._budget.idle_seconds, self._expire, args=("idle_timeout",))
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _expire(self, reason):
        with self._state:
            if reason == "idle_timeout" and (
                self._running or time.monotonic() < self._idle_deadline
            ):
                return
        try:
            self.close(reason)
        except Exception:
            # Cleanup is already complete; retain the failure for the next caller.
            with self._state:
                self._reason = "audit_unavailable"

    @property
    def closed(self):
        with self._state:
            return self._closed

    def close(self, reason="session_closed"):
        with self._state:
            if self._closed:
                return
            self._closed, self._reason = True, reason
            for timer in (self._idle_timer, self._total_timer):
                if timer is not None:
                    timer.cancel()
        try:
            if self._connection is not None:
                try:
                    self._connection.disconnect()
                except (EOFError, OSError):
                    pass  # The peer may have already closed the transport.
        finally:
            try:
                if self._lease is not None:
                    self._lease.__exit__(None, None, None)
            finally:
                try:
                    self._emit(
                        "closed",
                        close_reason=reason,
                        failure_code=None if reason == "session_closed" else reason,
                        commands=self._commands,
                        probes=self._probes,
                    )
                finally:
                    self._operation.event("disconnected", mode="diagnosis", reason=reason)

    def __enter__(self):
        self._guard()
        return self

    def __exit__(self, *args):
        self.close()

    def run(self, command_text, *, read_timeout=30):
        if not self._run_lock.acquire(blocking=False):
            raise DeviceAccessError("session_busy", "SSH session already has an active command")
        try:
            return self._run(command_text, read_timeout)
        finally:
            with self._state:
                self._running = False
                self._arm_idle()
            self._run_lock.release()

    def _run(self, command_text, read_timeout):
        try:
            self._guard()
            if not isinstance(command_text, str) or not 0 < len(command_text) <= 4096:
                raise DeviceAccessError("invalid_request", "invalid command text")
            if (
                type(read_timeout) not in (int, float)
                or not math.isfinite(read_timeout)
                or not 0 < read_timeout <= 120
            ):
                raise DeviceAccessError("invalid_request", "invalid command timeout")
            with self._state:
                self._running = True
                if self._commands >= self._budget.max_commands:
                    raise DeviceAccessError("budget_exceeded", "command budget exhausted")
                self._commands += 1
            decision = classify(self._platform, command_text, required_vrf=self._vrf)
            identifier = uuid4().hex
            fields = dict(
                command_id=identifier,
                command=self._redact(command_text),
                normalized_command=self._redact(decision.normalized),
                policy_level=decision.level,
            )
            self._emit("command_requested", **fields)
            self._guard()
            output, code, error, started = "", None, None, None
            status = "succeeded"
            if decision.level not in {"L1", "L2"}:
                status = "pending_approval" if decision.level == "L3" else "denied"
                code = "approval_required" if decision.level == "L3" else "policy_denied"
                error = decision.reason
                self._operation.event(
                    "command_denied",
                    status=status,
                    command=command_text,
                    policy_level=decision.level,
                    failure_code=code,
                )
            elif decision.level == "L2" and self._probes >= self._budget.max_probes:
                status, code, error = "denied", "probe_budget_exceeded", "probe budget exhausted"
            else:
                if decision.level == "L2":
                    self._probes += 1
                started = now()
                try:
                    output = self._redact(
                        self._operation.execute(
                            decision.normalized,
                            lambda: self._connection.send_command(
                                decision.normalized,
                                read_timeout=min(
                                    read_timeout, max(0.001, self._deadline - time.monotonic())
                                ),
                            ),
                        )
                    )
                except Exception as exc:
                    code, error = failure_code(exc), "device command failed"
                    output = self._redact(getattr(exc, "output", ""))
                    status = "failed"
                self._guard()  # Never publish output after revocation or a lost lease.
                raw = output.encode()
                if len(raw) > self._profile.max_output_bytes:
                    output = raw[: self._profile.max_output_bytes].decode(errors="ignore")
                    status, code, error = "failed", "output_limit", "command output limit exceeded"
            result = SessionCommandResult(
                execution_id=self.session_id,
                command_id=identifier,
                started_at=started,
                finished_at=now(),
                execution_status=status,
                output_complete=code is None,
                output=output,
                failure_code=code,
                error=self._redact(error) if error else None,
                driver=self._service.driver.name,
                driver_version=self._service.driver.version,
                policy_level=decision.level,
                normalized_command=self._redact(decision.normalized),
            )
            ref = self._emit(
                "command_finished",
                **fields,
                result=asdict(result),
                output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            )
            if status == "failed":
                self.close(code)
            return replace(result, evidence_ref=ref if isinstance(ref, str) else None)
        except Exception as exc:
            code = failure_code(exc)
            try:
                self.close(code)
            except Exception:
                pass
            raise DeviceAccessError(code, "SSH session command stopped") from None


def open_session(
    service,
    context,
    target_ref,
    *,
    connection_profile_id=None,
    platform,
    purpose,
    budget=None,
    audit,
    should_continue=None,
    required_vrf=None,
):
    profile_id = connection_profile_id or target_ref
    if (
        not context.actor_id
        or not context.source_app
        or not any(
            grant.action == "device.session"
            and grant.target_ref == target_ref
            and grant.connection_profile_id == profile_id
            for grant in context.grants
        )
    ):
        raise DeviceAccessError("not_authorized", "explicit controlled-session grant required")
    budget = budget or SessionBudget()
    if not isinstance(budget, SessionBudget):
        raise DeviceAccessError("invalid_request", "invalid session budget")
    budget.validate()
    if (
        platform not in {"comware", "nxos", "ios", "vrp"}
        or not callable(audit)
        or not isinstance(purpose, str)
        or not 0 < len(purpose.strip()) <= 512
    ):
        raise DeviceAccessError("invalid_request", "invalid session platform, purpose or audit")
    target, profile = service.target_resolver(target_ref), service.profile_resolver(profile_id)
    if target.target_ref != target_ref or profile.profile_id != profile_id:
        raise DeviceAccessError("not_authorized", "resolved session reference mismatch")
    validate_profile(profile)
    return SessionHandle(
        service,
        context,
        target,
        profile,
        platform=platform,
        purpose=purpose,
        budget=budget,
        audit=audit,
        should_continue=should_continue,
        required_vrf=required_vrf,
    )._open()
