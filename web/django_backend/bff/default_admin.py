# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""The first administrator of a fresh installation: admin / admin123.

Created only while the installation has no superuser and no user named admin, so a
changed password or a different first administrator is never touched. Change the
password after the first login.
"""

from django.contrib.auth.models import Group, User

from .roles import SYSTEM_GROUPS, assign_user_role

USERNAME = "admin"
PASSWORD = "admin123"


def ensure_default_admin() -> bool:
    """Create the default administrator when there is none; True when created."""
    for name in SYSTEM_GROUPS:
        Group.objects.get_or_create(name=name)
    taken = User.objects.filter(is_superuser=True) | User.objects.filter(username=USERNAME)
    if taken.exists():
        return False
    user = User.objects.create_user(USERNAME, email=f"{USERNAME}@local", password=PASSWORD)
    user.is_staff = True
    user.is_superuser = True
    user.save()
    assign_user_role(user, "admin")
    user.save()
    return True
