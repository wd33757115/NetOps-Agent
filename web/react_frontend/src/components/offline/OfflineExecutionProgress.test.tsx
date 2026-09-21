import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OfflinePatrolJob } from '@netops/services/api'
import OfflineExecutionProgress from './OfflineExecutionProgress'

const renderProgress = (job: Partial<OfflinePatrolJob>) => render(React.createElement(
  OfflineExecutionProgress, { job: job as OfflinePatrolJob },
))

describe('离线固定任务进度', () => {
  it('恢复持久步骤和当前阶段，不等待 Workflow', () => {
    renderProgress({
      status: 'running', progress: { phase: 'change_detection', percent: 65 },
      result: { steps: { current_ingest: { success: true }, current_normalization: { success: true } } },
    })
    expect(screen.getByText('检测变化')).toBeInTheDocument()
    expect(screen.getByText('归一化当前状态')).toBeInTheDocument()
    expect(screen.getAllByText('完成')).toHaveLength(2)
    expect(screen.queryByText(/Workflow/)).not.toBeInTheDocument()
  })

  it('显示报告保存失败及已完成的分析', () => {
    renderProgress({
      status: 'failed', error_message: '报告保存失败，分析结果已保留',
      progress: { phase: 'failed', percent: 100, failed_phase: 'report_storage' },
      result: { steps: { event_building: { success: true } } },
    })
    expect(screen.getByText('报告保存失败，分析结果已保留')).toBeInTheDocument()
    expect(screen.getByText('失败阶段：保存报告')).toBeInTheDocument()
    expect(screen.getByText('构建事件')).toBeInTheDocument()
  })

  it('取消后说明需要等待当前阶段退出', () => {
    renderProgress({
      status: 'cancelled', progress: { phase: 'cancelled', percent: 100 },
    })
    expect(screen.getByText(/当前阶段退出后可以重试/)).toBeInTheDocument()
  })
})
