import React, { useEffect, useState } from 'react'
import { api } from '../config/api'
import './TraceProgress.css'

interface StepOutput { text: string; truncated: boolean }
export interface AgentStep {
  id: string
  label: string
  detail?: string
  operation?: string
  summary?: string
  output?: StepOutput
  has_output?: boolean
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

const STEP_LABELS: Record<string, string> = { running: '进行中', completed: '已完成', failed: '失败', interrupted: '已中断' }
interface CommandOutput {
  command: string
  output?: string
  reason?: string
  failure_code?: string
  status?: string
  output_truncated?: boolean
  output_complete?: boolean
}

function ExecutionOutput({ output }: { output: StepOutput }) {
  let result
  try {
    const parsed = JSON.parse(output.text)
    result = parsed?.data ?? parsed
  } catch { /* Native tools may return plain text. */ }
  const commands: CommandOutput[] | undefined = result && typeof result.host === 'string'
    && Array.isArray(result.commands) && result.commands.every((item: CommandOutput) => item && typeof item.command === 'string')
    ? result.commands : undefined
  const commandLabels: Record<string, string> = { succeeded: '已返回', failed: '失败', not_executed: '未执行' }
  return <div className="agent-step-result">
    <div className="agent-result-heading"><span>执行结果</span>{commands && <code>{result.host}</code>}</div>
    {commands?.length ? <div className="agent-command-results">{commands.map((command, index) => <div className="agent-command-result" key={index}>
      <div className="agent-command-heading"><code>{command.command}</code><span>{commandLabels[command.status || ''] || command.status}</span></div>
      <pre><code>{command.output || command.reason || command.failure_code || '（无输出）'}</code></pre>
      {command.output_truncated && <small>输出较长，显示已保存的片段。</small>}
      {command.output_complete === false && <small>本次未取得完整输出。</small>}
    </div>)}</div> : <pre><code>{output.text || '（无输出）'}</code></pre>}
    {output.truncated && <small>结果较长，显示已保存的片段。</small>}
  </div>
}

function ExecutionStep({ step, runId, now }: { step: AgentStep; runId: string; now: number }) {
  const [expanded, setExpanded] = useState(false)
  const [remoteOutput, setRemoteOutput] = useState<StepOutput>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const output = step.output || remoteOutput
  const contentId = React.useId()
  useEffect(() => {
    if (!expanded || output || !step.has_output || !runId) return
    let disposed = false
    setLoading(true)
    setError('')
    void api.get<StepOutput>(`/apps/assistant/turns/${encodeURIComponent(runId)}/steps/${encodeURIComponent(step.id)}/output`)
      .then(({ data }) => { if (!disposed) setRemoteOutput(data) })
      .catch((error: { response?: { status?: number } }) => {
        if (disposed) return
        const status = error.response?.status
        setError(status === 403 ? '当前无权查看此执行结果。' : status === 404 ? '此记录未保存可展示的执行结果。' : '执行结果读取失败，请重试。')
      })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [expanded, output, step.has_output, runId, step.id, attempt])
  const label = STEP_LABELS[step.status] || '已结束'
  return <li className="agent-step">
    <button type="button" className="agent-step-toggle" aria-expanded={expanded} aria-controls={contentId}
      aria-label={`${expanded ? '收起' : '展开'}${step.label}：${step.operation || step.detail || label}`}
      onClick={() => setExpanded(!expanded)}>
      <span className={`agent-step-marker ${step.status}`} aria-hidden="true">{step.status === 'completed' ? '✓' : step.status === 'running' ? '·' : '!'}</span>
      <span className="agent-step-title"><span>{step.label}</span>{step.operation ? <code title={step.operation}>{step.operation}</code> : step.detail && <span>{step.detail}</span>}</span>
      <span className="agent-step-state">{label} · {duration(step.started_at, step.ended_at, now)}</span>
      <svg className="agent-step-chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m5 3 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
    </button>
    {expanded && <div id={contentId} className="agent-step-content">
      {step.detail && <small>{step.detail}</small>}
      {step.operation && <pre className="agent-step-operation"><code>{step.operation}</code></pre>}
      {step.summary && <small>{step.summary}</small>}
      {output ? <ExecutionOutput output={output} /> : loading ? <p role="status">正在读取执行结果…</p> : error ? <div className="agent-result-error" role="alert">{error}
        {error.includes('重试') && <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button>}
      </div> : !step.summary && <p className="agent-result-empty">{step.status === 'running' ? '执行中，结果返回后会显示在这里。' : '此记录未保存可展示的执行结果。'}</p>}
    </div>}
  </li>
}

/** What the model said, and reasoned in thinking mode, before one round of calls. */
interface Thought { call_ids: string[]; said: string; reasoning: string }

function ThoughtNote({ thought }: { thought: Thought }) {
  return <li className="agent-thought">
    {thought.said && <p><span>思路</span>{thought.said}</p>}
    {thought.reasoning && <details>
      <summary>推理过程（{thought.reasoning.length} 字）</summary>
      <pre>{thought.reasoning}</pre>
    </details>}
  </li>
}

export default function TraceProgress({ progress, onCancel }: { progress: AgentProgress; onCancel?: () => void }) {
  const running = isRunning(progress)
  const [expanded, setExpanded] = useState(running)
  const [now, setNow] = useState(Date.now())
  const [thoughts, setThoughts] = useState<Record<string, Thought>>({})
  useEffect(() => { setExpanded(running) }, [progress.run_id, running])
  useEffect(() => {
    if (!expanded || !progress.run_id) return
    let disposed = false
    // Keyed by the first call of each round, so a note sits above the calls it led to.
    void api.get<{ steps: Thought[] }>(`/apps/assistant/turns/${encodeURIComponent(progress.run_id)}/thinking`)
      .then(({ data }) => {
        if (!disposed) setThoughts(Object.fromEntries(data.steps.filter(step => step.call_ids.length).map(step => [step.call_ids[0], step])))
      })
      .catch(() => undefined) // Optional context: the calls themselves still show.
    return () => { disposed = true }
  }, [expanded, progress.run_id, progress.steps.length, progress.status])
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
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
      {progress.steps.length ? <ol>{progress.steps.map(step => <React.Fragment key={`${progress.run_id}:${step.id}`}>
        {thoughts[step.id] && <ThoughtNote thought={thoughts[step.id]} />}
        <ExecutionStep step={step} runId={progress.run_id} now={now} />
      </React.Fragment>)}</ol>
        : <p>{running ? '准备就绪后会显示搜索、阅读与业务查询记录。' : '本轮没有工具调用记录。'}</p>}
    </div>}
  </section>
}
