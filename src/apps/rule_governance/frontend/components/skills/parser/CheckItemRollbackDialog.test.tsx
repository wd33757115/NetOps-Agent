import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { parserApi, type CheckItemRollbackPreview, type ParserCheckItem } from '@netops/services/api'
import CheckItemRollbackDialog from './CheckItemRollbackDialog'

const checkItem = {
  check_item_id: 'interface.optical_power',
  version: 19,
  name: 'Interface optical power',
  display_name: '接口光功率',
} as ParserCheckItem

const rollback = (overrides: Partial<CheckItemRollbackPreview> = {}): CheckItemRollbackPreview => ({
  change_id: 'change-21',
  check_item_id: 'interface.optical_power',
  current_version: 19,
  restores_version: 18,
  new_version: 21,
  restores_wording: true,
  impact_digest: 'd'.repeat(64),
  preview: {
    applicable: true,
    check_item_id: 'interface.optical_power',
    from_version: 19,
    to_version: 21,
    releasable: true,
    blockers: [],
    fields: [],
    release: { rules_activated: 3, rules_retired_without_successor: 0, adapters_repinned: 7,
      runtime_bundle_version_ids: [], adapters: [], events_closed: [] },
  },
  ...overrides,
})

const renderDialog = (handlers: { onClose?: () => void; onDone?: () => void } = {}) => render(
  <CheckItemRollbackDialog
    checkItem={checkItem}
    restoresVersion={18}
    onClose={handlers.onClose || vi.fn()}
    onDone={handlers.onDone || vi.fn()}
  />,
)

describe('CheckItemRollbackDialog', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('says what going back publishes and shows the release trial before anything switches', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRollback').mockResolvedValue(rollback())
    const submit = vi.spyOn(parserApi, 'submitCatalogChange')
    renderDialog()

    // antd keeps a dialog's content hidden in jsdom while it animates in.
    expect(await screen.findByText(/回退会发布一个新版本 v21，内容与 v18 相同/)).toBeInTheDocument()
    expect(screen.getByText(/现在的 v19 留在历史里/)).toBeInTheDocument()
    expect(screen.getByText('可以整体切换')).toBeInTheDocument()
    expect(parserApi.previewCheckItemRollback).toHaveBeenCalledWith('interface.optical_power')
    expect(submit).not.toHaveBeenCalled()
  })

  it('publishes the drafted version on the confirming button', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRollback').mockResolvedValue(rollback())
    const submit = vi.spyOn(parserApi, 'submitCatalogChange').mockResolvedValue({} as any)
    const decide = vi.spyOn(parserApi, 'decideCatalogChange').mockResolvedValue({} as any)
    const discard = vi.spyOn(parserApi, 'discardCatalogDraft')
    const onClose = vi.fn()
    const onDone = vi.fn()
    renderDialog({ onClose, onDone })

    await screen.findByText('可以整体切换')
    await userEvent.click(screen.getByRole('button', { name: '回退到 v18' }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const body = { reason: '回退到 v18', expected_impact_digest: 'd'.repeat(64) }
    expect(submit).toHaveBeenCalledWith('change-21', body, expect.stringContaining('catalog-rollback-submit'))
    expect(decide).toHaveBeenCalledWith('change-21', 'approve', body, expect.stringContaining('catalog-rollback-approve'))
    expect(onClose).toHaveBeenCalled()
    expect(discard).not.toHaveBeenCalled()
  })

  it('withdraws the draft it made when the engineer leaves without confirming', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRollback').mockResolvedValue(rollback())
    const discard = vi.spyOn(parserApi, 'discardCatalogDraft').mockResolvedValue({} as any)
    const onClose = vi.fn()
    renderDialog({ onClose })

    await screen.findByText('可以整体切换')
    await userEvent.click(screen.getByRole('button', { name: /取\s*消/ }))

    await waitFor(() => expect(discard).toHaveBeenCalledWith('change-21'))
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the button off and says why when the earlier version cannot take over', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRollback').mockResolvedValue(rollback({
      restores_wording: false,
      preview: { ...rollback().preview, releasable: false, release: null,
        blockers: ['2 个适配器与新版本的字段不匹配（N9K、MSR5620）：不提供必填字段 lane_status'] },
    }))
    renderDialog()

    expect(await screen.findByText('现在不能发布，当前版本继续巡检')).toBeInTheDocument()
    expect(screen.getByText(/不提供必填字段 lane_status/)).toBeInTheDocument()
    expect(screen.getByText('v18 发布时没有留下名称和说明，这两项保持现在的内容。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '回退到 v18' })).toBeDisabled()
  })

  it('says so when the rollback could not be prepared, and has nothing to withdraw', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRollback').mockRejectedValue(new Error('这个检查项没有更早发布过的版本，无法回退'))
    const discard = vi.spyOn(parserApi, 'discardCatalogDraft').mockResolvedValue({} as any)
    renderDialog()

    expect(await screen.findByText('无法准备回退')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '回退到 v18' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /取\s*消/ }))
    expect(discard).not.toHaveBeenCalled()
  })
})
