import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ParserWorkItem } from '../../../services/api'
import ParserWorkQueueWorkspace from './ParserWorkQueueWorkspace'

const item: ParserWorkItem = {
  id: 'parser-job-1',
  task_type: 'parser',
  status: 'validation_failed',
  priority: 'high',
  title: 'Cisco show interface status',
  scope: '12 台设备',
  blocker: '关键字段覆盖不足',
  next_action: '修订并重新验证模板',
  age_seconds: 7200,
  target_workspace: 'generation',
  target_subtab: 'jobs',
}

const renderWorkspace = (isMobile: boolean, onOpen = vi.fn()) => {
  render(
    <ParserWorkQueueWorkspace
      isMobile={isMobile}
      items={[item]}
      loading={false}
      total={1}
      page={1}
      pageSize={20}
      search=""
      onSearchChange={vi.fn()}
      onTaskTypeChange={vi.fn()}
      onStatusChange={vi.fn()}
      onPageChange={vi.fn()}
      onOpen={onOpen}
    />,
  )
  return onOpen
}

describe('ParserWorkQueueWorkspace', () => {
  it('renders a task-oriented mobile summary without the desktop table', () => {
    const onOpen = renderWorkspace(true)

    expect(screen.getByText('Cisco show interface status')).toBeVisible()
    expect(screen.getByText('关键字段覆盖不足')).toBeVisible()
    expect(screen.getByText('下一步：修订并重新验证模板')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /处理任务/ }))
    expect(onOpen).toHaveBeenCalledWith(item)
  })

  it('keeps the operational table for desktop', () => {
    renderWorkspace(false)

    expect(screen.getByRole('table')).toBeVisible()
    expect(screen.getAllByText('影响范围').length).toBeGreaterThan(0)
  })
})
