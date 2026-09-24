import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { patrolReports, type ReportEvidence } from '../../services/patrolReports'
import PatrolCliEvidence, { LazyPatrolCliEvidence, PatrolCliComparison } from './PatrolCliEvidence'

vi.mock('../../services/patrolReports', () => ({ patrolReports: { evidence: vi.fn(), fullEvidence: vi.fn() } }))

const entry: ReportEvidence = {
  id: 'CLI-001', device: 'SW1', entity: '["GE1/0/7"]', command: 'display transceiver diagnosis interface',
  note: '两期对象摘录一致；当前值低于设备下限。', references: ['obs-1'],
  baseline: { snapshot_id: 'base-1', observed_at: '2026-09-01T00:00:00+08:00' },
  current: { snapshot_id: 'current-1', observed_at: '2026-09-03T00:00:00+08:00' },
  rows: [
    { baseline: { line: 93, text: 'GE1/0/7 diagnostic information' }, current: { line: 93, text: 'GE1/0/7 diagnostic information' }, change: 'equal' },
    { baseline: { line: 96, text: 'RX -13.07' }, current: { line: 96, text: 'RX -13.07', anomaly_span: [3, 9] }, change: 'equal' },
  ],
}
function mount(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(patrolReports.evidence).mockResolvedValue({ total: 1, source_digest: 'digest', items: [entry] })
  vi.mocked(patrolReports.fullEvidence).mockResolvedValue({ baseline: 'FULL BASE', current: 'FULL CURRENT' })
})

it('loads the exact observation and shows source lines before requesting full output', async () => {
  const user = userEvent.setup()
  mount(<PatrolCliEvidence jobId="job" reference="obs-1" />)
  expect(await screen.findByText('两期对象摘录一致；当前值低于设备下限。')).toBeVisible()
  expect(patrolReports.evidence).toHaveBeenCalledWith('job', 0, 'obs-1', expect.any(AbortSignal))
  expect(screen.getByText('基线 CLI 原文')).toBeVisible()
  expect(screen.getByText('快照：base-1')).toBeVisible()
  expect(screen.getByTitle('当前异常值')).toHaveTextContent('-13.07')
  expect(screen.getAllByText('……省略其他原文行……')).toHaveLength(2)
  expect(patrolReports.fullEvidence).not.toHaveBeenCalled()
  await user.click(screen.getByText('查看完整命令输出'))
  expect(await screen.findByText('FULL CURRENT')).toBeVisible()
  expect(patrolReports.fullEvidence).toHaveBeenCalledWith('job', 'CLI-001', 'digest', undefined, expect.any(AbortSignal))
})

it('keeps recovery changes unmarked and escapes raw CLI markup', () => {
  const recovery = { ...entry, rows: [{ baseline: { line: 1, text: '-32.00' }, current: { line: 1, text: '<script>alert(1)</script> -2.00' }, change: 'replace' }] }
  const result = mount(<PatrolCliComparison entry={recovery} jobId="job" digest="d" />)
  expect(screen.queryByTitle('当前异常值')).not.toBeInTheDocument()
  expect(screen.getByText('<script>alert(1)</script> -2.00')).toBeVisible()
  expect(result.container.querySelector('script')).toBeNull()
})

it('preserves Unicode code point offsets and shows absent baseline explicitly', () => {
  mount(<PatrolCliComparison entry={{ ...entry, rows: [{ baseline: null,
    current: { line: 1, text: '😀 -2.00', anomaly_span: [2, 7] }, change: 'insert' }] }} jobId="j" digest="d" />)
  expect(screen.getByTitle('当前异常值')).toHaveTextContent('-2.00')
  expect(screen.getByText('— 无对应原文行')).toBeVisible()
})

it('does not fetch collapsed trend evidence', async () => {
  const user = userEvent.setup()
  mount(<LazyPatrolCliEvidence jobId="job" reference="recovered-obs" />)
  expect(patrolReports.evidence).not.toHaveBeenCalled()
  await user.click(screen.getByText('查看基线与当前 CLI 原文对比'))
  await screen.findByText(entry.note)
  expect(patrolReports.evidence).toHaveBeenCalledWith('job', 0, 'recovered-obs', expect.any(AbortSignal))
})

it('can retry a failed excerpt request without claiming empty evidence', async () => {
  vi.mocked(patrolReports.evidence).mockRejectedValueOnce(new Error('offline'))
  const user = userEvent.setup()
  mount(<PatrolCliEvidence jobId="job" reference="obs" />)
  expect(await screen.findByText('CLI 对比加载失败，请重试；技术追溯信息仍保留。')).toBeVisible()
  await user.click(screen.getByText('重新加载对比'))
  expect(await screen.findByText(entry.note)).toBeVisible()
  await waitFor(() => expect(patrolReports.evidence).toHaveBeenCalledTimes(2))
})
