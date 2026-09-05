import React, { Suspense } from 'react'
import { Button, Spin } from 'antd'
import { useNavigate } from 'react-router-dom'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import { appRoutes } from '../apps/appRoutes'

const ParserControlTab = React.lazy(
  () => import('../components/skills/ParserControlTab'),
)
export default function PatrolRulesAppPage() {
  const navigate = useNavigate()
  return (
    <GrokShellLayout
      title="巡检规则生成"
      subtitle="检查项目录、命令样本与规则资产"
      toolbar={
        <Button onClick={() => navigate(appRoutes.workbench)}>
          返回工作台
        </Button>
      }
    >
      <Suspense fallback={<Spin />}>
        <ParserControlTab />
      </Suspense>
    </GrokShellLayout>
  )
}
