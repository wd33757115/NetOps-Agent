"""Administrator-managed business groups; role groups remain protected."""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt

from .audit import log_auth_event
from .decorators import require_jwt, require_role
from .models import AppGrant
from .response import bff_error, bff_success
from .roles import SYSTEM_GROUPS, get_user_role
from src.auth.netops_access import NETOPS_CONFIG_GROUP

PROTECTED_NAMES = frozenset(SYSTEM_GROUPS)


def protected_group(group):
    return group.name.casefold() in PROTECTED_NAMES


def serialize_group(group):
    members = sorted(group.user_set.values_list('id', flat=True))
    app_ids = sorted(AppGrant.objects.filter(group=group).values_list('app_id', flat=True))
    value = {'id': group.pk, 'name': group.name, 'member_ids': members,
             'app_ids': app_ids, 'protected': protected_group(group)}
    value['revision'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def parse_body(request):
    body = json.loads(request.body or b'{}')
    if not isinstance(body, dict):
        raise ValueError('请求必须为 JSON 对象')
    return body


def validate_members(value):
    if not isinstance(value, list) or any(type(x) is not int or x <= 0 for x in value):
        raise ValueError('成员必须为用户 ID 列表')
    ids = sorted(set(value))
    if get_user_model().objects.filter(pk__in=ids).count() != len(ids):
        raise ValueError('成员中有不存在的用户，请刷新后重试')
    return ids


@csrf_exempt
@require_jwt(strict=True)
@require_role('admin')
def groups(request, group_id=None):
    if request.method == 'GET' and group_id is None:
        return bff_success({'items': [serialize_group(g) for g in Group.objects.order_by('name')],
                            'users': [] if request.GET.get('compact') == '1' else list(get_user_model().objects.order_by('username').values('id', 'username', 'is_active'))})
    if (group_id is None and request.method != 'POST') or (group_id is not None and request.method not in {'PATCH', 'DELETE'}):
        return bff_error('Method not allowed', 405)
    try:
        body = parse_body(request)
        with transaction.atomic():
            # Serialize group membership changes, including the last-admin check.
            locked_users = list(get_user_model().objects.select_for_update().order_by('pk'))
            group = Group.objects.select_for_update().get(pk=group_id) if group_id else None
            before = serialize_group(group) if group else None
            if group and protected_group(group) and (request.method == 'DELETE' or body.get('name', group.name) != group.name):
                return bff_error('默认系统组不能删除或改名，可以添加或移出成员', 403)
            if group and body.get('revision') != before['revision']:
                return bff_error('用户组已发生变化，请重新加载后再操作', 409)
            if request.method == 'DELETE':
                group.delete()
                after = None
            else:
                name = body.get('name', group.name if group else None)
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
                    raise ValueError('组名不能为空，且不能超过 150 个字符')
                name = name.strip()
                if name.casefold() in PROTECTED_NAMES and not (group and name == group.name):
                    raise ValueError('不能使用系统保留组名')
                if Group.objects.filter(name__iexact=name).exclude(pk=group_id).exists():
                    raise ValueError('用户组名称已存在')
                members = validate_members(body.get('member_ids', before['member_ids'] if before else []))
                if group is None:
                    group = Group.objects.create(name=name)
                else:
                    group.name = name
                    group.save(update_fields=['name'])
                if group.name == 'admin':
                    removed = set(before['member_ids'] if before else []) - set(members)
                    if not any(u.is_active and (u.pk in members or (u.is_superuser and u.pk not in removed)) for u in locked_users):
                        raise ValueError('至少保留一名启用的 admin 管理员')
                    get_user_model().objects.filter(pk__in=removed).update(is_superuser=False, is_staff=False)
                    get_user_model().objects.filter(pk__in=members).update(is_superuser=True, is_staff=True)
                group.user_set.set(members)
                after = serialize_group(group)
            transaction.on_commit(lambda: log_auth_event('group_' + request.method.lower(), request.user, request,
                                                         detail={'before': before, 'after': after}))
        return bff_success(after or {'deleted': group_id}, status=201 if request.method == 'POST' else 200)
    except Group.DoesNotExist:
        return bff_error('用户组不存在', 404)
    except (ValueError, UnicodeDecodeError) as exc:
        return bff_error(str(exc), 400)
    except IntegrityError:
        return bff_error('用户组或成员已变化，请刷新后重试', 409)
