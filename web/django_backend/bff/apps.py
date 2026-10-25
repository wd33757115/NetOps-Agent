# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from django.apps import AppConfig


class BffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bff"
    verbose_name = "NetOps BFF"
