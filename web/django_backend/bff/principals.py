"""Searchable, bounded account/group directory for membership and grants editors."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q, Count
from django.views.decorators.csrf import csrf_exempt

from .decorators import require_jwt, require_app_grants_manager
from .response import bff_error, bff_success
from .roles import get_user_role, SYSTEM_GROUPS


def id_list(value):
    if not isinstance(value, list) or any(type(v) is not int or v <= 0 for v in value):
        raise ValueError('请选择有效的用户或组 ID')
    return list(set(value))


@csrf_exempt
@require_jwt(strict=True)
@require_app_grants_manager
def directory(request):
    if request.method != 'POST':
        return bff_error('Method not allowed', 405)
    try:
        body = json.loads(request.body or b'{}')
        kind = body.get('kind', 'user')
        if kind not in {'user', 'group'}:
            raise ValueError('不支持的查询类型')
        page, size = body.get('page', 1), body.get('page_size', 10)
        if type(page) is not int or page < 1 or type(size) is not int or not 1 <= size <= 100:
            raise ValueError('分页参数无效')
        search = body.get('search', '')
        if not isinstance(search, str) or len(search) > 150:
            raise ValueError('搜索文字过长')
        query = Group.objects.annotate(member_count=Count('user', distinct=True)).order_by('name', 'pk') if kind == 'group' else get_user_model().objects.prefetch_related('groups').order_by('username', 'pk')
        if 'ids' in body:
            query = query.filter(pk__in=id_list(body['ids']))
        if 'exclude_ids' in body:
            query = query.exclude(pk__in=id_list(body['exclude_ids']))
        if search.strip():
            query = query.filter(name__icontains=search.strip()) if kind == 'group' else query.filter(Q(username__icontains=search.strip()) | Q(email__icontains=search.strip()))
        access = body.get('access')
        selected_users, selected_groups = [], []
        if access is not None:
            if kind != 'user' or not isinstance(access, dict):
                raise ValueError('授权预览参数无效')
            selected_users, selected_groups = id_list(access.get('user_ids')), id_list(access.get('group_ids'))
            query = query.filter(Q(pk__in=selected_users) | Q(groups__pk__in=selected_groups) | Q(is_superuser=True) | Q(groups__name__iexact='admin')).distinct()
        total = query.count()
        items = []
        for row in query[(page - 1) * size:page * size]:
            if kind == 'group':
                items.append({'id': row.pk, 'name': row.name, 'member_count': row.member_count, 'protected': row.name in SYSTEM_GROUPS})
            else:
                role = get_user_role(row)
                sources = (['admin · 全部系统权限'] if role == 'admin' else
                           (['直接授权'] if row.pk in selected_users else []) +
                           ['用户组：' + g.name for g in row.groups.all() if g.pk in selected_groups])
                items.append({'id': row.pk, 'name': row.username, 'email': row.email, 'is_active': row.is_active, 'role': role, 'sources': sources})
        return bff_success({'items': items, 'total': total})
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError) as exc:
        return bff_error(str(exc), 400)
