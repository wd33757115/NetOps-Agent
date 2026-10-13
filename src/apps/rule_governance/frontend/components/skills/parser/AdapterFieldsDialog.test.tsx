import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parserApi, type AdapterFieldsState, type AdapterFieldTrial } from '@netops/services/api'
import AdapterFieldsDialog from './AdapterFieldsDialog'

const target = { implementationId: 'n9k-show-inventory', name: 'N9K · show inventory', checkItemId: 'device.inventory' }

const state = (overrides: Partial<AdapterFieldsState> = {}): AdapterFieldsState => ({
  implementation_id: target.implementationId,
  implementation_name: target.name,
  check_item_id: target.checkItemId,
  status: 'idle',
  requested_fields: [],
  sample_count: 9,
  field_count: 7,
  provided_count: 4,
  missing_fields: [
    { name: 'name', description: '组件名称原文', use: 'evidence', absent: false },
    { name: 'product_id', description: '厂商产品编号', use: 'change', absent: false },
    { name: 'vendor_name', description: '', use: 'evidence', absent: false },
  ],
  result: null,
  saved: null,
  error: null,
  ...overrides,
})

const trial = (overrides: Partial<AdapterFieldTrial> = {}): AdapterFieldTrial => ({
  status: 'ready',
  requested_fields: ['name', 'product_id', 'vendor_name'],
  sample_count: 9,
  record_count: 72,
  model_calls: 2,
  existing_field_count: 6,
  rules_in_force: 10,
  rules_added: 2,
  added_lines: ['Value name ([^"]+)', '^NAME:\\s+"${name}", -> Continue'],
  fields: [
    { name: 'name', description: '组件名称原文', records: 72, samples: 9, examples: ['Chassis', 'Slot 1'],
      sources: [{ value: 'Chassis', line: 'NAME: "Chassis",  DESCR: "Nexus9000 Chassis"', above: '' }] },
    { name: 'product_id', description: '厂商产品编号', records: 72, samples: 9, examples: ['N9K-C93180YC-FX'],
      sources: [{ value: 'N9K-C93180YC-FX', line: 'PID: N9K-C93180YC-FX     ,  VID: V03', above: 'NAME: "Chassis"' }] },
    { name: 'vendor_name', description: '', records: 0, samples: 0, examples: [], sources: [] },
  ],
  provided_fields: ['name', 'product_id'],
  absent_fields: ['vendor_name'],
  regression: { passed: true, differences: [] },
  stale: false,
  ...overrides,
})

const ready = () => state({
  status: 'ready', requested_fields: ['name', 'product_id', 'vendor_name'], result: trial(),
  missing_fields: state().missing_fields.map((field) => ({ ...field, absent: field.name === 'vendor_name' })),
})

const renderDialog = (handlers: { onClose?: () => void; onChanged?: () => void } = {}) => render(
  <AdapterFieldsDialog target={target} onClose={handlers.onClose || vi.fn()} onChanged={handlers.onChanged || vi.fn()} />,
)

describe('AdapterFieldsDialog', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('lists the fields the adapter lacks and starts a trial for the ones chosen', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state())
    const start = vi.spyOn(parserApi, 'tryAdapterFields')
      .mockResolvedValue(state({ status: 'queued', requested_fields: ['name', 'product_id'] }))
    renderDialog()

    // antd keeps a dialog's content hidden in jsdom while it animates in.
    expect(await screen.findByText(/检查项有 7 个字段，这个适配器现在提供 4 个/)).toBeInTheDocument()
    expect(screen.getByText(/还有 3 个没有提供/)).toBeInTheDocument()
    expect(screen.getByText(/巡检中解析过的 9 份输出验证/)).toBeInTheDocument()
    expect(screen.getByText('变化检测')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: '补 vendor_name' }))
    await userEvent.click(screen.getByRole('button', { name: '开始试算' }))

    expect(start).toHaveBeenCalledWith('n9k-show-inventory', 'device.inventory', ['name', 'product_id'])
    expect(await screen.findByText('AI 正在补字段：name、product_id')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭，稍后回来看' })).toBeInTheDocument()
  })

  it('shows each new field with its values and the output line it was read from, and saves only the ticked ones', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(ready())
    const save = vi.spyOn(parserApi, 'saveAdapterFields').mockResolvedValue({
      fields: ['name'], left_out_fields: ['product_id'], implementation_version: 3, previous_implementation_version: 2,
      runtime_bundle_version_ids: ['b1'], outputs_replayed: 9, records_replayed: 72, at: '', by: 'admin',
    })
    const onChanged = vi.fn()
    renderDialog({ onChanged })

    expect(await screen.findAllByText('9 份输出、72 条记录取到了值')).toHaveLength(2)
    expect(screen.getByText('Slot 1')).toBeInTheDocument()
    // The captured part is marked inside the device's own line.
    const marked = document.querySelectorAll('.adapter-fields__source mark')
    expect([...marked].map((node) => node.textContent)).toEqual(['Chassis', 'N9K-C93180YC-FX'])
    expect(marked[1].closest('.adapter-fields__source')?.textContent).toContain('NAME: "Chassis"PID: N9K-C93180YC-FX')
    expect(screen.getByText(/9 份输出里都没有这个值。已记为“设备不输出”/)).toBeInTheDocument()
    expect(screen.getByText('已有字段不受影响')).toBeInTheDocument()
    expect(screen.getByText(/原有 10 条解析规则一条未改，只新增 2 条/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '查看模板改动' }))
    expect(screen.getByLabelText('新增的模板内容').textContent).toContain('Value name ([^"]+)')

    expect(screen.getByRole('button', { name: '保存并启用 2 个字段' })).toBeEnabled()
    await userEvent.click(screen.getByRole('checkbox', { name: '保存 product_id' }))
    await userEvent.click(screen.getByRole('button', { name: '保存并启用 1 个字段' }))

    await waitFor(() => expect(save).toHaveBeenCalledWith('n9k-show-inventory', 'device.inventory', ['name']))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('will not save when no field is ticked', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(ready())
    renderDialog()

    await screen.findByText('已有字段不受影响')
    await userEvent.click(screen.getByRole('checkbox', { name: '保存 name' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '保存 product_id' }))

    expect(screen.getByRole('button', { name: '保存并启用' })).toBeDisabled()
  })

  it('warns when one output is all there is to check against', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({
      status: 'ready', sample_count: 1, result: trial({ sample_count: 1, record_count: 1 }),
    }))
    renderDialog()

    expect(await screen.findByText('只有 1 份输出可用来验证')).toBeInTheDocument()
    expect(screen.getByText(/请更仔细地核对下面取到的值/)).toBeInTheDocument()
  })

  it('says the device does not print the fields when a trial found none, and leaves them unticked', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({
      status: 'no_data',
      missing_fields: [{ name: 'vendor_name', description: '', use: 'evidence', absent: true }],
      result: trial({ status: 'no_data', provided_fields: [], absent_fields: ['vendor_name'] }),
    }))
    renderDialog()

    expect(await screen.findByText('设备输出里没有这 1 个字段')).toBeInTheDocument()
    expect(screen.getByText('上次试算：设备不输出')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '补 vendor_name' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '开始试算' })).toBeDisabled()
  })

  it('gives the reason a trial failed and keeps the technical detail out of the way', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({
      status: 'failed',
      result: trial({ status: 'failed', provided_fields: [], absent_fields: [], fields: [],
        reason: 'AI 试了 3 次，每次都改动了已有的解析规则，结果没有采用。可以减少一次要补的字段再试。',
        detail: 'existing rules must stay exactly as they are' }),
    }))
    renderDialog()

    expect(await screen.findByText('上次试算没有成功，没有做任何改动')).toBeInTheDocument()
    expect(screen.getByText(/AI 试了 3 次，每次都改动了已有的解析规则/)).toBeInTheDocument()
    expect(screen.getByText('技术细节')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始试算' })).toBeEnabled()
  })

  it('will not offer a result from before the adapter or the check item changed', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({ status: 'ready', result: trial({ stale: true }) }))
    renderDialog()

    expect(await screen.findByText('上次试算的结果已经不能用')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /保存并启用/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始试算' })).toBeInTheDocument()
  })

  it('cannot start without an output to check against', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({ sample_count: 0 }))
    renderDialog()

    expect(await screen.findByText('还没有可以用来验证的输出')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始试算' })).toBeDisabled()
  })

  it('picks the result up when a running trial ends and tells the page', async () => {
    const load = vi.spyOn(parserApi, 'adapterFields')
      .mockResolvedValueOnce(state({ status: 'running', requested_fields: ['name'], started_at: new Date().toISOString() }))
      .mockResolvedValue(ready())
    const onChanged = vi.fn()
    renderDialog({ onChanged })

    expect(await screen.findByText('AI 正在补字段：name')).toBeInTheDocument()
    // The dialog asks again every few seconds while the worker is busy.
    expect(await screen.findByText('已有字段不受影响', undefined, { timeout: 6000 })).toBeInTheDocument()
    expect(load.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(onChanged).toHaveBeenCalledTimes(1)
  }, 12000)

  it('reports what went into force after a save', async () => {
    vi.spyOn(parserApi, 'adapterFields').mockResolvedValue(state({
      status: 'saved',
      missing_fields: [{ name: 'vendor_name', description: '', use: 'evidence', absent: true }],
      saved: { fields: ['name', 'product_id'], left_out_fields: [], implementation_version: 3,
        previous_implementation_version: 2, runtime_bundle_version_ids: ['b1'], outputs_replayed: 9,
        records_replayed: 72, at: '', by: 'admin' },
    }))
    renderDialog()

    expect(await screen.findByText('已启用 name、product_id')).toBeInTheDocument()
    expect(screen.getByText('适配器 v2 → v3，解析模板和 1 个运行包一起切换，下次巡检生效。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /关\s*闭/ })).toBeInTheDocument()
  })
})
