import React from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { describe, expect, it, vi } from 'vitest'

import { parserApi, type CheckItemRelations } from '@netops/services/api'
import CheckItemRelationGraph from './CheckItemRelationGraph'

type Row = CheckItemRelations['rows'][number]

const presence: Row = {
  field: { name: 'transceiver_present', description: '光模块是否在位', observation_type: 'interface.optical.presence' },
  rule: { kind: 'change', title: '变化规则', detail: '状态转换', lines: ['变化比较方式：状态转换。'], state: 'active' },
  outcomes: [
    { key: 'event:TransceiverAbsent', kind: 'event', event_type: 'TransceiverAbsent', event_class: 'fault', severity: 'major',
      judges_current: true, resolves: [], text: '从 present 变为 absent 时产生故障事件（重要）。', state: 'active', open: 3, total: 5 },
    { key: 'event:TransceiverRestored', kind: 'event', event_type: 'TransceiverRestored', event_class: 'recovery', severity: 'info',
      judges_current: false, resolves: ['TransceiverAbsent'], text: '从 absent 恢复为 present 时产生恢复事件。', state: 'active', open: 0, total: 2 },
  ],
}
const power: Row = {
  field: { name: 'rx_power_dbm', description: '', observation_type: 'interface.optical.rx_power' },
  rule: { kind: 'health', title: '健康判断', detail: '按设备阈值', lines: ['低于设备输出的 rx_low_alarm_dbm 时判定异常。'], state: 'active' },
  outcomes: [{ key: 'abnormal', kind: 'abnormal', text: '超出阈值时计入当前异常' }],
}

const current = (overrides: Partial<CheckItemRelations> = {}): CheckItemRelations => ({
  mode: 'current',
  patrolled: true,
  rows: [presence, power],
  reference: ['rx_low_alarm_dbm', 'rx_high_alarm_dbm'],
  evidence: ['serial_number'],
  retired: [],
  errors: [],
  ...overrides,
})

const renderGraph = (graph: CheckItemRelations, props: Partial<React.ComponentProps<typeof CheckItemRelationGraph>> = {}) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <CheckItemRelationGraph checkItemId="interface.optical_power" graph={graph} {...props} />
  </QueryClientProvider>,
)

describe('CheckItemRelationGraph', () => {
  it('leads each judged field to its rule and on to the events and results that follow', () => {
    renderGraph(current())

    const row = screen.getByRole('listitem', { name: 'transceiver_present：变化规则，状态转换' })
    expect(within(row).getByText('生效')).toBeVisible()
    expect(within(row).getByText('TransceiverAbsent')).toBeVisible()
    expect(within(row).getByText('故障事件，重要，也计入当前异常')).toBeVisible()
    expect(within(row).getByText('恢复事件，关闭 TransceiverAbsent')).toBeVisible()
    expect(within(row).getByRole('button', { name: 'TransceiverAbsent 未恢复 3 条，查看' })).toBeVisible()

    const health = screen.getByRole('listitem', { name: 'rx_power_dbm：健康判断，按设备阈值' })
    expect(within(health).getByText('超出阈值时计入当前异常')).toBeVisible()
  })

  it('keeps the fields that are only shown or used as thresholds in two quiet rows', () => {
    renderGraph(current())

    const reference = screen.getByRole('listitem', { name: '阈值参考 2 个字段' })
    expect(within(reference).getByText('供 rx_power_dbm 判断时作阈值，本身不产生规则。')).toBeVisible()
    const evidence = screen.getByRole('listitem', { name: '仅展示 1 个字段' })
    expect(within(evidence).getByText('只进报告，不判断，不产生规则和事件。')).toBeVisible()
  })

  it('says a rule is not in force', () => {
    renderGraph(current({ rows: [{ ...presence, rule: { ...presence.rule, state: 'missing' } }] }))
    expect(screen.getByText('未生效')).toBeVisible()
  })

  it('lists the stored events behind a count', async () => {
    vi.spyOn(parserApi, 'checkItemEvents').mockResolvedValue({
      event_type: 'TransceiverAbsent',
      total: 3,
      items: [{
        id: 'event-1', device_id: 'XA-CORE-01', entity_key: '["Ethernet1/7"]', status: 'open', severity: 'major',
        last_seen_at: '2026-10-11T08:00:00+00:00', occurrence_count: 2, old: 'present', new: 'absent',
      }],
    })
    renderGraph(current())

    await userEvent.click(screen.getByRole('button', { name: 'TransceiverAbsent 未恢复 3 条，查看' }))
    // antd keeps a popover's content hidden in jsdom while it animates in.
    expect(await screen.findByText('XA-CORE-01')).toBeInTheDocument()
    expect(screen.getByText('Ethernet1/7')).toBeInTheDocument()
    expect(screen.getByText(/present 变为 absent，共 2 次/)).toBeInTheDocument()
    expect(parserApi.checkItemEvents).toHaveBeenCalledWith('interface.optical_power', 'TransceiverAbsent')
  })

  it('sets apart the events of rules that no longer run, with what to do about them', () => {
    renderGraph(current({
      retired: [{ event_type: 'interface.optical_power.vendor_note.changed', event_class: 'change', severity: 'info',
        field: 'vendor_note', open: 12, total: 12 }],
    }), { retiredAction: <button type="button">关闭这 12 条事件</button> })

    expect(screen.getByText('已停用的规则留下的事件')).toBeVisible()
    const row = screen.getByRole('listitem', { name: 'vendor_note：规则已停用' })
    expect(within(row).getByText('vendor_note.changed')).toBeVisible()
    expect(within(row).getByRole('button', { name: 'vendor_note.changed 未关闭 12 条，查看' })).toBeVisible()
    expect(screen.getByRole('button', { name: '关闭这 12 条事件' })).toBeVisible()
  })

  it('compares two versions and leaves out what does not change', () => {
    renderGraph({
      mode: 'diff',
      rows: [
        { ...presence, field: { ...presence.field, change: 'same' }, rule: { ...presence.rule, change: 'same' },
          outcomes: presence.outcomes.map((outcome) => ({ ...outcome, change: 'same' as const })) },
        {
          field: { name: 'lane_status', description: '', observation_type: 'interface.optical.lane_status', change: 'added' },
          rule: { kind: 'change', title: '变化规则', detail: '精确比较', lines: [], change: 'added' },
          outcomes: [{ key: 'event:lane', kind: 'event', event_type: 'interface.optical_power.lane_status.changed',
            event_class: 'change', severity: 'info', text: '与上次巡检相比有变化时产生提示事件。', change: 'added', open: 0, total: 0 }],
        },
        {
          field: { name: 'vendor_note', description: '', observation_type: 'interface.optical.vendor_note', change: 'same' },
          rule: { kind: 'change', title: '变化规则', detail: '精确比较', lines: [], change: 'removed' },
          outcomes: [{ key: 'event:note', kind: 'event', event_type: 'interface.optical_power.vendor_note.changed',
            event_class: 'change', severity: 'info', text: '与上次巡检相比有变化时产生提示事件。', change: 'removed', open: 12, total: 12 }],
        },
      ],
      reference: { fields: [], added: [], removed: [] },
      evidence: { fields: ['serial_number', 'vendor_note'], added: [], removed: [] },
      errors: [],
    }, { onlyChanges: true })

    expect(screen.queryByRole('listitem', { name: /transceiver_present/ })).not.toBeInTheDocument()
    expect(screen.getByText('其余 1 个字段的规则和事件不变。')).toBeVisible()
    const added = screen.getByRole('listitem', { name: 'lane_status：变化规则，精确比较' })
    expect(within(added).getAllByText('新增')).toHaveLength(3)
    const removed = screen.getByRole('listitem', { name: 'vendor_note：变化规则，精确比较' })
    expect(within(removed).getAllByText('停用')).toHaveLength(2)
    expect(within(removed).getByText('发布时关闭 12 条')).toBeVisible()
    expect(screen.queryByRole('listitem', { name: /仅展示/ })).not.toBeInTheDocument()
  })

  it('draws nothing when two versions judge the same', () => {
    const { container } = renderGraph({
      mode: 'diff',
      rows: [{ ...presence, field: { ...presence.field, change: 'same' }, rule: { ...presence.rule, change: 'same' },
        outcomes: presence.outcomes.map((outcome) => ({ ...outcome, change: 'same' as const })) }],
      reference: { fields: [], added: [], removed: [] },
      evidence: { fields: [], added: [], removed: [] },
      errors: [],
    }, { onlyChanges: true })
    expect(container).toBeEmptyDOMElement()
  })
})
