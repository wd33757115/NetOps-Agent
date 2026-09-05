import { businessHref } from '../apps/appRoutes'
// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { useRuleTaskNavigate } from '../hooks/useRuleTaskNavigate'
import { Button } from 'antd'
import { Navigate, useParams } from 'react-router-dom'

import { OfflineFileJobWorkbench } from '../components/devices/OfflinePatrolPanel'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import '../components/skills/parser/parser-workspace.css'

const RuleAssetSourceJobDetailPage: React.FC = () => {
  const navigate = useRuleTaskNavigate()
  const { jobId } = useParams<{ jobId: string }>()

  if (!jobId) {
    return <Navigate to={businessHref('/skills/parser?workspace=production&subtab=source-jobs')} replace />
  }

  return (
    <GrokShellLayout
      title="巡检规则工作台"
      toolbar={<Button onClick={() => navigate(businessHref('/skills/parser'))}>返回巡检规则工作台</Button>}
      subtitle="样本、检查项、结果校对与发布进度"
    >
      <div className="parser-detail-shell">
        <OfflineFileJobWorkbench purpose="rule_asset_source" detailPageJobId={jobId} />
      </div>
    </GrokShellLayout>
  )
}

export default RuleAssetSourceJobDetailPage
