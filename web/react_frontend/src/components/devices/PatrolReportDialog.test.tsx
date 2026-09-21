import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import PatrolReportDialog from './PatrolReportDialog'
import { patrolReports } from '../../services/patrolReports'

vi.mock('../../context/AuthContext', () => ({ useIsAdmin: () => false }))
vi.mock('../../utils/storageLocalOpen', () => ({ downloadBlob: vi.fn() }))
vi.mock('../../services/patrolReports', () => ({ patrolReports: {
  templates: vi.fn().mockResolvedValue([{ id: 'builtin-v1', name: '网络巡检 v1' }]),
  preview: vi.fn().mockResolvedValue({ conclusion: '需复核', limitations: '覆盖有限', evidence_count: 0, text: { 'narrative.summary': '本地概述', 'changes.summary': '未比较' } }),
  export: vi.fn().mockResolvedValue(new Blob(['docx'])),
  details: vi.fn().mockResolvedValue(new Blob(['xlsx'])),
  prepare: vi.fn().mockResolvedValue({ id: 'draft', digest: 'hash', request: { messages: ['仅汇总统计'] }, provider: { endpoint: 'https://model.example/v1', model: 'test' }, bytes: 100 }),
  generate: vi.fn().mockResolvedValue({ id: 'draft', state: 'ready', output: { sections: [{ id: 'narrative.summary', paragraphs: [{ text: '建议概述', fact_ids: ['F001'] }] }] } }),
  accept: vi.fn().mockResolvedValue({ state: 'accepted' }), evidence: vi.fn().mockResolvedValue({ total: 0, items: [] }),
} }))

beforeEach(() => vi.clearAllMocks())

test('exports detailed Excel locally without sending data to AI', async () => {
  render(<PatrolReportDialog jobId="job" onClose={() => {}} />)
  await screen.findByText('覆盖有限')
  await userEvent.click(screen.getByRole('button', { name: '导出详细数据 Excel' }))
  await waitFor(() => expect(patrolReports.details).toHaveBeenCalledWith('job'))
  expect(patrolReports.prepare).not.toHaveBeenCalled()
})

test('defaults to local export and exposes template import to an App user', async () => {
  render(<PatrolReportDialog jobId="job" onClose={() => {}} />)
  await screen.findByText('覆盖有限')
  expect(screen.getByText('导入模板')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '导出 Word' }))
  await waitFor(() => expect(patrolReports.export).toHaveBeenCalledWith('job', expect.objectContaining({ template_id: 'builtin-v1', draft_id: undefined, bundle: false })))
  expect(patrolReports.prepare).not.toHaveBeenCalled()
  expect(patrolReports.generate).not.toHaveBeenCalled()
})

test('shows evidence integrity failures and prevents exporting an invalid report', async () => {
  vi.mocked(patrolReports.preview).mockRejectedValueOnce(new Error('历史证据引用不一致，请复用文件新建分析'))
  render(<PatrolReportDialog jobId="invalid-job" onClose={() => {}} />)
  await screen.findByText('历史证据引用不一致，请复用文件新建分析')
  expect(screen.getByRole('button', { name: '导出 Word' })).toBeDisabled()
  expect(patrolReports.export).not.toHaveBeenCalled()
  expect(patrolReports.generate).not.toHaveBeenCalled()
})

test('preview does not send; sending and adopting are separate explicit actions', async () => {
  render(<PatrolReportDialog jobId="job" onClose={() => {}} />)
  await screen.findByText('覆盖有限')
  await userEvent.click(screen.getByRole('switch', { name: '使用 AI 起草' }))
  expect(patrolReports.generate).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '预览将发送的内容' }))
  await screen.findByText(/仅汇总统计/)
  expect(patrolReports.generate).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '确认发送并起草' }))
  await screen.findByText('建议概述')
  expect(patrolReports.accept).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '核对后采纳这些段落' }))
  await screen.findByText('已采纳，导出时使用')
  await userEvent.click(screen.getByRole('button', { name: '导出 Word' }))
  await waitFor(() => expect(patrolReports.export).toHaveBeenCalledWith('job', expect.objectContaining({ draft_id: 'draft' })))
})
