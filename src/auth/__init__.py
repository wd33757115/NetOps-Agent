# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""FastAPI 认证与 RBAC。"""

from src.auth.dependencies import get_current_user, get_optional_user, require_role
from src.auth.models import CurrentUser

__all__ = ["CurrentUser", "get_current_user", "get_optional_user", "require_role"]
