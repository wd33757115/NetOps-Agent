"""Exact server-issued grants, independent of App admission policy."""

import codecs
import math
import re

from .contracts import AccessContext, BatchRequest, ConnectionProfile, DeviceAccessError


def authorize(context: AccessContext, request: BatchRequest) -> None:
    allowed = any(
        grant.action == "device.collect"
        and grant.target_ref == request.target_ref
        and grant.connection_profile_id == request.connection_profile_id
        and all(command.text in grant.commands for command in request.commands)
        for grant in context.grants
    )
    if not context.actor_id or not context.source_app or not allowed:
        raise DeviceAccessError("not_authorized", "device access is not authorized")
    ids = [command.command_id for command in request.commands]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise ValueError("command IDs must be nonempty and unique within a batch")
    if (
        not math.isfinite(request.budget_seconds)
        or request.budget_seconds <= 0
        or any(
            not command.text.strip()
            or any(c in command.text for c in ("\r", "\n", "\x00"))
            or not math.isfinite(command.read_timeout)
            or command.read_timeout <= 0
            for command in request.commands
        )
    ):
        raise ValueError("commands and execution timeouts must be valid")


def validate_profile(profile: ConnectionProfile) -> None:
    if not profile.ssh_strict:
        raise DeviceAccessError("host_key_rejected", "strict host-key verification is required")
    if not profile.username or not 1 <= profile.port <= 65535:
        raise DeviceAccessError("invalid_request", "invalid connection profile")
    if profile.connection_attempts not in (1, 2):
        raise DeviceAccessError(
            "invalid_request", "at most one transient connection retry is allowed"
        )
    if (
        any(
            not math.isfinite(value) or value <= 0
            for value in (
                profile.conn_timeout,
                profile.auth_timeout,
                profile.banner_timeout,
                profile.prompt_timeout,
                profile.session_setup_timeout,
            )
        )
        or not math.isfinite(profile.retry_delay)
        or profile.retry_delay < 0
    ):
        raise DeviceAccessError("invalid_request", "invalid connection timeout")
    if not 1 <= profile.max_output_bytes <= 64 * 1024 * 1024:
        raise DeviceAccessError("invalid_request", "invalid output byte limit")
    re.compile(profile.prompt_pattern)
    codecs.lookup(profile.encoding)
