import json
import os
import hashlib

import jwt
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from src.platform.kernel.registry import discover

from ..app_access import available_apps, decision, identity, deployment_decision
from ..decorators import require_jwt, require_app_grants_manager
from ..models import AppGrant, AppInstallation
from ..response import bff_error, bff_success
from ..audit import log_auth_event
from ..roles import get_user_role
from ..group_admin import protected_group


def grant_revision(grants):
    values = sorted((g['user_id'] or 0, g['group_id'] or 0) for g in grants)
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def grant_data(app_id):
    grants = list(AppGrant.objects.filter(app_id=app_id).values('id', 'user_id', 'group_id'))
    return {'grants': grants, 'revision': grant_revision(grants)}


@csrf_exempt
def runtime_admission(request):
    from src.platform.kernel.runtime import authenticate

    if request.method != "POST":
        return bff_error("Method not allowed", 405)
    try:
        authenticate(request.headers.get("Authorization", ""))
        body = json.loads(request.body)
        manifests = discover(app_id=body["app_id"])
        manifest = manifests.get(body["app_id"])
        ready = (
            manifest
            and manifest.digest == body["digest"]
            and deployment_decision(manifest.id, manifests) == 200
        )
    except (ValueError, KeyError, TypeError, jwt.InvalidTokenError):
        return bff_error("Invalid runtime credential or payload", 401)
    return bff_success({}) if ready else bff_error("Application unavailable", 404)


@require_jwt(strict=True)
def apps(request):
    if request.method != "GET":
        return bff_error("Method not allowed", 405)
    return bff_success({"items": available_apps(request.user)})


def authorization_response(user, app_id):
    code = decision(user, app_id)
    return (
        bff_success({"user": identity(user)})
        if code == 200
        else bff_error("应用不可用或没有使用权限", code)
    )


@csrf_exempt
@require_jwt(strict=True)
def authorize(request, app_id):
    if request.method != "POST":
        return bff_error("Method not allowed", 405)
    digest = request.headers.get("X-Netops-App-Digest")
    if digest and not AppInstallation.objects.filter(pk=app_id, deployment_digest=digest).exists():
        return bff_error("Gateway version mismatch", 503)
    return authorization_response(request.user, app_id)


@csrf_exempt
def worker_authorize(request, app_id):
    if request.method != "POST":
        return bff_error("Method not allowed", 405)
    secret = os.getenv("NETOPS_APP_INTERNAL_SECRET", "")
    if len(secret) < 32:
        return bff_error("应用授权服务凭据未配置", 503)
    try:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return bff_error("Unauthorized", 401)
        claims = jwt.decode(
            header[7:],
            secret,
            algorithms=["HS256"],
            audience="netops-app-worker",
            options={"require": ["exp", "iat", "sub", "app_id", "aud", "digest"]},
        )
        if claims["app_id"] != app_id:
            return bff_error("Forbidden", 403)
        if not AppInstallation.objects.filter(
            pk=app_id, deployment_digest=claims["digest"]
        ).exists():
            return bff_error("Worker version mismatch", 503)
        user = get_user_model().objects.get(pk=claims["sub"], is_active=True)
    except (jwt.InvalidTokenError, get_user_model().DoesNotExist, ValueError, TypeError):
        return bff_error("Unauthorized", 401)
    return authorization_response(user, app_id)


@csrf_exempt
@require_jwt(strict=True)
@require_app_grants_manager
def administration(request, app_id=None):
    manifests = discover()
    if request.method == "GET" and app_id is None:
        records = {a.app_id: a for a in AppInstallation.objects.all()}
        return bff_success(
            {
                "items": [
                    {
                        **m.public(),
                        "enabled": bool(records.get(m.id) and records[m.id].desired_enabled),
                        "state": records[m.id].state if m.id in records else "discovered",
                        **grant_data(m.id),
                    }
                    for m in manifests.values()
                ],
                "users": [] if request.GET.get("compact") == "1" else [
                    {'id': u.pk, 'username': u.username, 'is_active': u.is_active,
                     'role': get_user_role(u), 'group_ids': [g.pk for g in u.groups.all()]}
                    for u in get_user_model().objects.prefetch_related('groups').order_by('username')
                ],
                "groups": [{'id': g.pk, 'name': g.name, 'protected': protected_group(g)} for g in Group.objects.order_by('name')],
            }
        )
    if request.method != "PUT" or app_id not in manifests:
        return bff_error("Method or application not found", 404)
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict):
            raise ValueError('请求必须为 JSON 对象')
        user_ids, group_ids = body["user_ids"], body["group_ids"]
        if (
            not isinstance(user_ids, list)
            or not isinstance(group_ids, list)
            or any(type(x) is not int for x in user_ids + group_ids)
        ):
            raise ValueError("授权对象必须为用户或组 ID 列表")
        if get_user_model().objects.filter(pk__in=set(user_ids)).count() != len(
            set(user_ids)
        ) or Group.objects.filter(pk__in=set(group_ids)).count() != len(set(group_ids)):
            raise ValueError("授权对象不存在")
        with transaction.atomic():
            app = AppInstallation.objects.select_for_update().get(pk=app_id)
            before = grant_data(app_id)
            if 'revision' in body and body['revision'] != before['revision']:
                return bff_error('应用授权已发生变化，请重新加载后再保存', 409)
            AppGrant.objects.filter(app=app).delete()
            AppGrant.objects.bulk_create(
                [AppGrant(app=app, user_id=x, granted_by=request.user) for x in set(user_ids)]
                + [AppGrant(app=app, group_id=x, granted_by=request.user) for x in set(group_ids)]
            )
            after = grant_data(app_id)
            transaction.on_commit(lambda: log_auth_event('app_grants_update', request.user, request,
                detail={'app_id': app_id, 'before': before, 'after': after}))
    except AppInstallation.DoesNotExist:
        return bff_error("请先安装应用", 409)
    except (ValueError, KeyError, TypeError) as exc:
        return bff_error(str(exc), 400)
    return bff_success({"app_id": app_id})
