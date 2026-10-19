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

  it('names the adapters a rule package brought and what their templates were replayed on', () => {
    render(<CheckItemReleasePreview preview={preview({
      release: {
        rules_activated: 0, rules_retired_without_successor: 0, adapters_repinned: 0, adapters_brought_in: 2,
        runtime_bundle_version_ids: ['bundle-1', 'bundle-2'],
        adapters: [
          { implementation_id: 'msr', name: 'MSR5620 · display startup', repinned: false, in_force: true, brought_in: true,
            templates: [{ template_id: 't1', replay: { passed: true, samples: 0, outputs: 3, outputs_parsed: 3, records_differing: 1 } }] },
          { implementation_id: 's6850', name: 'S6850-56HF · display startup', repinned: false, in_force: true, brought_in: true,
            templates: [{ template_id: 't2', replay: { passed: true, samples: 2, samples_passed: 2, outputs: 0 } }] },
          { implementation_id: 'n9k', name: 'N9K-C9364C', repinned: true, in_force: true },
        ],
      },
    })} />)

    expect(screen.getByText('随新版本一起生效的导入适配器（解析模板已回放通过）：')).toBeVisible()
    expect(screen.getByText('：本地 3 份巡检输出都能解析；与现用模板相比有 1 条记录的值不同')).toBeVisible()
    expect(screen.getByText('：包内 2 份样本与导出时一致')).toBeVisible()
    // An adapter already in force is still reported the usual way, and not as imported.
    expect(screen.getByText('随新版本切换：N9K-C9364C。')).toBeVisible()
  })

  it('counts imported adapters in the summary, and says when the platform will never read their template', () => {
    render(<CheckItemReleasePreview preview={preview({
      release: {
        rules_activated: 2, rules_retired_without_successor: 0, adapters_repinned: 0, adapters_brought_in: 1,
        runtime_bundle_version_ids: ['bundle-1'],
        adapters: [
          { implementation_id: 'log', name: 'S6520XP · display logbuffer level 1', repinned: false, brought_in: true,
            text_only_commands: ['display logbuffer level 1'],
            text_only_warning: '命令 display logbuffer level 1 在本平台只比较原文、不做解析，导入的解析模板不会被巡检使用',
            templates: [{ template_id: 't1', replay: { passed: true, samples: 1, samples_passed: 1, outputs: 0 } }] },
        ],
      },
    })} />)

    expect(screen.getByText(/1 个导入的适配器随之生效；发布前不影响正在运行的巡检。$/)).toBeVisible()
    expect(screen.queryByText(/没有正在使用的适配器需要切换/)).not.toBeInTheDocument()
    // The wording is the server's, shown as given.
    expect(screen.getByText('命令 display logbuffer level 1 在本平台只比较原文、不做解析，导入的解析模板不会被巡检使用。')).toBeVisible()
  })
})
