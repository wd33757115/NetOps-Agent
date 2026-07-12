// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Card, List, Space, Spin, Steps, Tag, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { workflowApi, WorkflowTimelineEvent } from '../../services/api'
import { GrokChip } from '../ui/GrokUi'

const { Text } = Typography

interface WorkflowRunMonitorProps {
  runId: string | null
  onClose?: () => void
  embedded?: boolean
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

type ArtifactMeta = {
  download_url?: string
  filename?: string
  content_type?: string
  file_key?: string
}

function artifactLabel(key: string, meta?: ArtifactMeta) {
  if (key === 'raw_outputs_archive') return 'Raw CLI outputs'
  if (key === 'analysis_report') return 'Analysis report'
  if (key === 'patrol_report') return 'Patrol report'
  if (key === 'change_excel') return 'Change ticket'
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

const WorkflowRunMonitor: React.FC<WorkflowRunMonitorProps> = ({ runId, onClose, embedded }) => {
  const [liveEvents, setLiveEvents] = useState<WorkflowTimelineEvent[]>([])

  useEffect(() => {
    setLiveEvents([])
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
    for (const ev of liveEvents) {
      const key = `${ev.timestamp}-${ev.status}-${ev.step_name}-${ev.message}`
      if (!merged.some((m) => `${m.timestamp}-${m.status}-${m.step_name}-${m.message}` === key)) {
        merged.push(ev)
      }
    }
    return merged.sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')))
  }, [run?.timeline, liveEvents])

  useEffect(() => {
    if (!runId || !runStatus) return undefined
    if (runStatus === 'completed' || runStatus === 'failed') return undefined

    const timer = window.setInterval(() => {
      refetch()
    }, 2500)
    return () => window.clearInterval(timer)
  }, [runId, runStatus, refetch])

  useEffect(() => {
    if (!runId || !runStatus) return undefined
    if (runStatus === 'completed' || runStatus === 'failed') return undefined

    const url = workflowApi.getRunEventsStreamUrl(runId)
    const es = new EventSource(url, { withCredentials: true })

    const onTimeline = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data) as WorkflowTimelineEvent
        setLiveEvents((prev) => [...prev, payload])
      } catch {
        /* ignore */
      }
    }
    const onDone = () => {
      refetch()
      es.close()
    }

    es.addEventListener('timeline', onTimeline)
    es.addEventListener('progress', onTimeline)
    es.addEventListener('done', onDone)
    es.onerror = () => es.close()

    return () => es.close()
  }, [runId, runStatus, refetch])

  if (!runId) return null

  const content = (
    <>
      {isLoading && !run ? (
        <Spin />
      ) : run ? (
        <>
          <div className="grok-chip-row" style={{ marginBottom: 12 }}>
            <GrokChip tone={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'warn' : undefined}>
              {run.status}
            </GrokChip>
            <Tag>{run.template_name}</Tag>
            {run.ticket_id && <Tag color="blue">{run.ticket_id}</Tag>}
            {run.langfuse_url && (
              <a href={run.langfuse_url} target="_blank" rel="noreferrer">
                <Tag icon={<LinkOutlined />} color="geekblue">Langfuse Trace</Tag>
              </a>
            )}
          </div>
          <Text copyable={{ text: run.run_id }} style={{ fontSize: 12 }}>Run ID: {run.run_id}</Text>
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
              title: `${s.step_name} (${s.skill_name})`,
              status:
                s.status === 'completed'
                  ? 'finish'
                  : s.status === 'failed'
                    ? 'error'
                    : s.status === 'running'
                      ? 'process'
                      : 'wait',
              description: s.error_message || renderArtifacts(s.output_artifacts),
            }))}
          />

          {timeline.length > 0 && (
            <Card size="small" title="运行时间线" style={{ marginTop: 16 }}>
              <List
                size="small"
                dataSource={timeline}
                renderItem={(ev) => (
                  <List.Item>
                    <List.Item.Meta
                      title={
                        <span>
                          <Tag color={STATUS_COLOR[ev.status] || 'default'}>{ev.status}</Tag>
                          {ev.step_name && <Text strong>{ev.step_name}</Text>}
                          {ev.skill_name && <Text type="secondary"> · {ev.skill_name}</Text>}
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
            </Card>
          )}

          {run.steps.some((s) => s.skill_name === 'llm-result-analyzer' && hasArtifacts(s.output_artifacts)) && (
            <Alert
              type="success"
              style={{ marginTop: 12 }}
              message="LLM 分析报告已生成"
              description={
                run.steps
                  .filter((s) => s.skill_name === 'llm-result-analyzer')
                  .map((s) => {
                    const art = s.output_artifacts as Record<string, { download_url?: string }> | null
                    const url = art?.analysis_report?.download_url
                    return url ? (
                      <a key={s.step_name} href={url} target="_blank" rel="noreferrer">
                        下载分析报告
                      </a>
                    ) : (
                      '见步骤产物'
                    )
                  })
              }
            />
          )}
        </>
      ) : null}
    </>
  )

  if (embedded) {
    return (
      <Card size="small" title="运行监控" extra={onClose ? <a onClick={onClose}>关闭</a> : null}>
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
