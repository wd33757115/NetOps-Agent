import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { describe, expect, it, vi } from 'vitest'

import {
  parserApi,
  type CheckItemOverview,
  type CheckItemPatrolStatus,
  type ParserCheckItem,
} from '@netops/services/api'
import CheckItemOverviewDrawer, { CheckItemPatrolStatusLabel } from './CheckItemOverviewDrawer'

const status = (overrides: Partial<CheckItemPatrolStatus>): CheckItemPatrolStatus => ({
  state: 'ready',
  judged_fields: 2,
  rules_wanted: 3,
  rules_active: 3,
  rules_awaiting_approval: false,
  adapters: 9,
  adapters_ready: 9,
  pending_change: null,
  blockers: [],
  ...overrides,
})

const checkItem = {
  check_item_id: 'interface.optical_power',
  check_item_version_id: 'version-18',
  version: 18,
  name: 'Interface optical power',
  display_name: '光功率',
} as ParserCheckItem

const overview: CheckItemOverview = {
  check_item_id: 'interface.optical_power',
  name: 'Interface optical power',
  version: 18,
  check_item_version_id: 'version-18',
  result_mode: 'structured',
  status: status({ state: 'adapters_pending', adapters_ready: 7 }),
  fields: [
    { canonical_field: 'rx_power_dbm', description: '接收光功率', use: 'health', use_label: '健康判断',
      lines: ['低于设备输出的 rx_low_alarm_dbm 时判定异常。'], rules_missing: false, stale_count: 0, provided_by: 7 },
    { canonical_field: 'lane_status', description: '', use: 'change', use_label: '变化检测',
      lines: [], rules_missing: true, stale_count: 0, provided_by: 5 },
    { canonical_field: 'serial_number', description: '', use: 'evidence', use_label: '仅展示',
      lines: [], rules_missing: false, stale_count: 0, provided_by: 2 },
  ],
  structured_adapters: 7,
  adapters: [
    { implementation_id: 'n9k', implementation_version_id: 'n9k:v5', name: 'N9K-C9364C', scope: 'Cisco / N9K-C9364C',
      command: 'show interface transceiver detail', state: 'ready', running_check_item_version: null,
      fields_provided: 2, missing_fields: ['lane_status'] },
    { implementation_id: 'h3c', implementation_version_id: 'h3c:v3', name: 'H3C optical diagnostics', scope: 'H3C',
      command: 'display transceiver diagnosis interface', state: 'previous_version', running_check_item_version: 16,
      fields_provided: null, missing_fields: null },
  ],
  leftover_count: 0,
}

const renderDrawer = (onEdit = vi.fn(), onDelete = vi.fn(), onRollback = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CheckItemOverviewDrawer open checkItem={checkItem} onClose={vi.fn()} onEdit={onEdit} onDelete={onDelete}
        onRollback={onRollback} />
    </QueryClientProvider>,
  )
  return onEdit
}

const graph: NonNullable<CheckItemOverview['graph']> = {
  mode: 'current',
  patrolled: true,
  rows: [{
    field: { name: 'rx_power_dbm', description: '接收光功率', observation_type: 'interface.optical.rx_power' },
    rule: { kind: 'health', title: '健康判断', detail: '按设备阈值', lines: [], state: 'active' },
    outcomes: [{ key: 'abnormal', kind: 'abnormal', text: '超出阈值时计入当前异常' }],
  }],
  reference: [],
  evidence: ['serial_number'],
  retired: [{ event_type: 'interface.optical_power.lane_status.changed', event_class: 'change', severity: 'info',
    field: 'lane_status', open: 12, total: 12 }],
  errors: [],
}

describe('CheckItemPatrolStatusLabel', () => {
  it.each([
    [status({}), '生效', '3 条规则 · 9 个适配器'],
    [status({ rules_wanted: 0, rules_active: 0 }), '生效', '按设备阈值判断 · 9 个适配器'],
    [status({ state: 'display_only', judged_fields: 0, rules_wanted: 0, rules_active: 0 }), '仅展示', '不产生规则 · 9 个适配器'],
    [status({ state: 'adapters_pending', adapters_ready: 7 }), '2 个适配器未生效', '其余 7 个已生效'],
    [status({ state: 'rules_missing', rules_active: 1 }), '规则未生效', '1/3 条规则生效'],
    [status({ state: 'rules_missing', rules_active: 0, rules_awaiting_approval: true }), '规则待审批', '0/3 条规则生效'],
    [status({ state: 'topology' }), '拓扑生成', '不参与巡检'],
    [status({ state: 'no_adapter', adapters: 0, adapters_ready: 0 }), '未适配设备', '还没有命令适配器'],
  ])('names the state of the chain', (value, label, detail) => {
    render(<CheckItemPatrolStatusLabel status={value} />)
    expect(screen.getByText(label)).toBeVisible()
    expect(screen.getByText(detail)).toBeVisible()
  })

  it('adds the unpublished change beside the state in force', () => {
    render(<CheckItemPatrolStatusLabel status={status({ pending_change: { id: 'change-1', status: 'draft' } })} />)
    expect(screen.getByText('生效')).toBeVisible()
    expect(screen.getByText('有待发布的修改')).toBeVisible()
  })
})

describe('CheckItemOverviewDrawer', () => {
  it('puts each field beside what the patrol does with it and which adapters provide it', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue(overview)
    renderDrawer()

    expect(await screen.findByText('低于设备输出的 rx_low_alarm_dbm 时判定异常。')).toBeVisible()
    expect(parserApi.checkItemOverview).toHaveBeenCalledWith('interface.optical_power')
    expect(screen.getByText('定义了判断，但规则还没有生效，巡检不会执行。')).toBeVisible()
    expect(screen.getByText('只进报告，不判断是否正常。')).toBeVisible()
    expect(screen.getByText('7 / 7')).toBeVisible()
    expect(screen.getByText('5 / 7')).toBeVisible()
    expect(screen.getByText('2 个适配器没有生效')).toBeVisible()
    expect(screen.getByText('不提供：lane_status')).toBeVisible()
    expect(screen.getByText('仍在 v16')).toBeVisible()
    expect(screen.getByText('不按字段解析')).toBeVisible()
  })

  it('hands the check item to the editor', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue(overview)
    const onEdit = renderDrawer()

    await userEvent.click(await screen.findByRole('button', { name: /修\s*改/ }))
    expect(onEdit).toHaveBeenCalledWith(checkItem)
  })
})

describe('CheckItemOverviewDrawer relations', () => {
  it('opens with the picture of fields, rules and events, and says what a change or a delete does to them', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue({ ...overview, graph })
    renderDrawer()

    expect(await screen.findByText('字段、规则与事件')).toBeVisible()
    expect(screen.getByRole('listitem', { name: 'rx_power_dbm：健康判断，按设备阈值' })).toBeVisible()
    expect(screen.getByText('修改后发布')).toBeVisible()
    expect(screen.getByText(/停用的规则留下的事件在发布时关闭/)).toBeVisible()
    expect(screen.getByText(/历史报告、各个版本和审计记录保留/)).toBeVisible()
  })

  it('closes the events left by rules that no longer run when the engineer asks', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue({ ...overview, graph })
    const close = vi.spyOn(parserApi, 'closeRetiredRuleEvents').mockResolvedValue({
      events_closed: [{ event_type: 'interface.optical_power.lane_status.changed', event_class: 'change', severity: 'info', count: 12 }],
    })
    renderDrawer()

    await userEvent.click(await screen.findByRole('button', { name: '关闭这 12 条事件' }))
    expect(close).toHaveBeenCalledWith('interface.optical_power')
  })

  it('hands the check item to the delete dialog', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue({ ...overview, graph })
    const onDelete = vi.fn()
    renderDrawer(vi.fn(), onDelete)

    await userEvent.click(await screen.findByRole('button', { name: /删\s*除/ }))
    expect(onDelete).toHaveBeenCalledWith(checkItem)
  })
})

describe('CheckItemOverviewDrawer rollback', () => {
  it('offers the way back to the previous release and hands it to the dialog', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue({ ...overview, graph, rollback_version: 17 })
    const onRollback = vi.fn()
    renderDrawer(vi.fn(), vi.fn(), onRollback)

    await userEvent.click(await screen.findByRole('button', { name: '回退到 v17' }))
    expect(onRollback).toHaveBeenCalledWith(checkItem, 17)
    expect(screen.getByText(/把上一个发布过的版本的内容作为新版本再发布一次/)).toBeVisible()
  })

  it('offers nothing to go back to for a check item released only once', async () => {
    vi.spyOn(parserApi, 'checkItemOverview').mockResolvedValue({ ...overview, graph, rollback_version: null })
    renderDrawer()

    await screen.findByText('字段、规则与事件')
    expect(screen.queryByRole('button', { name: /回退到/ })).not.toBeInTheDocument()
  })
})
