import React, { useEffect, useState } from 'react'
import './TraceProgress.css'

export interface AgentStep {
  id: string
  label: string
  detail?: string
  operation?: string
  summary?: string
  status: string
  started_at: string
  ended_at?: string
}
export interface AgentProgress {
  run_id: string
  status: string
  message: string
  started_at: string
  ended_at?: string
  steps: AgentStep[]
}
export const isRunning = (progress?: AgentProgress | null) => !!progress && ['running', 'cancel_requested'].includes(progress.status)
export const pendingProgress = (): AgentProgress => ({ run_id: '', status: 'running', message: '正在发送问题', started_at: new Date().toISOString(), steps: [] })

function duration(start: string, end: string | undefined, now: number) {
  const seconds = Math.max(0, Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000))
  return Number.isFinite(seconds) ? (seconds < 1 ? '不足 1 秒' : `${seconds} 秒`) : ''
}

export default function TraceProgress({ progress, onCancel }: { progress: AgentProgress; onCancel?: () => void }) {
  const running = isRunning(progress)
  const [expanded, setExpanded] = useState(running)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { setExpanded(running) }, [progress.run_id, running])
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const labels: Record<string, string> = { running: '进行中', completed: '已完成', failed: '失败', interrupted: '已中断' }
  return <section className="agent-progress" aria-label="执行过程">
    <div className="agent-progress-heading">
      <button type="button" className="agent-progress-toggle" aria-label={`${expanded ? '收起' : '展开'}执行过程：${progress.message}`} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span aria-hidden="true" className={running ? 'agent-progress-dot is-running' : 'agent-progress-dot'} />
        <span role="status">{progress.message}</span>
        {progress.steps.length > 0 && <span className="agent-progress-time">{progress.steps.length} 次调用</span>}
        <span className="agent-progress-time">{duration(progress.started_at, progress.ended_at, now)}</span>
        <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
      </button>
      {running && onCancel && <button className="agent-progress-stop" type="button" disabled={progress.status === 'cancel_requested'} onClick={onCancel}>停止</button>}
    </div>
    {expanded && <div className="agent-progress-body">
      {progress.steps.length ? <ol>{progress.steps.map(step => <li key={step.id}>
        <span className={`agent-step-marker ${step.status}`} aria-hidden="true">{step.status === 'completed' ? '✓' : step.status === 'running' ? '·' : '!'}</span>
        <div className="agent-step-content"><span>{step.label}</span>{step.detail && <small>{step.detail}</small>}
          {step.operation && <pre className="agent-step-operation"><code>{step.operation}</code></pre>}
          {step.summary && <small>{step.summary}</small>}
        </div>
        <span className="agent-step-state">{labels[step.status] || '已结束'} · {duration(step.started_at, step.ended_at, now)}</span>
      </li>)}</ol> : <p>{running ? '准备就绪后会显示搜索、阅读与业务查询记录。' : '本轮没有工具调用记录。'}</p>}
    </div>}
  </section>
}
