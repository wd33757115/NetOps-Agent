// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Card, Collapse, Descriptions, List, Space, Spin, Steps, Tag, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { workflowApi, WorkflowTimelineEvent } from '../../services/api'
import { openAuthenticatedEventStream } from '../../services/authenticatedSse'
import { GrokChip } from '../ui/GrokUi'

const { Text } = Typography

interface WorkflowRunMonitorProps {
  runId: string | null
  onClose?: () => void
  embedded?: boolean
  plain?: boolean
  subjectLabel?: string
}

const STATUS_COLOR: Record<string, string> = {
  started: 'blue',
  running: 'processing',
  completed: 'success',
  failed: 'error',
}

function hasArtifacts(value?: Record<string, unknown> | null) {
  return !!value && Object.keys(value).length > 0
}

function formatTimestamp(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

type ArtifactMeta = {
  download_url?: string
  filename?: string
  content_type?: string
  file_key?: string
}

function artifactLabel(key: string, meta?: ArtifactMeta) {
  if (key === 'raw_outputs_archive') return '原始输出'
  if (key === 'analysis_report') return '分析报告'
  if (key === 'patrol_report') return '巡检报告'
  if (key === 'change_excel') return '变更工单'
  if (key === 'config_zip') return 'Config package'
  return meta?.filename || key.replaceAll('_', ' ')
}

function artifactColor(key: string) {
  if (key === 'raw_outputs_archive') return 'default'
  if (key === 'analysis_report' || key === 'patrol_report' || key.includes('report')) return 'green'
  if (key === 'config_zip') return 'blue'
  if (key === 'change_excel') return 'gold'
  return 'geekblue'
}

function artifactDescription(key: string) {
  if (key === 'raw_outputs_archive') {
    return '原始 CLI 输出归档，用于审计、离线解析和排查；不是巡检报告。'
  }
  if (key === 'analysis_report' || key === 'patrol_report') {
    return '下游生成的人类可读报告。'
  }
  return undefined
}

function renderArtifacts(value?: Record<string, unknown> | null) {
  if (!hasArtifacts(value)) return null
  const entries = Object.entries(value || {}).filter(([, meta]) => {
    return typeof meta === 'object' && meta !== null
  }) as Array<[string, ArtifactMeta]>
  if (!entries.length) return null
  return (
    <Space direction="vertical" size={4}>
      {entries.map(([key, meta]) => {
        const tag = <Tag color={artifactColor(key)}>{artifactLabel(key, meta)}</Tag>
        return (
          <span key={key}>
            {meta.download_url ? (
              <a href={meta.download_url} target="_blank" rel="noreferrer">{tag}</a>
            ) : tag}
            {artifactDescription(key) && (
              <Text type="secondary" style={{ fontSize: 12 }}>{artifactDescription(key)}</Text>
            )}
          </span>
        )
      })}
    </Space>
  )
}

function formatTime(ts?: string) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return ts
  }
}

function durationText(startedAt?: string | null, completedAt?: string | null) {
  if (!startedAt || !completedAt) return '-'
  const seconds = Math.max(0, (new Date(completedAt).getTime() - new Date(startedAt).getTime()) / 1000)
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

const patrolStepMeta: Record<string, { label: string; description: string }> = {
  'device-patrol': { label: '命令采集与解析', description: '登录设备、执行命令并保存原始输出' },
  'state-normalizer': { label: '状态归一化', description: '转换为统一、可比较的设备状态' },
  'change-detector': { label: '变化检测', description: '与历史状态比较并识别有效变化' },
  'event-builder': { label: '事件判定', description: '使用活动规则判断是否生成巡检事件' },
}

const displayStatus = (status?: string | null) => {
  const labels: Record<string, string> = {
    pending: '等待中', queued: '排队中', running: '运行中', completed: '完成', failed: '失败',
    cancelled: '已取消',
  }
  return labels[String(status || '')] || status || '-'
}

const WorkflowRunMonitor: React.FC<WorkflowRunMonitorProps> = ({
  runId,
  onClose,
  embedded,
  plain,
  subjectLabel = '设备执行',
}) => {
  const [liveEvents, setLiveEvents] = useState<WorkflowTimelineEvent[]>([])
  const [liveStreamUnavailable, setLiveStreamUnavailable] = useState(false)

  useEffect(() => {
    setLiveEvents([])
    setLiveStreamUnavailable(false)
  }, [runId])

  const { data: run, isLoading, refetch } = useQuery(
    ['workflow-run', runId],
    () => workflowApi.getRun(runId!),
    { enabled: !!runId, refetchOnWindowFocus: false },
  )
  const runStatus = run?.status

  const timeline = useMemo(() => {
    const base = run?.timeline || []
    const merged = [...base]
    for (const step of run?.steps || []) {
      const meta = patrolStepMeta[step.skill_name]
      if (step.started_at && !merged.some((event) =>
        event.step_name === step.step_name && event.status === 'started')) {
        merged.push({
          run_id: run?.run_id || '',
          step_name: step.step_name,
          skill_name: step.skill_name,
          status: 'started',
          message: `${meta?.label || step.step_name}开始执行`,
          timestamp: step.started_at,
        })
      }
      if (step.completed_at && !merged.some((event) =>
        event.step_name === step.step_name && ['completed', 'failed'].includes(event.status))) {
        merged.push({
          run_id: run?.run_id || '',
          step_name: step.step_name,
          skill_name: step.skill_name,
          status: step.status === 'failed' ? 'failed' : 'completed',
          message: step.error_message || `${meta?.label || step.step_name}执行完成`,
          timestamp: step.completed_at,
        })
      }
    }
    for (const ev of liveEvents) {
      const key = `${ev.timestamp}-${ev.status}-${ev.step_name}-${ev.message}`
      if (!merged.some((m) => `${m.timestamp}-${m.status}-${m.step_name}-${m.message}` === key)) {
        merged.push(ev)
      }
    }
    return merged.sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')))
  }, [run?.run_id, run?.steps, run?.timeline, liveEvents])

  useEffect(() => {
    if (!runId || !runStatus) return undefined
    if (['completed', 'failed', 'cancelled'].includes(runStatus)) return undefined

    const timer = window.setInterval(() => {
      refetch()
    }, 2500)
    return () => window.clearInterval(timer)
  }, [runId, runStatus, refetch])

  useEffect(() => {
    if (!runId || !runStatus) return undefined
    if (['completed', 'failed', 'cancelled'].includes(runStatus)) return undefined

    const url = workflowApi.getRunEventsStreamUrl(runId)
    let close = () => {}
    close = openAuthenticatedEventStream(url, {
      onOpen: () => setLiveStreamUnavailable(false),
      onEvent: (event) => {
        if (event.event === 'done') {
          refetch()
          close()
          return
        }
        if (!['timeline', 'progress', 'message'].includes(event.event)) return
        try {
          const payload = JSON.parse(event.data) as WorkflowTimelineEvent
          setLiveEvents((prev) => [...prev, payload])
        } catch {
          /* ignore malformed SSE */
        }
      },
      onError: () => setLiveStreamUnavailable(true),
    })
    return close
  }, [runId, runStatus, refetch])

  if (!runId) return null

  const content = (
    <>
      {isLoading && !run ? (
        <Spin />
      ) : run ? (
        <>
          {(!plain || run.status === 'failed') && (
            <Alert
              type={run.status === 'failed' ? 'error' : run.status === 'completed' ? 'success' : 'info'}
              showIcon
              message={`${subjectLabel}${displayStatus(run.status)}`}
              description="步骤轨道展示完整处理流程；事件日志按实际时间记录每一步的开始、完成和错误。"
              style={{ marginBottom: 12 }}
            />
          )}
          {liveStreamUnavailable && (
            <Alert
              type="warning"
              showIcon
              message="实时日志流暂不可用"
              description="运行状态仍按 2.5 秒轮询刷新，错误详情可能延迟显示。"
              style={{ marginBottom: 12 }}
            />
          )}
          <div className="grok-chip-row" style={{ marginBottom: 8 }}>
            <GrokChip tone={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'warn' : undefined}>
              {displayStatus(run.status)}
            </GrokChip>
            <Tag>{String(run.context?.device_name || run.context?.device_id || run.template_name)}</Tag>
            {run.ticket_id && <Tag color="blue">{run.ticket_id}</Tag>}
            {run.langfuse_url && (
              <a href={run.langfuse_url} target="_blank" rel="noreferrer">
                <Tag icon={<LinkOutlined />} color="geekblue">Langfuse Trace</Tag>
              </a>
            )}
          </div>
          <Descriptions size="small" bordered column={2} style={{ marginTop: 10 }}>
            <Descriptions.Item label="开始时间">{formatTimestamp(run.created_at)}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{formatTimestamp(run.completed_at)}</Descriptions.Item>
            <Descriptions.Item label="总耗时">{durationText(run.created_at, run.completed_at)}</Descriptions.Item>
            <Descriptions.Item label="执行编号">
              <Text copyable={{ text: run.run_id }} code>{run.run_id}</Text>
            </Descriptions.Item>
          </Descriptions>
          {run.error_message && <Alert type="error" message={run.error_message} style={{ marginTop: 8 }} />}

          {run.child_runs && run.child_runs.length > 0 && (
            <Alert
              type="info"
              style={{ marginTop: 8 }}
              message="子 Workflow"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {run.child_runs.map((c) => (
                    <li key={c.run_id}>
                      {c.template_name} — <Tag>{c.status}</Tag> <Text code>{c.run_id.slice(0, 8)}</Text>
                    </li>
                  ))}
                </ul>
              }
            />
          )}

          <Steps
            direction="vertical"
            size="small"
            current={run.current_step_index}
            style={{ marginTop: 16 }}
            items={run.steps.map((s) => ({
              title: patrolStepMeta[s.skill_name]?.label || s.step_name,
              status:
                s.status === 'completed'
                  ? 'finish'
                  : s.status === 'failed'
                    ? 'error'
                    : s.status === 'running'
                      ? 'process'
                      : 'wait',
              description: (
                <Space direction="vertical" size={3}>
                  <Text type="secondary">
                    {patrolStepMeta[s.skill_name]?.description || s.skill_name}
                  </Text>
                  <Space wrap size={[8, 2]}>
                    <Text type="secondary">状态：{displayStatus(s.status)}</Text>
                    <Text type="secondary">开始：{formatTime(s.started_at || undefined) || '-'}</Text>
                    <Text type="secondary">完成：{formatTime(s.completed_at || undefined) || '-'}</Text>
                    <Text type="secondary">耗时：{durationText(s.started_at, s.completed_at)}</Text>
                    {s.celery_task_id && <Text copyable code>任务 {s.celery_task_id}</Text>}
                  </Space>
                  {s.error_message && <Text type="danger">{s.error_message}</Text>}
                  {renderArtifacts(s.output_artifacts)}
                </Space>
              ),
            }))}
          />

          {timeline.length > 0 && (
            <Collapse
              style={{ marginTop: 16 }}
              items={[{
                key: 'execution-log',
                label: `事件日志（${timeline.length}）`,
                children: (
                  <List
                    size="small"
                    dataSource={timeline}
                    renderItem={(ev) => (
                      <List.Item>
                        <List.Item.Meta
                          title={
                            <span>
                              <Tag color={STATUS_COLOR[ev.status] || 'default'}>{displayStatus(ev.status)}</Tag>
                              {ev.step_name && <Text strong>{patrolStepMeta[String(ev.skill_name || '')]?.label || ev.step_name}</Text>}
                            </span>
                          }
                          description={
                            <>
                              {ev.message && <div>{ev.message}</div>}
                              <Text type="secondary" style={{ fontSize: 11 }}>{formatTime(ev.timestamp)}</Text>
                            </>
                          }
                        />
                      </List.Item>
                    )}
                  />
                ),
              }]}
            />
          )}

        </>
      ) : null}
    </>
  )

  if (plain) return content

  if (embedded) {
    return (
      <Card size="small" title="设备执行过程" extra={onClose ? <a onClick={onClose}>关闭</a> : null}>
        {content}
      </Card>
    )
  }

  return (
    <Card
      className="grok-run-monitor"
      title="Workflow 运行"
      extra={onClose ? <a onClick={onClose}>关闭</a> : <a onClick={() => refetch()}>刷新</a>}
    >
      {content}
    </Card>
  )
}

export default WorkflowRunMonitor
