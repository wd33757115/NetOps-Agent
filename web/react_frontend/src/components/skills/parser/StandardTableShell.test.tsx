import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StandardTableShell from './StandardTableShell'

interface Row {
  id: string
  name: string
}

describe('StandardTableShell', () => {
  it('renders the supplied empty state', () => {
    render(
      <StandardTableShell<Row>
        rowKey="id"
        dataSource={[]}
        columns={[{ title: '名称', dataIndex: 'name' }]}
        emptyText="暂无治理记录"
      />,
    )

    expect(screen.getByText('暂无治理记录')).toBeInTheDocument()
  })

  it('exposes a recoverable error state', () => {
    const retry = vi.fn()
    render(
      <StandardTableShell<Row>
        rowKey="id"
        dataSource={[]}
        columns={[{ title: '名称', dataIndex: 'name' }]}
        emptyText="暂无数据"
        error={new Error('service unavailable')}
        errorTitle="治理数据加载失败"
        onRetry={retry}
      />,
    )

    expect(screen.getByText('治理数据加载失败')).toBeInTheDocument()
    expect(screen.getByText('service unavailable')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
