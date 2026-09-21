import React, { Suspense } from 'react'
import { Button, Spin } from 'antd'
import { useNavigate } from 'react-router-dom'
import GrokShellLayout from '@netops/components/Layout/GrokShellLayout'
import { appRoutes } from '@netops/apps/appRoutes'

const ParserControlTab = React.lazy(
  () => import('../components/skills/ParserControlTab'),
)
export default function PatrolRulesAppPage() {
  const navigate = useNavigate()
  return (
    <GrokShellLayout
      title="巡检规则工作台"
      subtitle="检查项目录、命令样本与规则资产"
      toolbar={
        <>
          <Button onClick={() => navigate('/apps/rule-governance/devices')}>设备维护</Button>
          <Button onClick={() => navigate(appRoutes.workbench)}>返回工作台</Button>
        </>
      }
    >
      <Suspense fallback={<Spin />}>
        <ParserControlTab />
      </Suspense>
    </GrokShellLayout>
  )
}
