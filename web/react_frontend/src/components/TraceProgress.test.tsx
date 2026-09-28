import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TraceProgress, { type AgentProgress } from './TraceProgress'
afterEach(cleanup)
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
  expect(screen.getByText('read({"file_path": "manual.txt", "offset": 80, "limit": 20})')).toBeInTheDocument()
  expect(screen.getByText('读取 20 行')).toBeInTheDocument()
  expect(screen.getByText('已完成 · 不足 1 秒')).toBeInTheDocument()
})

it('exposes a collapsed, plain-text execution result inside saved steps', () => {
  render(<TraceProgress progress={{ ...progress, steps: [{ ...progress.steps[0],
    output: { text: 'manual.txt:3 精油 <script>bad()</script>', truncated: true } }] }} />)
  const toggle = screen.getByText('查看执行结果')
  const details = toggle.closest('details')!
  expect(details).not.toHaveAttribute('open')
  fireEvent.click(toggle)
  expect(details).toHaveAttribute('open')
  expect(screen.getByText('manual.txt:3 精油 <script>bad()</script>')).toBeInTheDocument()
  expect(details.querySelector('script')).toBeNull()
  expect(screen.getByText(/仅展示前 12,000 字符/)).toBeInTheDocument()
})
