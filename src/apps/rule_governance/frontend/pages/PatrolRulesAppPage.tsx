import React, { Suspense } from 'react'
import { Spin } from 'antd'
const ParserControlTab = React.lazy(() => import('../components/skills/ParserControlTab'))
export default function PatrolRulesAppPage() {
  return <Suspense fallback={<Spin aria-label="规则工作区加载中" />}><ParserControlTab standalone /></Suspense>
}
