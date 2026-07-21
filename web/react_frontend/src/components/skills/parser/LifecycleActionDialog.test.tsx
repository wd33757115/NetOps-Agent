import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import LifecycleActionDialog from './LifecycleActionDialog'

describe('LifecycleActionDialog', () => {
  it('requires an operational reason before confirming a production change', () => {
    const onConfirm = vi.fn()
    render(
      <LifecycleActionDialog
        open
        title="关闭 Parser 自动调度"
        resourceName="Parser 自动调度"
        impact="关闭后新任务不会自动提交。"
        actionLabel="确认关闭"
        loading={false}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )

    const confirm = screen.getByRole('button', { name: '确认关闭' })
    expect(confirm).toBeDisabled()

    fireEvent.change(
      screen.getByPlaceholderText('说明变更目的、依据或回退原因（至少 5 个字符）'),
      { target: { value: '例行' } },
    )
    expect(confirm).toBeDisabled()

    fireEvent.change(
      screen.getByPlaceholderText('说明变更目的、依据或回退原因（至少 5 个字符）'),
      { target: { value: '维护窗口暂停自动调度' } },
    )
    fireEvent.click(confirm)

    expect(onConfirm).toHaveBeenCalledWith('维护窗口暂停自动调度')
  })
})
