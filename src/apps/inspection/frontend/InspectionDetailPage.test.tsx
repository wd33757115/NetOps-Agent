import React, { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import InspectionDetailPage, { InspectionPageHost } from './InspectionDetailPage'

function Workspace() {
  const [open, setOpen] = useState(false)
  const [evidence, setEvidence] = useState(false)
  const [view, setView] = useState('offline')
  return <div className="inspection-app">
    <button onClick={() => setView('risks')}>风险导航</button>
    <InspectionPageHost view={view}>
      <input aria-label="任务筛选" defaultValue="核心交换机" />
      <button onClick={() => setOpen(true)}>打开任务</button>
      <InspectionDetailPage open={open} title="离线任务" onClose={() => setOpen(false)} backLabel="返回任务列表">
        <input aria-label="结果筛选" defaultValue="接口" />
        <button onClick={() => setEvidence(true)}>查看原文</button>
        <InspectionDetailPage open={evidence} title="CLI 原文" onClose={() => setEvidence(false)}>
          <pre>display interface</pre>
        </InspectionDetailPage>
      </InspectionDetailPage>
    </InspectionPageHost>
  </div>
}

describe('巡检完整详情页', () => {
  it('preserves both list and result filters through nested evidence pages', () => {
    render(<Workspace />)
    fireEvent.change(screen.getByRole('textbox', { name: '任务筛选' }), { target: { value: '交换机 A' } })
    fireEvent.click(screen.getByRole('button', { name: '打开任务' }))
    expect(screen.getByRole('heading', { name: '离线任务' })).toHaveFocus()
    expect(screen.queryByRole('textbox', { name: '任务筛选' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '结果筛选' }), { target: { value: 'Ethernet1/1' } })
    fireEvent.click(screen.getByRole('button', { name: '查看原文' }))
    expect(screen.getByRole('heading', { name: 'CLI 原文' })).toHaveFocus()
    expect(screen.queryByRole('heading', { name: '离线任务' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回上一页' }))
    expect(screen.getByRole('textbox', { name: '结果筛选' })).toHaveValue('Ethernet1/1')
    fireEvent.click(screen.getByRole('button', { name: '返回任务列表' }))
    expect(screen.getByRole('textbox', { name: '任务筛选' })).toHaveValue('交换机 A')
  })

  it('dismisses nested details when navigating to a different workspace', () => {
    render(<Workspace />)
    fireEvent.click(screen.getByRole('button', { name: '打开任务' }))
    fireEvent.click(screen.getByRole('button', { name: '查看原文' }))
    fireEvent.click(screen.getByRole('button', { name: '风险导航' }))
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '任务筛选' })).toBeVisible()
  })
})
