import React from 'react'
import { Alert, Spin } from 'antd'
import { useAuth } from '../context/AuthContext'

export default function PackageRoute({ appId, children }: { appId: string; children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <Spin />
  if (!user?.app_ids?.includes(appId)) return <Alert type="warning" showIcon message="应用未启用或你没有使用权限" />
  return <>{children}</>
}
