import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ParserCheckItem } from '@netops/services/api'
import CheckItemEditorDrawer from './CheckItemEditorDrawer'

const checkItem: ParserCheckItem = {
  check_item_id: 'environment.fan',
  check_item_version_id: 'environment-fan-v3',
  version: 3,
  name: 'Fan health',
  category: 'environment',
  description: 'Evaluate fan presence and operating state.',
  result_mode: 'structured',
  assessment_mode: 'health',
  canonical_contract: {
    entity_type: 'environment_fan',
    identity_dimensions: ['slot', 'fan'],
    observations: [{
      observation_type: 'environment.fan.state',
      semantic_type: 'state',
      canonical_field: 'fan_status',
    }],
    parser_field_vocabulary: {
      fan: { aliases: ['fan_id', 'fan_name'], role: 'identity' },
      fan_status: {
        aliases: ['status', 'state'],
        observation_type: 'environment.fan.state',
        role: 'state',
      },
    },
  },
  report_projection: 'fan',
  applicability: { sites: ['dc-a'] },
  freshness_policy: {
    max_observation_age: '24h',
    max_collection_span: '8h',
  },
  empty_result_policy: { classification: 'not_applicable' },
  coverage_policy: {
    model: 'derived_entity_set',
    source: 'previous_qualified_observation',
    first_observation: 'unable_to_determine',
  },
  status: 'active',
  digest: 'a'.repeat(64),
  implementation_count: 1,
  implementations: [],
}

describe('CheckItemEditorDrawer', () => {
  it('edits frozen semantics and saves a new identifier without changing the shared template', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    const definition = { id: 'environment.fan.state', description: '检查项自己的说明',
      semantic_type: 'state', value_types: ['text'], dimensions: [], assessment_roles: ['current_state'],
      state_groups: { healthy: ['ok'], unhealthy: ['broken'] }, allowed_values: ['ok', 'broken'] }
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={onSubmit}
      checkItem={{ ...checkItem, canonical_contract: { ...checkItem.canonical_contract,
        rule_semantics_snapshot: { digest: 'frozen', observations: [definition] } } }}
      canonicalCatalog={{ items: [{ ...definition, description: '全局目录说明' }] } as any} />)
    await user.click(screen.getByText('编辑语义与规则用途'))
    expect(screen.getByRole('checkbox', { name: '参与普通变化比较' })).toBeChecked()
    await user.click(screen.getByRole('checkbox', { name: '参与普通变化比较' }))
    expect(screen.getByLabelText('含义说明')).toHaveValue('检查项自己的说明')
    await user.clear(screen.getByLabelText('含义说明'))
    await user.type(screen.getByLabelText('含义说明'), '现场风扇状态')
    await user.clear(screen.getByLabelText('观测语义'))
    await user.type(screen.getByLabelText('观测语义'), 'custom.fan.state')
    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0].canonical_contract.observations[0]).toMatchObject({
      observation_type: 'custom.fan.state', semantic_definition: {
        description: '现场风扇状态', state_groups: { unhealthy: ['broken'] },
      },
      change_tracking: false,
    })
    expect(definition.description).toBe('检查项自己的说明')
  })

  it('allows creating a local field when the shared catalog is unavailable', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={onSubmit}
      canonicalError="模板目录暂不可用" checkItem={{ ...checkItem,
        canonical_contract: { ...checkItem.canonical_contract, observations: [] } }} />)
    await user.click(screen.getByRole('button', { name: /添加标准字段/ }))
    await user.type(screen.getByLabelText('标准列名'), 'MODEL')
    await user.type(screen.getByLabelText('观测语义'), 'custom.fan.model')
    await user.click(screen.getByText('编辑语义与规则用途'))
    await user.type(screen.getByLabelText('含义说明'), '风扇型号原文')
    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0].canonical_contract.observations[0]).toMatchObject({
      canonical_field: 'MODEL', semantic_definition: { description: '风扇型号原文', assessment_roles: ['evidence'] },
    })
  })
  it('saves exact uppercase Values and preserves child-list and uptime semantics', async () => {
    const onSubmit = vi.fn()
    const observations = [
      { canonical_field: 'SOFTWARE_VERSION', observation_type: 'device.version.software_version', required: true },
      { canonical_field: 'UPTIME', observation_type: 'device.version.uptime', required: true, change_tracking: false },
      { canonical_field: 'SUBSLOT', observation_type: 'device.version.subslot', required: false, collection: 'list', paired_with: 'SUBSLOT_INFO', unique_items: true },
      { canonical_field: 'SUBSLOT_INFO', observation_type: 'device.version.subslot_info', required: false, collection: 'list', paired_with: 'SUBSLOT' },
    ].map(row => ({ ...row, semantic_type: 'text', value_type: 'text' }))
    render(<CheckItemEditorDrawer open mobile={false}
      checkItem={{ ...checkItem, check_item_id: 'device.version', report_projection: 'auto@1',
        canonical_contract: { value_contract: 'same-name/v1', entity_type: 'device_slot',
          identity_dimensions: ['SLOT'], record_semantics: { grain: 'one slot per record' }, observations } }}
      canonicalCatalog={{ version: 6, items: observations.map(row => ({ id: row.observation_type,
        description: row.canonical_field, semantic_type: 'text', value_types: ['text'],
        dimensions: [], assessment_roles: ['evidence'] })) }}
      onClose={vi.fn()} onSubmit={onSubmit} />)
    await userEvent.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const contract = onSubmit.mock.calls[0][0].canonical_contract
    expect(contract.identity_dimensions).toEqual(['SLOT'])
    expect(contract.record_semantics).toEqual({ grain: 'one slot per record' })
    expect(contract.observations).toEqual(expect.arrayContaining([
      expect.objectContaining({ canonical_field: 'UPTIME', change_tracking: false }),
      expect.objectContaining({ canonical_field: 'SUBSLOT', collection: 'list', paired_with: 'SUBSLOT_INFO', unique_items: true }),
    ]))
  })

  it('preserves advanced contract fields when they remain collapsed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <CheckItemEditorDrawer
        open
        mobile={false}
        checkItem={checkItem}
        canonicalCatalog={{ version: 1, items: [{
          id: 'environment.fan.state',
          description: 'Current fan state',
          semantic_type: 'state',
          value_types: ['text'],
          dimensions: [],
          assessment_roles: ['current_state'],
        }] }}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.canonical_contract.parser_field_vocabulary).toMatchObject({
      fan: { aliases: ['fan_id', 'fan_name'], role: 'identity' },
      slot: { role: 'identity' },
      fan_status: {
        aliases: ['status', 'state'],
        observation_type: 'environment.fan.state',
        role: 'state',
        value_type: 'text',
      },
    })
    expect(payload.applicability).toEqual(checkItem.applicability)
  })
})
