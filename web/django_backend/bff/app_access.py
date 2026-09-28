"""One application admission policy shared by proxy, discovery and introspection."""

import os

from django.contrib.auth import get_user_model
from django.db.models import Q

from src.platform.kernel.registry import discover

from .models import AppGrant, AppInstallation
from .roles import get_user_role


def deployment_decision(app_id, manifests=None):
    manifests = manifests if manifests is not None else discover(app_id=app_id)
    manifest = manifests.get(app_id)
    if manifest is None:
        return 404
    if (
        app_id == "official-documents"
        and os.getenv("OFFICIAL_DOCUMENTS_ENABLED", "true").lower() == "false"
    ):
        return 404
    record = AppInstallation.objects.filter(pk=app_id).first()
    if not record or not record.desired_enabled or record.state != "enabled":
        return 404
    if (
        record.deployment_digest != manifest.digest
        or record.installed_version != manifest.data["version"]
    ):
        return 503
    for dependency in manifest.data.get("dependencies", {}):
        if deployment_decision(dependency, manifests) != 200:
            return 503
    return 200


def decision(user, app_id, *, manifests=None):
    code = deployment_decision(app_id, manifests)
    if code != 200:
        return code
    if not user or not user.is_authenticated or not user.is_active:
        return 401
    # Streams may outlive the request's original User instance.
    user = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if user is None:
        return 401
    if get_user_role(user) == "admin":
        return 200
    return (
        200
        if AppGrant.objects.filter(app_id=app_id)
        .filter(Q(user=user) | Q(group__in=user.groups.all()))
        .exists()
        else 403
    )


def available_apps(user):
    # One request needs one filesystem snapshot, not a full rescan per App.
    # User status, membership and grants are still read from the database.
    manifests = discover()
    return [m.public() for m in manifests.values() if decision(user, m.id, manifests=manifests) == 200]


def identity(user):
    return {"user_id": str(user.pk), "username": user.username, "role": get_user_role(user)}
