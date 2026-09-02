// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { Navigate, useParams } from 'react-router-dom'

import { OfflineFileJobWorkbench } from '../components/devices/OfflinePatrolPanel'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import '../components/skills/parser/parser-workspace.css'

const RuleAssetSourceJobDetailPage: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>()

  if (!jobId) {
    return <Navigate to="/skills/parser?workspace=production&subtab=source-jobs" replace />
  }

  return (
    <GrokShellLayout
      title="巡检规则资产"
      subtitle="查看 CLI 样本证据、处理过程和进入检查项目录的待归类命令"
    >
      <div className="parser-detail-shell">
        <OfflineFileJobWorkbench purpose="rule_asset_source" detailPageJobId={jobId} />
      </div>
    </GrokShellLayout>
  )
}

export default RuleAssetSourceJobDetailPage
