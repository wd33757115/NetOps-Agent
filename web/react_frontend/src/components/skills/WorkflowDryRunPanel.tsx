// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Alert, Input, Steps, Tag, Typography, message } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { GrokToolBtn } from '../ui/GrokUi'
import { workflowApi, WorkflowDryRunResult } from '../../services/api'
import type { WorkflowDSL } from '../../types/workflowDsl'

const { Text, Paragraph } = Typography

interface WorkflowDryRunPanelProps {
  buildDsl: () => WorkflowDSL | null
  defaultTicketId?: string
}

const WorkflowDryRunPanel: React.FC<WorkflowDryRunPanelProps> = ({
  buildDsl,
  defaultTicketId = 'REQ2025099',
}) => {
  const [ticketId, setTicketId] = useState(defaultTicketId)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<WorkflowDryRunResult | null>(null)

  const handleDryRun = async () => {
    const dsl = buildDsl()
    if (!dsl) {
      message.warning('请先完成 Skill 链与参数配置')
      return
    }
    setLoading(true)
    try {
      const res = await workflowApi.dryRun({
        dsl,
        context: { ticket_id: ticketId },
      })
      setResult(res)
      if (!res.validation?.valid) {
        message.warning('模拟完成，但 YAML 校验有警告')
      } else {
        message.success('Dry-run 模拟完成')
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '模拟失败')
    } finally {
      setLoading(false)
    }
  }

  const activeSteps = result?.steps.filter((s) => s.enabled) ?? []

  return (
    <div className="grok-dry-run-panel" style={{ marginTop: 16 }}>
      <Text strong>模拟执行（Dry-run）</Text>
      <Paragraph type="secondary" style={{ marginBottom: 8 }}>
        不启动 Celery，解析 when 条件并展示各步解析后的 inputs 与 mock 结果。
      </Paragraph>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <Input
          style={{ width: 200 }}
          placeholder="模拟工单号"
          value={ticketId}
          onChange={(e) => setTicketId(e.target.value)}
        />
        <GrokToolBtn icon={<PlayCircleOutlined />} loading={loading} onClick={handleDryRun}>
          运行模拟
        </GrokToolBtn>
      </div>

      {result && (
        <>
          <Alert
            type="success"
            showIcon
            message={`预计执行 ${result.active_step_count} 步`}
            description={result.flow_description}
            style={{ marginBottom: 12 }}
          />
          {result.parallel_batches?.length > 0 && (
            <Alert
              type="info"
              style={{ marginBottom: 12 }}
              message="并行批次"
              description={result.parallel_batches
                .map((b) => `${b.parallel_group}: ${b.step_names.join(' ∥ ')}`)
                .join('；')}
            />
          )}
          {result.skipped_steps?.length > 0 && (
            <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
              跳过（when=false）：{result.skipped_steps.join(', ')}
            </Text>
          )}
          <Steps
            direction="vertical"
            size="small"
            current={activeSteps.length - 1}
            items={activeSteps.map((s) => ({
              title: (
                <span>
                  {s.label || s.name}{' '}
                  <Tag>{s.skill}</Tag>
                  {s.parallel_group && <Tag color="purple">并行 {s.parallel_group}</Tag>}
                </span>
              ),
              description: (
                <div style={{ fontSize: 12 }}>
                  {Object.keys(s.resolved_inputs || {}).length > 0 && (
                    <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap' }}>
                      {JSON.stringify(s.resolved_inputs, null, 2)}
                    </pre>
                  )}
                  {s.mock_result && (
                    <Text type="secondary">{String(s.mock_result.message)}</Text>
                  )}
                </div>
              ),
              status: s.enabled ? 'finish' : 'wait',
            }))}
          />
        </>
      )}
    </div>
  )
}

export default WorkflowDryRunPanel
