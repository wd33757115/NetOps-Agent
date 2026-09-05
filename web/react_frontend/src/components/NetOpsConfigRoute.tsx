import React from 'react'
import { Button, Result, Spin } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { canConfigureNetOps } from '../apps/permissions'
export default function NetOpsConfigRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const navigate = useNavigate()
  if (loading) return <Spin aria-label="正在校验权限" />
  return canConfigureNetOps(user) ? <>{children}</> : <Result status="403" title="需要设备与规则配置权限"
    subTitle="请联系管理员授权。你仍可使用网络巡检。" extra={<Button onClick={() => navigate('/apps/patrol')}>返回网络巡检</Button>} />
}
