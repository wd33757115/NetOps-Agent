import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { CheckItemReleasePreview as ReleasePreview } from '@netops/services/api'
import CheckItemReleasePreview from './CheckItemReleasePreview'

const preview = (overrides: Partial<ReleasePreview>): ReleasePreview => ({
  applicable: true,
  check_item_id: 'interface.optical_power',
  from_version: 18,
  to_version: 19,
  releasable: true,
  blockers: [],
  fields: [
    { canonical_field: 'tx_power_dbm', change: 'changed', before: [], after: ['低于设备输出的 tx_low_alarm_dbm 时判定异常。'] },
    { canonical_field: 'lane_status', change: 'added', before: [], after: ['与上次巡检相比有变化时产生提示事件。'] },
    { canonical_field: 'vendor_note', change: 'removed', before: ['与上次巡检相比有变化时产生提示事件。'], after: [] },
    { canonical_field: 'rx_power_dbm', change: 'unchanged', before: ['低于阈值时判定异常。'], after: ['低于阈值时判定异常。'] },
  ],
  release: {
    rules_activated: 4,
    rules_retired_without_successor: 1,
    adapters_repinned: 2,
    runtime_bundle_version_ids: ['bundle-1'],
    adapters: [
      { implementation_id: 'n9k', name: 'N9K-C9364C', repinned: true, in_force: true, dropped_fields: ['vendor_note'] },
      { implementation_id: 'generic', name: 'Generic optics', repinned: true, in_force: false },
    ],
  },
  ...overrides,
})

describe('CheckItemReleasePreview', () => {
  it('lists only the fields whose judgement changes, as now against after publishing', () => {
    render(<CheckItemReleasePreview preview={preview({})} />)

    expect(screen.getByText('可以整体切换')).toBeVisible()
    expect(screen.getByText('4 条规则随新版本生效；1 个适配器和运行包一起切换；发布前不影响正在运行的巡检。')).toBeVisible()
    expect(screen.getByText('tx_power_dbm')).toBeVisible()
    expect(screen.getByText('低于设备输出的 tx_low_alarm_dbm 时判定异常。')).toBeVisible()
    expect(screen.getByText('没有这个字段')).toBeVisible()
    expect(screen.getByText('字段删除，规则停用')).toBeVisible()
    expect(screen.queryByText('rx_power_dbm')).not.toBeInTheDocument()
    expect(screen.getByText('其余 1 个字段的判断不变。')).toBeVisible()
    expect(screen.getByText('随新版本切换：N9K-C9364C。另有 1 个适配器原本就未生效，保持不变。')).toBeVisible()
    expect(screen.getByText('已删除的字段 vendor_note 会自动从适配器的输出中去掉，不需要改适配器。')).toBeVisible()
  })

  it('says why the version cannot take over and that the current one keeps running', () => {
    render(<CheckItemReleasePreview preview={preview({
      releasable: false,
      release: null,
      blockers: ['适配器“MSR5620 · display startup”与新版本的字段不匹配：同名 Value CURRENT_FILE 不允许改名或转换'],
    })} />)

    expect(screen.getByText('现在不能发布，当前版本继续巡检')).toBeVisible()
    expect(screen.getByText(/MSR5620 · display startup/)).toBeVisible()
    expect(screen.queryByText('可以整体切换')).not.toBeInTheDocument()
  })

  it('states plainly when nothing the patrol judges changes', () => {
    render(<CheckItemReleasePreview preview={preview({
      fields: [{ canonical_field: 'rx_power_dbm', change: 'unchanged', before: ['a'], after: ['a'] }],
    })} />)

    expect(screen.getByText('巡检判断不变。')).toBeVisible()
    expect(screen.getByText(/4 条规则内容不变，随新版本重新生效/)).toBeVisible()
  })

  it('renders nothing for a change that is not a check item release', () => {
    const { container } = render(
      <CheckItemReleasePreview preview={{ applicable: false, releasable: false, blockers: [], fields: [], release: null }} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe('CheckItemReleasePreview events', () => {
  it('says which events close with the release, and that the rest stay as they are', () => {
    const { rerender } = render(<CheckItemReleasePreview preview={preview({})} />)
    expect(screen.getByText('没有事件需要关闭，现有事件保持原样。')).toBeVisible()

    const base = preview({})
    rerender(<CheckItemReleasePreview preview={preview({
      release: {
        ...base.release!,
        events_closed: [{ event_type: 'interface.optical_power.vendor_note.changed', event_class: 'change', severity: 'info', count: 12 }],
      },
    })} />)
    expect(screen.getByText(
      '发布时关闭 12 条事件，因为产生它们的规则停用了（interface.optical_power.vendor_note.changed 12 条）。',
    )).toBeVisible()
  })
})
