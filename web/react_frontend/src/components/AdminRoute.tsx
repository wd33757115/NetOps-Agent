// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Navigate } from 'react-router-dom'
import { Spin } from 'antd'
import { useAuth } from '../context/AuthContext'

const AdminRoute: React.FC<{ children: React.ReactNode; appGrants?: boolean }> = ({ children, appGrants = false }) => {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div style={{ minHeight: '40vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (user?.role !== 'admin' && !(appGrants && user?.permissions?.includes('apps.manage'))) {
    return <Navigate to="/chat" replace />
  }

  return <>{children}</>
}

export default AdminRoute
