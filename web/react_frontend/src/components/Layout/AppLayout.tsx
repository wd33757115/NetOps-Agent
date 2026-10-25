// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Outlet } from 'react-router-dom'

/** 各页面自带 GrokShellLayout，此处仅提供全屏容器 */
const AppLayout: React.FC = () => {
  return (
    <div className="grok-app-root">
      <Outlet />
    </div>
  )
}

export default AppLayout
