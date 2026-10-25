# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""创建默认管理员与 RBAC 分组；已有 admin 时把密码重置为默认值。"""

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from bff.roles import assign_user_role, SYSTEM_GROUPS

DEMO_USERS = [
    ("admin", "admin123", "admin", True),
]


class Command(BaseCommand):
    help = "创建默认管理员 admin 及 admin/netops-config 默认组"

    def handle(self, *args, **options):
        for group_name in SYSTEM_GROUPS:
            Group.objects.get_or_create(name=group_name)
            self.stdout.write(f"Group ready: {group_name}")

        for username, password, group_name, is_staff in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username, defaults={"email": f"{username}@local"})
            user.set_password(password)
            user.is_staff = is_staff
            user.is_superuser = group_name == "admin"
            user.save()
            assign_user_role(user, group_name)
            user.save()
            action = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f"User {username} {action} (role={group_name})"))

        self.stdout.write("")
        self.stdout.write("默认账号：admin/admin123，登录后请修改密码")
