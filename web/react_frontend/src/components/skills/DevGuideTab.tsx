// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Card, Input, Segmented, Table } from 'antd'
import { useQuery } from 'react-query'
import { workflowApi } from '../../services/api'
import WorkflowRunMonitor from './WorkflowRunMonitor'
import DeveloperHandbook, { HANDBOOKS } from './DeveloperHandbook'
import { GrokToolBtn } from '../ui/GrokUi'

type Guide = keyof typeof HANDBOOKS

const GUIDES: { value: Guide; label: string }[] = [
  { value: 'platform', label: '平台开发手册' },
  { value: 'skill', label: 'Skill 开发手册' },
  { value: 'workflow', label: 'Workflow 开发手册' },
]

// #skill and #workflow open the matching handbook; the platform handbook has no hash.
const fromHash = (): Guide => {
  const hash = window.location.hash.slice(1)
  return hash === 'skill' || hash === 'workflow' ? hash : 'platform'
}

const DevGuideTab: React.FC = () => {
  const [guide, setGuide] = useState<Guide>(fromHash)
  const choose = (value: Guide) => {
    setGuide(value)
    const { pathname, search } = window.location
    window.history.replaceState(null, '', value === 'platform' ? pathname + search : `#${value}`)
  }
  return (
    <div className="grok-dev-guide">
      <Segmented className="dev-guide-switch" options={GUIDES} value={guide} onChange={value => choose(value as Guide)} />
      <DeveloperHandbook key={guide} source={HANDBOOKS[guide]}>
        {guide === 'workflow' && <WorkflowRuns />}
      </DeveloperHandbook>
    </div>
  )
}

// Recent runs under the workflow handbook, to look at what a test-run or webhook just did.
const WorkflowRuns: React.FC = () => {
  const [runIdInput, setRunIdInput] = useState('')
  const [monitorRunId, setMonitorRunId] = useState<string | null>(null)

  const { data: recentRuns = [] } = useQuery(
    'workflow-runs',
    () => workflowApi.listRuns({ limit: 10 }),
    { refetchOnWindowFocus: false },
  )

  return (
    <section className="handbook-runs" aria-label="Workflow 运行记录">
      <Card title="最近 Workflow 运行" size="small">
        <Table
          size="small"
          rowKey="run_id"
          pagination={false}
          scroll={{ x: 'max-content' }}
          dataSource={recentRuns}
          columns={[
            { title: 'Run ID', dataIndex: 'run_id', ellipsis: true, width: 200 },
            { title: '模板', dataIndex: 'template_name' },
            { title: '工单', dataIndex: 'ticket_id' },
            { title: '状态', dataIndex: 'status' },
            {
              title: '操作',
              render: (_, row) => (
                <GrokToolBtn onClick={() => setMonitorRunId(row.run_id)}>查看</GrokToolBtn>
              ),
            },
          ]}
        />
      </Card>

      <Card title="按 Run ID 查询" style={{ marginTop: 16 }} size="small">
        <Input.Search
          placeholder="输入 Workflow Run ID"
          value={runIdInput}
          onChange={(e) => setRunIdInput(e.target.value)}
          onSearch={(v) => setMonitorRunId(v.trim() || null)}
          enterButton="查询"
        />
        {monitorRunId && (
          <div style={{ marginTop: 16 }}>
            <WorkflowRunMonitor runId={monitorRunId} embedded onClose={() => setMonitorRunId(null)} />
          </div>
        )}
      </Card>
    </section>
  )
}

export default DevGuideTab
