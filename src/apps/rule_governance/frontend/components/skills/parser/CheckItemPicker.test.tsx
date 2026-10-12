import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ParserCheckItem } from '@netops/services/api'
import CheckItemPicker from './CheckItemPicker'

const item = (id: string, name: string, category: string, description: string, resultMode = 'structured') => ({
  check_item_id: id,
  check_item_version_id: `${id}:v1`,
  name,
  display_name: name,
  category,
  description,
  display_description: description,
  result_mode: resultMode,
}) as ParserCheckItem

const items = [
  item('interface.status', '接口状态', 'interface', '接口物理与协议状态'),
  item('routing.bgp_peers', 'BGP 邻居', 'routing', 'BGP 会话状态'),
  item('configuration.running', '运行配置变化', 'configuration', '对比两次巡检的运行配置', 'normalized_diff'),
]
const modeLabels = { structured: '结构化解析', normalized_diff: '规范化文本比较' }

describe('CheckItemPicker', () => {
  it('lists the whole catalog and narrows it as the engineer types', async () => {
    const onChange = vi.fn()
    render(<CheckItemPicker items={items} modeLabels={modeLabels} onChange={onChange} />)

    expect(screen.getByRole('radiogroup', { name: '关联目标检查项' })).toBeVisible()
    expect(screen.queryByText('routing')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /接口状态/ })).toBeInTheDocument()
    expect(screen.getByText('接口状态')).toBeVisible()
    expect(screen.getByText('对比两次巡检的运行配置')).toBeVisible()
    expect(screen.getByText('规范化文本比较')).toBeVisible()
    expect(screen.getByText('共 3 个检查项，点选其中一个')).toBeVisible()

    await userEvent.type(screen.getByRole('textbox', { name: '搜索检查项' }), 'BGP')
    expect(screen.queryByRole('radio', { name: /接口状态/ })).not.toBeInTheDocument()
    expect(screen.getByText('共 3 个检查项，匹配 1 个')).toBeVisible()
    await userEvent.click(screen.getByRole('radio', { name: /BGP 邻居/ }))
    expect(onChange).toHaveBeenCalledWith('routing.bgp_peers')
  })

  it('keeps the current choice named when the search hides its row', async () => {
    render(
      <CheckItemPicker items={items} value="configuration.running" modeLabels={modeLabels} onChange={vi.fn()} />,
    )

    expect(screen.getByRole('radio', { name: /运行配置变化/ })).toBeChecked()
    await userEvent.type(screen.getByRole('textbox', { name: '搜索检查项' }), '接口')
    expect(screen.queryByRole('radio', { name: /运行配置变化/ })).not.toBeInTheDocument()
    expect(screen.getByText('运行配置变化')).toBeVisible()
  })

  it('says what to do when nothing matches', async () => {
    render(<CheckItemPicker items={items} modeLabels={modeLabels} onChange={vi.fn()} />)

    await userEvent.type(screen.getByRole('textbox', { name: '搜索检查项' }), 'ospf')
    expect(screen.getByText('没有匹配的检查项，换一个关键词试试')).toBeVisible()
  })
})
