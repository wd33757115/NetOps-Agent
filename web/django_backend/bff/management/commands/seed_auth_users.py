# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""创建演示用户与 RBAC 分组。"""

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from bff.roles import assign_user_role, SYSTEM_GROUPS

DEMO_USERS = [
    ("admin", "admin123", "admin", True),
    ("operator", "operator123", "operator", False),
    ("viewer", "viewer123", "viewer", False),
]


class Command(BaseCommand):
    help = "创建演示账号及 admin/netops-config 默认组"

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
        self.stdout.write("演示账号：admin/admin123 | operator/operator123 | viewer/viewer123")
