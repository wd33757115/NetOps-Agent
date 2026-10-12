import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { describe, expect, it, vi } from 'vitest'

import { parserApi, type CheckItemDeletePreview, type ParserCheckItem } from '@netops/services/api'
import CheckItemDeleteDialog from './CheckItemDeleteDialog'

const checkItem = {
  check_item_id: 'environment.fan',
  version: 5,
  name: 'Fan state',
  display_name: '风扇状态',
} as ParserCheckItem

const preview: CheckItemDeletePreview = {
  check_item_id: 'environment.fan',
  deletable: true,
  blockers: [],
  name: '风扇状态',
  version: 5,
  version_count: 3,
  cancelled_change_count: 1,
  rules: [
    { kind: 'change_policy', name: 'environment.fan.state' },
    { kind: 'event_rule', name: 'environment_fan_state_down' },
  ],
  runtime_bundle_version_ids: ['bundle-1', 'bundle-2'],
  adapters_withdrawn: [{ implementation_id: 'h3c', name: 'S6850-56HF · display fan' }],
  adapters_kept: [{ implementation_id: 'isr', name: 'ISR4451-X/K9 · show env all', also_serves: ['温度'] }],
  events_closed: [{ event_type: 'environment_fan_state_down', event_class: 'fault', severity: 'major', count: 4 }],
}

const renderDialog = (handlers: { onClose?: () => void; onDeleted?: () => void } = {}) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <CheckItemDeleteDialog
      checkItem={checkItem}
      onClose={handlers.onClose || vi.fn()}
      onDeleted={handlers.onDeleted || vi.fn()}
    />
  </QueryClientProvider>,
)

describe('CheckItemDeleteDialog', () => {
  it('shows everything that goes with the check item before anything is done', async () => {
    vi.spyOn(parserApi, 'previewCheckItemDelete').mockResolvedValue(preview)
    const remove = vi.spyOn(parserApi, 'deleteCheckItem')
    renderDialog()

    const flow = await screen.findByRole('list', { name: '删除会一起停用的内容' })
    const steps = within(flow).getAllByRole('listitem').map((item) => item.textContent)
    expect(steps).toEqual([
      '检查项停用新的巡检不再执行这项检查。1 个还没发布的修改一并取消。',
      '2 条规则停用1 条变化规则，1 条事件规则（environment_fan_state_down）。',
      '2 个运行包停用匹配这些运行包的设备不再采集这项检查。',
      '1 个命令适配器停用只为这个检查项服务：S6850-56HF · display fan。TextFSM 模板保留。'
        + '另有 1 个还为其他检查项服务，继续保留：ISR4451-X/K9 · show env all。',
      '4 条事件关闭规则停用后不会再更新，关闭时注明“检查项已删除”：environment_fan_state_down。',
    ])
    // antd keeps a dialog's content hidden in jsdom while it animates in.
    expect(screen.getByText('保留：历史巡检报告、全部 3 个版本、审计记录。')).toBeInTheDocument()
    expect(parserApi.previewCheckItemDelete).toHaveBeenCalledWith('environment.fan')
    expect(remove).not.toHaveBeenCalled()
  })

  it('deletes only on the confirming button, then tells the catalog', async () => {
    vi.spyOn(parserApi, 'previewCheckItemDelete').mockResolvedValue(preview)
    const remove = vi.spyOn(parserApi, 'deleteCheckItem').mockResolvedValue({
      success: true, check_item_id: 'environment.fan', status: 'deprecated', cancelled_change_count: 1, idempotent_replay: false,
    })
    const onClose = vi.fn()
    const onDeleted = vi.fn()
    renderDialog({ onClose, onDeleted })

    await userEvent.type(await screen.findByPlaceholderText('删除备注（可选）'), '不再巡检风扇')
    await userEvent.click(screen.getByRole('button', { name: '删除并停用以上内容' }))

    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    expect(remove).toHaveBeenCalledWith('environment.fan', '不再巡检风扇', expect.stringContaining('catalog-check-item-delete-'))
    expect(onClose).toHaveBeenCalled()
  })

  it('leaves out the steps that have nothing to do', async () => {
    vi.spyOn(parserApi, 'previewCheckItemDelete').mockResolvedValue({
      check_item_id: 'environment.fan', deletable: true, blockers: [], version_count: 1, cancelled_change_count: 0,
      rules: [], runtime_bundle_version_ids: [], adapters_withdrawn: [], adapters_kept: [], events_closed: [],
    })
    renderDialog()

    const flow = await screen.findByRole('list', { name: '删除会一起停用的内容' })
    expect(within(flow).getAllByRole('listitem')).toHaveLength(1)
  })

  it('says why a check item cannot be deleted and keeps the button off', async () => {
    vi.spyOn(parserApi, 'previewCheckItemDelete').mockResolvedValue({
      check_item_id: 'topology.routes', deletable: false, blockers: ['系统基础检查项不能删除'],
    })
    renderDialog()

    expect(await screen.findByText('这个检查项不能删除')).toBeInTheDocument()
    expect(screen.getByText('系统基础检查项不能删除')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除并停用以上内容' })).toBeDisabled()
  })
})
