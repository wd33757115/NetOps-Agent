import { businessHref } from '@netops/apps/appRoutes'
// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { Button } from 'antd'
import { Navigate, useParams } from 'react-router-dom'

import { RuleAssetSourceWorkbench } from '../components/RuleAssetSourceWorkbench'
import RuleWorkspaceLayout from '../RuleWorkspaceLayout'
import '../components/skills/parser/parser-workspace.css'

const RuleAssetSourceJobDetailPage: React.FC = () => {
  const navigate = useRuleTaskNavigate()
  const { jobId } = useParams<{ jobId: string }>()

  if (!jobId) {
    return <Navigate to={businessHref('/skills/parser?workspace=production&subtab=source-jobs')} replace />
  }

  return (
    <RuleWorkspaceLayout activeKey="sources"
      title="样本任务详情"
      toolbar={<Button onClick={() => navigate(businessHref('/skills/parser'))}>返回制作任务</Button>}
      subtitle="样本、检查项、结果校对与发布进度"
    >
      <div className="parser-detail-shell">
        <RuleAssetSourceWorkbench detailPageJobId={jobId} />
      </div>
    </RuleWorkspaceLayout>
  )
}

export default RuleAssetSourceJobDetailPage
