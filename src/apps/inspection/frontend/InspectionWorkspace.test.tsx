import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PatrolTaskInbox from './components/PatrolTaskInbox'
import InspectionLayout from './InspectionLayout'
import { inspectionView } from './inspectionNavigation'

const { online, offline } = vi.hoisted(() => ({ online: vi.fn(), offline: vi.fn() }))
vi.mock('@netops/services/api', () => ({ workflowApi: { listPatrolBatches: online }, offlinePatrolApi: { list: offline } }))
function Location() { return <output data-testid="location">{useLocation().search}</output> }
const open = (element: React.ReactNode, initial = '/?patrol_view=tasks') => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } }, logger: { log: () => {}, warn: () => {}, error: () => {} } })}>
    <MemoryRouter initialEntries={[initial]}>{element}<Location /></MemoryRouter>
  </QueryClientProvider>,
)
beforeEach(() => {
  online.mockReset().mockResolvedValue({ items: [{ patrol_batch_id: 'batch-1', target_label: '核心交换机', aggregate_status: 'completed', total: 2, created_at: '2026-09-21T02:00:00Z' }] })
  offline.mockReset().mockResolvedValue({ items: [{ id: 'file-1', name: '离线核查', status: 'blocked', blocker_count: 2, created_at: '2026-09-21T01:00:00Z', preview_totals: { device_count: 3, file_count: 9 } }] })
})
describe('巡检任务中心', () => {
  it('selects a task without navigating, then opens the correct offline work item', async () => {
    open(<PatrolTaskInbox />, '/?patrol_view=tasks&patrol_batch_id=old&offline_job_tab=result&batch_page=2')
    fireEvent.click(await screen.findByRole('button', { name: '离线核查', exact: true }))
    const summary = screen.getByRole('complementary', { name: '任务摘要' })
    expect(within(summary).getByText('3 台设备 · 9 份文件')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('patrol_view=tasks')
    fireEvent.click(within(summary).getByRole('button', { name: '打开任务' }))
    expect(screen.getByTestId('location')).toHaveTextContent('patrol_view=offline')
    expect(screen.getByTestId('location')).toHaveTextContent('offline_job_id=file-1')
    expect(screen.getByTestId('location')).toHaveTextContent('offline_job_tab=overview')
    expect(screen.getByTestId('location')).toHaveTextContent('batch_page=2')
    expect(screen.getByTestId('location')).not.toHaveTextContent('patrol_batch_id')
  })
  it('opens completed online results and removes stale offline selection', async () => {
    open(<PatrolTaskInbox />, '/?offline_job_id=old&offline_job_tab=result')
    fireEvent.click(await screen.findByRole('button', { name: '查看结果', exact: true }))
    expect(screen.getByTestId('location')).toHaveTextContent('patrol_view=online')
    expect(screen.getByTestId('location')).toHaveTextContent('patrol_batch_id=batch-1')
    expect(screen.getByTestId('location')).not.toHaveTextContent('offline_job')
  })
  it('supports source filtering and search reset', async () => {
    open(<PatrolTaskInbox />)
    await screen.findByRole('button', { name: '核心交换机', exact: true })
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '任务来源' }))
    fireEvent.click(await screen.findByText('离线文件', { selector: '.ant-select-item-option-content' }))
    expect(screen.queryByRole('button', { name: '核心交换机', exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '离线核查', exact: true })).toBeInTheDocument()
    online.mockResolvedValue({ items: [] }); offline.mockResolvedValue({ items: [] })
    const input = screen.getByRole('searchbox', { name: '搜索巡检任务' })
    fireEvent.change(input, { target: { value: ' 无匹配 ' } }); fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', charCode: 13 })
    await waitFor(() => expect(online).toHaveBeenCalledWith({ limit: 20, search: '无匹配' }))
    await screen.findByText('没有匹配的巡检任务')
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    expect(input).toHaveValue('')
    await waitFor(() => expect(screen.queryByText('没有匹配的巡检任务')).not.toBeInTheDocument())
    expect(screen.getByText('全部来源', { selector: '.ant-select-selection-item' })).toBeInTheDocument()
  })
  it('shows a retryable error while preserving the available tasks', async () => {
    online.mockRejectedValue(new Error('offline'))
    open(<PatrolTaskInbox />)
    await screen.findByText('部分任务加载失败')
    expect(screen.getByRole('button', { name: '离线核查', exact: true })).toBeInTheDocument()
    expect(screen.queryByText('从一次巡检开始')).not.toBeInTheDocument()
    online.mockResolvedValue({ items: [] })
    fireEvent.click(screen.getByRole('button', { name: /^重\s*试$/, exact: true }))
    await waitFor(() => expect(screen.queryByText('部分任务加载失败')).not.toBeInTheDocument())
  })
  it('provides a real creation callback in the empty state', async () => {
    online.mockResolvedValue({ items: [] }); offline.mockResolvedValue({ items: [] })
    const create = vi.fn()
    open(<PatrolTaskInbox onNewPatrol={create} />)
    fireEvent.click(await screen.findByRole('button', { name: '创建首次巡检' }))
    expect(create).toHaveBeenCalledOnce()
  })
})
describe('巡检应用导航', () => {
  it('honors an explicit tasks view even when a batch is still selected', () => {
    expect(inspectionView(new URLSearchParams('patrol_batch_id=1')).key).toBe('online')
    expect(inspectionView(new URLSearchParams('offline_job_id=1')).key).toBe('offline')
    expect(inspectionView(new URLSearchParams('patrol_view=tasks&patrol_batch_id=1')).key).toBe('tasks')
  })
  it('changes views while preserving result links and supports the create action', () => {
    const create = vi.fn()
    open(<InspectionLayout onNewPatrol={create}><p>工作内容</p></InspectionLayout>, '/?patrol_batch_id=batch-1')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('在线巡检')
    fireEvent.click(screen.getByRole('button', { name: '全部任务' }))
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('巡检任务')
    expect(screen.getByTestId('location')).toHaveTextContent('patrol_batch_id=batch-1')
    fireEvent.click(screen.getByRole('button', { name: '新建巡检' }))
    expect(create).toHaveBeenCalledOnce()
    expect(screen.queryByRole('link', { name: /设备管理/ })).not.toBeInTheDocument()
  })
})
