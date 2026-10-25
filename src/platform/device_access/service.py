"""Authorized, bounded batch execution. No transport objects are returned."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

from .admission import SSHAdmission
from .contracts import BatchResult, CommandResult, DeviceAccessError
from .drivers.paramiko_driver import ParamikoDriver
from .errors import failure_code
from .governance import operation_for
from .netconf import NetconfConnector
from .policy import authorize, validate_profile


def _now():
    return datetime.now(timezone.utc).isoformat()


class DeviceAccessService:
    def __init__(
        self, *, target_resolver, profile_resolver, driver=None, admission=None, netconf=None
    ):
        self.target_resolver = target_resolver
        self.profile_resolver = profile_resolver
        self.driver = driver or ParamikoDriver()
        self.admission = admission or SSHAdmission()
        # NETCONF pins host keys with the same verifier as the CLI driver (per port).
        self.netconf_connector = netconf or NetconfConnector(
            host_key_verifier=getattr(self.driver, "host_key_verifier", None)
        )

    def open_session(self, context, target_ref, **kwargs):
        from .sessions import open_session

        return open_session(self, context, target_ref, **kwargs)

    @contextmanager
    def terminal(self, context, target_ref, profile_id, *, should_continue=None):
        """A separately authorized human channel; never share a batch SSH session."""
        if (
            not context.actor_id
            or not context.source_app
            or not any(
                g.action == "device.terminal"
                and g.target_ref == target_ref
                and g.connection_profile_id == profile_id
                for g in context.grants
            )
        ):
            raise DeviceAccessError("not_authorized", "terminal grant required")
        target, profile = self.target_resolver(target_ref), self.profile_resolver(profile_id)
        if target.target_ref != target_ref or profile.profile_id != profile_id:
            raise DeviceAccessError("not_authorized", "resolved reference mismatch")
        validate_profile(profile)
        operation = operation_for(context, target, profile)
        with self.admission.hold(target, profile) as lease:

            def guard():
                lease()
                if should_continue is not None and should_continue() is False:
                    raise DeviceAccessError("cancelled", "terminal was cancelled")

            guard()
            connection = None
            try:
                operation.event("connecting", mode="human")
                with operation.activate():
                    connection = self.driver.connect(target, profile, interactive=True)
                operation.event("connected", mode="human")
            except Exception as exc:
                try:
                    operation.event(
                        "connection_failed", status="failed", failure_code=failure_code(exc)
                    )
                finally:
                    if connection is not None:
                        connection.disconnect()
                    operation.event("disconnected", mode="human")
                # Same classification as batch access, so callers can ask for a new login.
                raise DeviceAccessError(failure_code(exc), self._safe_error(exc, profile)) from None
            try:
                guard()
                yield connection.channel, guard
            finally:
                try:
                    connection.disconnect()
                finally:
                    operation.event("disconnected", mode="human")

    @contextmanager
    def netconf(self, context, target_ref, profile_id, *, should_continue=None):
        """A read-only NETCONF session (netconf.py) under its own grant, "device.netconf".

        Yields (session, guard); the session sends only filtered <get>/<get-config>.
        """
        if (
            not context.actor_id
            or not context.source_app
            or not any(
                g.action == "device.netconf"
                and g.target_ref == target_ref
                and g.connection_profile_id == profile_id
                for g in context.grants
            )
        ):
            raise DeviceAccessError("not_authorized", "NETCONF grant required")
        target, profile = self.target_resolver(target_ref), self.profile_resolver(profile_id)
        if target.target_ref != target_ref or profile.profile_id != profile_id:
            raise DeviceAccessError("not_authorized", "resolved reference mismatch")
        validate_profile(profile)
        operation = operation_for(context, target, profile)
        with self.admission.hold(target, profile) as lease:

            def guard():
                lease()
                if should_continue is not None and should_continue() is False:
                    raise DeviceAccessError("cancelled", "NETCONF session was cancelled")

            guard()
            session = None
            try:
                operation.event("connecting", mode="netconf")
                with operation.activate():
                    session = self.netconf_connector.connect(target, profile, operation)
                operation.event(
                    "connected", mode="netconf", capabilities=len(session.capabilities)
                )
            except Exception as exc:
                try:
                    operation.event(
                        "connection_failed", status="failed", failure_code=failure_code(exc)
                    )
                finally:
                    if session is not None:
                        session.close()
                    operation.event("disconnected", mode="netconf")
                raise DeviceAccessError(failure_code(exc), self._safe_error(exc, profile)) from None
            try:
                guard()
                yield session, guard
            finally:
                try:
                    session.close()
                finally:
                    operation.event("disconnected", mode="netconf")

    def execute_batch(
        self, context, request, *, on_result=None, should_continue=None, command_filter=None
    ):
        # Refuse before resolving secrets, waiting for capacity, or opening SSH.
        authorize(context, request)
        target = self.target_resolver(request.target_ref)
        profile = self.profile_resolver(request.connection_profile_id)
        if (
            target.target_ref != request.target_ref
            or profile.profile_id != request.connection_profile_id
        ):
            raise DeviceAccessError("not_authorized", "resolved device access reference mismatch")
        validate_profile(profile)
        operation = operation_for(context, target, profile)
        execution_id = uuid.uuid4().hex
        results = []
        prompt = None
        connection = None
        stopped = None
        with self.admission.hold(target, profile) as assert_lease:
            deadline = time.monotonic() + request.budget_seconds

            def guard():
                assert_lease()
                if should_continue is not None and should_continue() is False:
                    raise DeviceAccessError("cancelled", "device execution was cancelled")
                if time.monotonic() >= deadline:
                    raise DeviceAccessError("budget_exceeded", "device execution budget exceeded")

            try:
                for attempt in range(max(1, profile.connection_attempts)):
                    if attempt:
                        operation = operation_for(
                            context, target, profile, session_id=uuid.uuid4().hex
                        )
                    guard()
                    try:
                        remaining = max(0.001, deadline - time.monotonic())
                        operation.event("connecting", mode="batch", attempt=attempt + 1)
                        with operation.activate():
                            connection = self.driver.connect(
                                target,
                                replace(
                                    profile,
                                    conn_timeout=min(profile.conn_timeout, remaining),
                                    auth_timeout=min(profile.auth_timeout, remaining),
                                    banner_timeout=min(profile.banner_timeout, remaining),
                                    prompt_timeout=min(profile.prompt_timeout, remaining),
                                    session_setup_timeout=min(
                                        profile.session_setup_timeout, remaining
                                    ),
                                ),
                            )
                        operation.event("connected", mode="batch", attempt=attempt + 1)
                        break
                    except Exception as exc:
                        code = failure_code(exc)
                        operation.event("connection_failed", status="failed", failure_code=code)
                        if connection is not None:
                            connection.disconnect()
                            connection = None
                        operation.event("disconnected", mode="batch", attempt=attempt + 1)
                        # Never cycle credentials or retry trust/authorization errors.
                        if (
                            code not in {"connection_failed", "timeout"}
                            or attempt + 1 >= profile.connection_attempts
                        ):
                            raise DeviceAccessError(code, self._safe_error(exc, profile)) from None
                        time.sleep(
                            min(max(0, profile.retry_delay), max(0, deadline - time.monotonic()))
                        )
                guard()
                if request.capture_prompt:
                    find_prompt = getattr(connection, "find_prompt", None)
                    prompt = str(find_prompt() or "") if callable(find_prompt) else ""
                for command in request.commands:
                    guard()
                    # Server-only selector may skip already-authorized commands, never add any.
                    if command_filter is not None and not command_filter(command, tuple(results)):
                        continue
                    started = _now() if stopped is None else None
                    output, error, code = "", None, stopped
                    if stopped is None:
                        try:
                            output = str(
                                operation.execute(
                                    command.text,
                                    lambda: connection.send_command(
                                        command.text,
                                        read_timeout=min(
                                            command.read_timeout,
                                            max(0.001, deadline - time.monotonic()),
                                        ),
                                    ),
                                    command_id=command.command_id,
                                )
                                or ""
                            )
                            output = operation.redact(output)
                        except Exception as exc:
                            code = failure_code(exc)
                            if code == "connection_failed":
                                code = "command_failed"
                            error = operation.redact(self._safe_error(exc, profile))
                            output = operation.redact(getattr(exc, "output", ""))
                            if request.stop_on_error or code in {
                                "policy_denied",
                                "policy_unavailable",
                                "policy_match_failed",
                                "audit_unavailable",
                                "invalid_command",
                                "timeout",
                                "connection_closed",
                                "output_incomplete",
                                "output_limit",
                            }:
                                stopped = "previous_command_failed"
                    else:
                        operation.event(
                            "command_not_executed",
                            status="not_executed",
                            command=command.text,
                            command_id=command.command_id,
                            failure_code=stopped,
                        )
                    # A stale executor cannot publish output or send another command.
                    guard()
                    result = CommandResult(
                        execution_id=execution_id,
                        command_id=command.command_id,
                        started_at=started,
                        finished_at=_now(),
                        execution_status=(
                            "not_executed" if started is None else "failed" if code else "succeeded"
                        ),
                        output_complete=code is None,
                        output=output,
                        failure_code=code,
                        error=error,
                        driver=self.driver.name,
                        driver_version=self.driver.version,
                    )
                    if on_result:
                        evidence_ref = on_result(result)
                        if isinstance(evidence_ref, str):
                            result = replace(result, evidence_ref=evidence_ref)
                    results.append(result)
                guard()
            finally:
                if connection is not None:
                    try:
                        connection.disconnect()
                    except Exception:
                        pass
                if connection is not None:
                    operation.event("disconnected", mode="batch")
        failures = [result for result in results if result.failure_code]
        return BatchResult(
            execution_id=execution_id,
            results=tuple(results),
            prompt=prompt,
            execution_status=(
                "partial"
                if failures and len(failures) < len(results)
                else "failed"
                if failures
                else "succeeded"
            ),
            failure_code=failures[0].failure_code if failures else None,
            error=failures[0].error if failures else None,
        )

    @staticmethod
    def _safe_error(exc, profile):
        message = str(exc)
        # Resolve only in the execution path, after authorization.
        secret = profile.password
        if secret is None and profile.credential_ref:
            from src.platform.inventory.credentials import CredentialResolver

            try:
                secret = CredentialResolver().resolve_password(profile.credential_ref)
            except ValueError:
                pass
        return message.replace(secret, "[redacted]") if secret else message
