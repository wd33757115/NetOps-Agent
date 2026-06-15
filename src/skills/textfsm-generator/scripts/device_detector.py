# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Compatibility wrapper around the platform identity resolver."""

from src.core.parser.identity import DeviceIdentity, DeviceIdentityResolver


def detect_device_identity(*, path, blocks, config, vendor=None, model=None):
    resolver = DeviceIdentityResolver()
    resolver.config = config
    return resolver.resolve(
        path=path,
        blocks=blocks,
        vendor=vendor,
        model=model,
    )
