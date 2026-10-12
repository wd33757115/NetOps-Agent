import React from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { CheckItemReleaseTrial as Trial } from '@netops/services/api'
import CheckItemReleaseTrial from './CheckItemReleaseTrial'

const port = (index: number) => ({
  device_id: 'test-10.34.2.13',
  entity_key: JSON.stringify([`Ten-GigabitEthernet1/0/${index}`, 'aggregate']),
  field: 'transceiver_present',
  value: 'absent',
  severity: 'major',
  event_type: 'interface_optical_presence_down',
})

const trial = (overrides: Partial<Trial> = {}): Trial => ({
  available: true,
  run: { run_id: 'offline-1-current', observed_at: '2026-10-09T23:31:00+08:00', devices: 1, values: 253 },
  now: { abnormal: 11, undetermined: 44 },
  after: { abnormal: 51, undetermined: 0 },
  rules: [
    { field: 'transceiver_present', event_type: 'interface_optical_presence_down', text: '值为 absent 时判定异常（重要）。',
      change: 'added', now: 0, after: 40 },
    { field: 'rx_power_dbm', event_type: 'NumericHealthAbnormal', text: '超出设备阈值时判定异常。',
      change: 'same', now: 11, after: 11 },
  ],
  added_count: 40,
  added: Array.from({ length: 40 }, (_, index) => port(index + 1)),
  removed_count: 0,
  removed: [],
  no_data_fields: [],
  ...overrides,
})

describe('CheckItemReleaseTrial', () => {
  it('sets what the last patrol reports now beside what it would report after publishing', () => {
    render(<CheckItemReleaseTrial trial={trial()} />)

    const section = screen.getByRole('region', { name: '按上次巡检的数据试算' })
    expect(within(section).getByText(/1 台设备，253 个值/)).toBeVisible()
    expect(within(section).getByText('多报 40 项，少报 0 项')).toBeVisible()
    expect(section.querySelector('.release-trial__tile--attention')?.textContent).toContain('11 项，发布后 51 项')
    expect(section.textContent).toContain('44 项，发布后 0 项')

    const rules = within(section).getByRole('list', { name: '变化来自哪条判断' })
    expect(within(rules).getAllByRole('listitem')).toHaveLength(1)
    expect(within(rules).getByText('0 变为 40')).toBeVisible()
    expect(within(rules).getByText('新增')).toBeVisible()
    expect(within(section).getByText('其余 1 条判断的结果不变。')).toBeVisible()
    expect(within(section).getByText('两次巡检之间的变化事件不在这次试算里。')).toBeVisible()
  })

  it('lists the newly reported objects, a few first, and says what they have in common', async () => {
    render(<CheckItemReleaseTrial trial={trial()} />)

    expect(screen.getByText('全部是 transceiver_present = absent，都在 test-10.34.2.13 上。')).toBeVisible()
    const list = screen.getByRole('list', { name: '多报的 40 项' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    expect(within(list).getByText('Ten-GigabitEthernet1/0/1，aggregate')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: '查看全部 40 项' }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(40)
  })

  it('says so plainly when the last patrol would be judged the same', () => {
    render(<CheckItemReleaseTrial trial={trial({
      after: { abnormal: 11, undetermined: 44 },
      rules: [{ field: 'rx_power_dbm', event_type: 'NumericHealthAbnormal', text: '超出设备阈值时判定异常。',
        change: 'same', now: 11, after: 11 }],
      added_count: 0, added: [], removed_count: 0, removed: [],
    })} />)

    expect(screen.getByText('上次巡检报的异常一项不多、一项不少')).toBeVisible()
    expect(screen.queryByRole('list', { name: '变化来自哪条判断' })).not.toBeInTheDocument()
    expect(screen.queryByText(/多报的/)).not.toBeInTheDocument()
    expect(document.querySelector('.release-trial__tile--attention')).toBeNull()
  })

  it('lists what would no longer be reported and names the fields it could not try', () => {
    render(<CheckItemReleaseTrial trial={trial({
      now: { abnormal: 3, undetermined: 0 },
      after: { abnormal: 2, undetermined: 0 },
      rules: [{ field: 'fan_status', event_type: 'FanDown', text: '值为 failed 时判定异常（重要）。', change: 'removed', now: 1, after: 0 }],
      added_count: 0, added: [],
      removed_count: 1,
      removed: [{ device_id: 'core-1', entity_key: '["Fan2"]', field: 'fan_status', value: 'failed', severity: 'major', event_type: 'FanDown' }],
      no_data_fields: ['lane_status'],
    })} />)

    expect(within(screen.getByRole('list', { name: '少报的 1 项' })).getByText('Fan2')).toBeVisible()
    expect(screen.getByText('停用')).toBeVisible()
    expect(screen.getByText('上次巡检没有采集到 lane_status 的值，这部分无法试算。')).toBeVisible()
  })

  it.each([
    ['new_check_item', '这是新检查项，还没有巡检数据。发布后的第一次巡检才有结果。'],
    ['no_run', '还没有巡检采集过这个检查项的值，无法试算。'],
  ] as const)('explains why there is nothing to try (%s)', (reason, text) => {
    render(<CheckItemReleaseTrial trial={{ available: false, reason }} />)
    expect(screen.getByText(text)).toBeVisible()
  })

  it('reports a trial that failed without blocking the release', () => {
    render(<CheckItemReleaseTrial trial={{ available: false, reason: 'error', detail: 'boom' }} />)
    expect(screen.getByText('试算没有完成，不影响发布')).toBeVisible()
    expect(screen.getByText('boom')).toBeVisible()
  })
})
