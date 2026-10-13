import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { parserApi, type RuleGenerationGuide } from '@netops/services/api'
import RuleGuideDialog from './RuleGuideDialog'

const guide: RuleGenerationGuide = {
  rows: [
    { key: 'evidence', kinds: '任意', use: '仅展示', needs: '', change_rule: '', event_rules: '', effect: '只进报告。' },
    { key: 'health_state', kinds: '状态、枚举、布尔', use: '健康判断', needs: '正常状态和异常状态',
      change_rule: '', event_rules: '1 条故障（重要）', effect: '当前值属于异常状态就计入当前异常。' },
  ],
  fixed: ['级别是固定的：故障事件一律“重要”。'],
  timing: ['发布时：规则随检查项版本一起生效。'],
}

describe('RuleGuideDialog', () => {
  it('lays out how settings become rules, what cannot be set, and when rules take effect', async () => {
    vi.spyOn(parserApi, 'ruleGenerationGuide').mockResolvedValue(guide)
    render(<RuleGuideDialog open onClose={vi.fn()} />)

    // antd keeps a dialog's content hidden in jsdom while it animates in.
    expect(await screen.findByText('当前值属于异常状态就计入当前异常。')).toBeInTheDocument()
    expect(screen.getByText('1 条故障（重要）')).toBeInTheDocument()
    expect(screen.getByText('级别是固定的：故障事件一律“重要”。')).toBeInTheDocument()
    expect(screen.getByText('发布时：规则随检查项版本一起生效。')).toBeInTheDocument()
    expect(document.querySelector('tr.rule-guide__row--current')).toBeNull()
  })

  it('marks the row the field being edited falls in', async () => {
    vi.spyOn(parserApi, 'ruleGenerationGuide').mockResolvedValue(guide)
    render(<RuleGuideDialog open current={{ row: 'health_state', field: 'fan_status' }} onClose={vi.fn()} />)

    expect(await screen.findByText('标出的一行是字段 fan_status 现在的设置。')).toBeInTheDocument()
    expect(document.querySelector('tr.rule-guide__row--current')?.textContent).toContain('健康判断')
  })

  it('offers another try when the table cannot be loaded', async () => {
    const load = vi.spyOn(parserApi, 'ruleGenerationGuide').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(guide)
    render(<RuleGuideDialog open onClose={vi.fn()} />)

    expect(await screen.findByText('对照表加载失败')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
    expect(await screen.findByText('只进报告。')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(2)
  })
})
