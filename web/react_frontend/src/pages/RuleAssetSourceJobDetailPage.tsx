// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Navigate, useParams } from 'react-router-dom'

import { OfflineFileJobWorkbench } from '../components/devices/OfflinePatrolPanel'
import GrokShellLayout from '../components/layout/GrokShellLayout'

const RuleAssetSourceJobDetailPage: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>()

  if (!jobId) {
    return <Navigate to="/skills/parser?workspace=production&subtab=source-jobs" replace />
  }

  return (
    <GrokShellLayout
      title="巡检规则资产"
      subtitle="查看样本证据、处理过程、命令适配器生产进度与回放结果"
    >
      <OfflineFileJobWorkbench purpose="rule_asset_source" detailPageJobId={jobId} />
    </GrokShellLayout>
  )
}

export default RuleAssetSourceJobDetailPage
