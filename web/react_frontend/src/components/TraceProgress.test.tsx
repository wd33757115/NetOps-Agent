import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TraceProgress, { type AgentProgress } from './TraceProgress'
import { api } from '../config/api'
vi.mock('../config/api', () => ({ api: { get: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const progress: AgentProgress = { run_id: 'r1', status: 'running', message: '正在综合资料与工具结果', started_at: '2026-09-21T00:00:00Z',
  steps: [{ id: 't1', label: '阅读原文', detail: 'BGP EVPN / RR', status: 'completed', started_at: '2026-09-21T00:00:00Z', ended_at: '2026-09-21T00:00:02Z' }] }
it('shows live calls, cancellation, and a collapsible completed record', () => {
  const stop = vi.fn()
  const view = render(<TraceProgress progress={progress} onCancel={stop} />)
  expect(screen.getByText('BGP EVPN / RR')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '停止' }))
  expect(stop).toHaveBeenCalledOnce()
  view.rerender(<TraceProgress progress={{ ...progress, status: 'completed', message: '已完成', ended_at: '2026-09-21T00:00:04Z' }} />)
  expect(screen.queryByText('BGP EVPN / RR')).not.toBeInTheDocument()
  expect(screen.getByText('4 秒')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  expect(screen.getByText('BGP EVPN / RR')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument()
})

it('retains real operations and result summaries in completed history', () => {
  render(<TraceProgress progress={{ ...progress, status: 'completed', message: '已完成',
    steps: [{ ...progress.steps[0], operation: 'read({"file_path": "manual.txt", "offset": 80, "limit": 20})',
      summary: '读取 20 行', ended_at: '2026-09-21T00:00:00.200Z' }] }} />)
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  fireEvent.click(screen.getByRole('button', { name: /^展开阅读原文/ }))
  expect(document.querySelector('.agent-step-operation')).toHaveTextContent('read({"file_path": "manual.txt", "offset": 80, "limit": 20})')
  expect(screen.getAllByText('读取 20 行').length).toBeGreaterThan(0)
  expect(screen.getByText('已完成 · 不足 1 秒')).toBeInTheDocument()
})

it('exposes a collapsed, plain-text execution result inside saved steps', () => {
  render(<TraceProgress progress={{ ...progress, steps: [{ ...progress.steps[0],
    output: { text: 'manual.txt:3 精油 <script>bad()</script>', truncated: true } }] }} />)
  const toggle = screen.getByRole('button', { name: /^展开阅读原文/ })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('manual.txt:3 精油 <script>bad()</script>')).not.toBeInTheDocument()
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('manual.txt:3 精油 <script>bad()</script>')).toBeInTheDocument()
  expect(document.querySelector('.agent-step-result script')).toBeNull()
  expect(screen.getByText('结果较长，显示已保存的片段。')).toBeInTheDocument()
})

it('fetches a business result only on expansion and displays individual device commands', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: { text: JSON.stringify({ host: '10.10.1.1', commands: [
    { command: 'display interface GE2/0', status: 'succeeded', output: 'Current state: Administratively DOWN\nLine protocol state: DOWN' },
    { command: 'display ospf peer brief', status: 'succeeded', output: '% Wrong parameter found at position.' },
    { command: 'display this', status: 'not_executed', reason: '配置导出不执行' },
  ] }), truncated: false } })
  render(<TraceProgress progress={{ ...progress, steps: [{ ...progress.steps[0], label: '设备只读取证', detail: '',
    operation: 'device_access__run({"host":"10.10.1.1"})', has_output: true }] }} />)
  expect(api.get).not.toHaveBeenCalled()
  const toggle = screen.getByRole('button', { name: /^展开设备只读取证/ })
  fireEvent.click(toggle)
  expect(await screen.findByText(/Current state: Administratively DOWN/)).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith('/apps/assistant/turns/r1/steps/t1/output')
  expect(screen.getByText('display interface GE2/0')).toBeInTheDocument()
  expect(screen.getByText('% Wrong parameter found at position.')).toBeInTheDocument()
  expect(screen.getByText('配置导出不执行')).toBeInTheDocument()
  expect(screen.getByText('10.10.1.1')).toBeInTheDocument()
  fireEvent.click(toggle)
  expect(screen.queryByText(/Current state: Administratively DOWN/)).not.toBeInTheDocument()
  fireEvent.click(toggle)
  expect(screen.getByText(/Current state: Administratively DOWN/)).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledTimes(1)
})

it('loads a result when an expanded live call finishes', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: { text: '{"status":"confirmed"}', truncated: false } })
  const step = { ...progress.steps[0], status: 'running', operation: 'topology__locate({"query":"1.1.1.2"})' }
  const view = render(<TraceProgress progress={{ ...progress, steps: [step] }} />)
  fireEvent.click(screen.getByRole('button', { name: /^展开阅读原文/ }))
  expect(screen.getByText('执行中，结果返回后会显示在这里。')).toBeInTheDocument()
  expect(api.get).not.toHaveBeenCalled()
  view.rerender(<TraceProgress progress={{ ...progress, steps: [{ ...step, status: 'completed', has_output: true }] }} />)
  expect(await screen.findByText('{"status":"confirmed"}')).toBeInTheDocument()
})

it('reports denied access without exposing protected output or claiming it is empty', async () => {
  vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } })
  render(<TraceProgress progress={{ ...progress, steps: [{ ...progress.steps[0], has_output: true }] }} />)
  fireEvent.click(screen.getByRole('button', { name: /^展开阅读原文/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent('当前无权查看此执行结果。')
  expect(document.querySelector('.agent-step-result')).toBeNull()
})

it('allows retrying a failed result read and marks unavailable historical results', async () => {
  vi.mocked(api.get).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: { text: 'saved output', truncated: false } })
  const view = render(<TraceProgress progress={{ ...progress, steps: [{ ...progress.steps[0], has_output: true }] }} />)
  fireEvent.click(screen.getByRole('button', { name: /^展开阅读原文/ }))
  fireEvent.click(await screen.findByRole('button', { name: '重试' }))
  expect(await screen.findByText('saved output')).toBeInTheDocument()
  view.rerender(<TraceProgress progress={{ ...progress, run_id: 'old', steps: progress.steps }} />)
  fireEvent.click(screen.getByRole('button', { name: /^展开阅读原文/ }))
  expect(screen.getByText('此记录未保存可展示的执行结果。')).toBeInTheDocument()
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
})
