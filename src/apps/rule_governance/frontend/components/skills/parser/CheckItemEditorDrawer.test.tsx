import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { parserApi, type ParserCheckItem } from '@netops/services/api'
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
    // The tracking switch is gone: only the one use decides whether a field is compared.
    expect(screen.queryByRole('checkbox', { name: '参与普通变化比较' })).not.toBeInTheDocument()
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

  it('keeps a published field on the dimensions it was published with, whatever its definition lists', async () => {
    const onSubmit = vi.fn()
    const row = (name: string, extra: Record<string, unknown> = {}) => ({ canonical_field: name,
      observation_type: `configuration.startup_files.${name.toLowerCase()}`, semantic_type: 'text', value_type: 'text',
      required: false, assessment_roles: ['evidence'], ...extra })
    const observations = [row('CURRENT_FILE'), row('NEXT_MAIN_FILE', { dimensions: ['BOARD'] })]
    render(<CheckItemEditorDrawer open mobile={false}
      checkItem={{ ...checkItem, check_item_id: 'configuration.startup_files', report_projection: 'auto@1',
        canonical_contract: { value_contract: 'same-name/v1', entity_type: 'device_board',
          identity_dimensions: ['BOARD'], observations,
          // An early import: the frozen definitions name the identity as a dimension, the fields do not.
          rule_semantics_snapshot: { digest: 'frozen', observations: observations.map(item => ({
            id: item.observation_type, description: item.canonical_field, semantic_type: 'text',
            value_types: ['text'], dimensions: ['BOARD'], assessment_roles: ['evidence'] })) } } }}
      onClose={vi.fn()} onSubmit={onSubmit} />)
    await userEvent.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const saved = onSubmit.mock.calls[0][0].canonical_contract.observations as Array<Record<string, unknown>>
    // The adapters output CURRENT_FILE without a dimension: a draft that gave it one could not be published.
    expect(saved.find(item => item.canonical_field === 'CURRENT_FILE')?.dimensions).toEqual([])
    expect(saved.find(item => item.canonical_field === 'NEXT_MAIN_FILE')?.dimensions).toEqual(['BOARD'])
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

  const numeric = (canonical_field: string, id: string, semantic_type: string, extra = {}) => ({
    canonical_field, observation_type: id, semantic_type, value_type: 'number', unit: 'percent', change_tracking: false, ...extra,
  })
  const frozen = (id: string, semantic_type: string, assessment_roles: string[]) => ({
    id, description: id, semantic_type, value_types: ['number'], canonical_unit: 'percent', dimensions: [],
    allowed_values: [], state_groups: {}, assessment_roles,
  })
  const cpu: ParserCheckItem = { ...checkItem, check_item_id: 'system.cpu_usage', canonical_contract: {
    entity_type: 'cpu_scope', identity_dimensions: ['slot'], value_contract: 'same-name/v1',
    observations: [
      numeric('cpu_usage_percent', 'device.cpu.utilization', 'gauge', {
        // Stored as "evidence", yet the runtime judges it from the health policy.
        assessment_roles: ['evidence'], fixed_dimensions: { window: '5m' },
        threshold_references: { high: { field_name: 'cpu_high_warning_percent' }, critical_high: { field_name: 'cpu_high_alarm_percent' } },
        health_policy: { revision: 'old', window: '5m', baseline_mode: 'trend_only',
          trend: { kind: 'utilization_increase', threshold_ratio: 0.1, min_increase_points: 10 },
          device_thresholds: { high: ['cpu_high_warning_percent', 'cpu_high_alarm_percent'] } },
      }),
      numeric('cpu_high_warning_percent', 'device.cpu.high_warning', 'threshold', { assessment_roles: ['reference'] }),
      numeric('cpu_high_alarm_percent', 'device.cpu.high_alarm', 'threshold', { assessment_roles: ['reference'] }),
    ],
    rule_semantics_snapshot: { digest: 'frozen', observations: [
      frozen('device.cpu.utilization', 'gauge', ['current_state', 'change']),
      frozen('device.cpu.high_warning', 'threshold', ['reference']),
      frozen('device.cpu.high_alarm', 'threshold', ['reference']),
    ] },
  } }

  it('opens a health-policy Value as a health check and hands the server only its trend', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<CheckItemEditorDrawer open mobile={false} checkItem={cpu} onClose={vi.fn()} onSubmit={onSubmit} />)
    expect(screen.getByLabelText('评估模式')).toHaveValue('健康与风险')
    expect(screen.queryByText('启用全部字段变化事件')).not.toBeInTheDocument()
    await user.click(screen.getAllByText('编辑语义与规则用途')[0])
    expect(screen.getByText('上限阈值字段')).toBeInTheDocument()
    expect(screen.getByLabelText('相对上次上升超过（%）')).toHaveValue('10')
    await user.clear(screen.getByLabelText('相对上次上升超过（%）'))
    await user.type(screen.getByLabelText('相对上次上升超过（%）'), '20')
    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    const payload = onSubmit.mock.calls[0][0]
    const usage = payload.canonical_contract.observations[0]
    expect(payload.assessment_mode).toBe('health')
    expect(payload.canonical_contract.change_events).toBe(false)
    expect(usage.semantic_definition.assessment_roles).toEqual(['current_state'])
    expect(usage.threshold_references).toEqual({
      high: { field_name: 'cpu_high_warning_percent' }, critical_high: { field_name: 'cpu_high_alarm_percent' },
    })
    expect(usage.health_policy).toEqual({ trend: { kind: 'utilization_increase', threshold_ratio: 0.2, min_increase_points: 10 } })
    expect(payload.canonical_contract.observations[1].semantic_definition.assessment_roles).toEqual(['reference'])
  })

  it('keeps one use per Value and shows what the draft will do', async () => {
    const onSubmit = vi.fn()
    const preview = vi.spyOn(parserApi, 'previewCheckItemRules').mockResolvedValue({
      valid: true, errors: [], assessment_mode: 'change', fields: [{
        canonical_field: 'fan_status', observation_type: 'environment.fan.state', outcome: 'change',
        lines: ['从 ok 变为 broken 时产生故障事件（重要）。'], warnings: [],
      }],
    })
    const definition = { id: 'environment.fan.state', description: '风扇状态', semantic_type: 'state',
      value_types: ['text'], dimensions: [], assessment_roles: ['evidence'], allowed_values: ['ok', 'broken'],
      state_groups: { healthy: ['ok'], unhealthy: ['broken'] } }
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={onSubmit}
      checkItem={{ ...checkItem, canonical_contract: { ...checkItem.canonical_contract,
        observations: [{ ...(checkItem.canonical_contract.observations as any[])[0], assessment_roles: ['change', 'evidence'] }],
        rule_semantics_snapshot: { digest: 'frozen', observations: [definition] } } }} />)
    expect(await screen.findByText('从 ok 变为 broken 时产生故障事件（重要）。')).toBeInTheDocument()
    expect(preview.mock.calls[0][0].canonical_contract).toMatchObject({ change_events: false })
    await userEvent.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.assessment_mode).toBe('change')
    expect(payload.canonical_contract.observations[0]).toMatchObject({
      change_tracking: true, semantic_definition: { assessment_roles: ['change'], state_groups: { unhealthy: ['broken'] } },
    })
    preview.mockRestore()
  })

  it('sets the rules in force beside the draft only where they differ', async () => {
    const draft = (field: string, outcome: string, lines: string[]) => ({
      canonical_field: field, observation_type: field, outcome, lines, warnings: [] })
    const preview = vi.spyOn(parserApi, 'previewCheckItemRules').mockResolvedValue({
      valid: true, errors: [], assessment_mode: 'health', fields: [
        draft('cpu_usage_percent', 'health', ['高于设备输出的 cpu_high_warning_percent 时判定异常。']),
        draft('cpu_high_warning_percent', 'reference', ['作为 cpu_usage_percent 的阈值。']),
        draft('cpu_high_alarm_percent', 'reference', ['作为 cpu_usage_percent 的阈值。']),
      ] } as any)
    const active = (field: string, lines: string[], stale = 0) => ({
      canonical_field: field, observation_type: field, lines, stale_count: stale, stale_versions: stale ? [13] : [] })
    const inForce = vi.spyOn(parserApi, 'checkItemRulesInForce').mockResolvedValue({
      check_item_id: 'system.cpu_usage', version: 14, check_item_version_id: 'v14', leftover_count: 2, fields: [
        active('cpu_usage_percent', ['不低于设备输出的 cpu_high_warning_percent 时产生 CpuUtilizationHigh（警告）。'], 1),
        active('cpu_high_warning_percent', []),
        active('cpu_high_alarm_percent', []),
      ] })
    render(<CheckItemEditorDrawer open mobile={false} checkItem={cpu} onClose={vi.fn()} onSubmit={vi.fn()} />)
    expect(await screen.findByText('不低于设备输出的 cpu_high_warning_percent 时产生 CpuUtilizationHigh（警告）。')).toBeInTheDocument()
    expect(inForce).toHaveBeenCalledWith('system.cpu_usage')
    expect(screen.getAllByText('现在生效（保存并发布后将被上面的内容取代）：')).toHaveLength(1)
    expect(screen.getAllByText('与现在生效的规则一致。')).toHaveLength(2)
    expect(screen.getByText('另有 1 条绑定在旧版本 v13 上的规则仍为生效状态。')).toBeInTheDocument()
    expect(screen.getByText('另有 2 条规则绑定在旧版本已不存在的字段上，仍为生效状态')).toBeInTheDocument()
    preview.mockRestore()
    inForce.mockRestore()
  })
})

describe('CheckItemEditorDrawer boolean states', () => {
  // interface.optical_power stores transceiver_present this way: a state whose value type is boolean
  // and which names no transitional group.
  it('saves a boolean state whose groups are only partly filled in', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    vi.spyOn(parserApi, 'previewCheckItemRules').mockResolvedValue({ valid: true, errors: [], fields: [] } as any)
    const definition = { id: 'interface.optical.presence', description: '光模块是否在位',
      semantic_type: 'state', value_types: ['boolean'], dimensions: [], assessment_roles: ['current_state'],
      state_groups: { healthy: ['present'], unhealthy: ['absent'], unknown: ['unknown'] },
      allowed_values: ['present', 'absent', 'unknown'] }
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={onSubmit}
      checkItem={{ ...checkItem, canonical_contract: { ...checkItem.canonical_contract,
        observations: [{ observation_type: 'interface.optical.presence', semantic_type: 'state',
          canonical_field: 'transceiver_present', value_type: 'boolean', semantic_definition: definition }] } }}
      canonicalCatalog={{ items: [] } as any} />)

    await user.click(screen.getByText('编辑语义与规则用途'))
    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
    expect(onSubmit.mock.calls[0][0].canonical_contract.observations[0].semantic_definition.state_groups).toEqual({
      healthy: ['present'], unhealthy: ['absent'], unknown: ['unknown'],
    })
  })
})

describe('CheckItemEditorDrawer rule generation', () => {
  const definition = { id: 'environment.fan.state', description: '风扇状态', semantic_type: 'state',
    value_types: ['text'], dimensions: [], assessment_roles: ['change'], allowed_values: ['ok', 'broken'],
    state_groups: { healthy: ['ok'], unhealthy: ['broken'] } }
  const fan = { ...checkItem, canonical_contract: { ...checkItem.canonical_contract,
    observations: [{ ...(checkItem.canonical_contract.observations as any[])[0], assessment_roles: ['change'] }],
    rule_semantics_snapshot: { digest: 'frozen', observations: [definition] } } }
  const generated = {
    canonical_field: 'fan_status', observation_type: 'environment.fan.state', outcome: 'change',
    lines: ['从 ok 变为 broken 时产生故障事件（重要）。'], warnings: [],
    guide_row: 'change_state_grouped', summary: '会生成 1 条变化规则（状态转换）和 3 条事件规则。',
    reason: '因为它是状态、用于变化检测，并且填了正常和异常状态。', change_rules: 1, event_rules: 3,
  }

  it('says under each field what it generates and why, and opens the guide on its row', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRules').mockResolvedValue({
      valid: true, errors: [], assessment_mode: 'change', fields: [generated] } as any)
    vi.spyOn(parserApi, 'ruleGenerationGuide').mockResolvedValue({
      rows: [
        { key: 'change_text', kinds: '文本', use: '变化检测', needs: '', change_rule: '1 条，精确比较', event_rules: '1 条提示', effect: '值变了就提示。' },
        { key: 'change_state_grouped', kinds: '状态、枚举、布尔', use: '变化检测', needs: '正常状态和异常状态',
          change_rule: '1 条，状态转换', event_rules: '3 条：故障（重要）、恢复、提示', effect: '变坏报故障。' },
      ],
      fixed: ['级别是固定的。'], timing: ['发布时生效。'],
    })
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={vi.fn()} checkItem={fan} />)

    expect(await screen.findByText('会生成 1 条变化规则（状态转换）和 3 条事件规则。')).toBeInTheDocument()
    expect(screen.getByText(/因为它是状态、用于变化检测，并且填了正常和异常状态。/)).toBeInTheDocument()

    await userEvent.click(screen.getAllByRole('button', { name: '规则是怎么生成的' })[0])
    // antd keeps a dialog's content hidden in jsdom while it animates in.
    expect(await screen.findByText('3 条：故障（重要）、恢复、提示')).toBeInTheDocument()
    expect(screen.getByText('标出的一行是字段 fan_status 现在的设置。')).toBeInTheDocument()
    const current = document.querySelector('tr.rule-guide__row--current')
    expect(current?.textContent).toContain('状态、枚举、布尔')
    expect(document.querySelectorAll('tr.rule-guide__row--current')).toHaveLength(1)
    expect(screen.getByText('级别是固定的。')).toBeInTheDocument()
  })

  it('draws what the draft generates while it is edited', async () => {
    vi.spyOn(parserApi, 'previewCheckItemRules').mockResolvedValue({
      valid: true, errors: [], assessment_mode: 'change', fields: [generated],
      graph: {
        mode: 'draft',
        rows: [{
          field: { name: 'fan_status', description: '风扇状态', observation_type: 'environment.fan.state' },
          rule: { kind: 'change', title: '变化规则', detail: '状态转换', lines: [] },
          outcomes: [{ key: 'event:environment.fan.state.degraded', kind: 'event', event_type: 'environment.fan.state.degraded',
            event_class: 'fault', severity: 'major', text: '从 ok 变为 broken 时产生故障事件（重要）。' }],
        }],
        reference: [], evidence: [], errors: [],
      },
    } as any)
    render(<CheckItemEditorDrawer open mobile={false} onClose={vi.fn()} onSubmit={vi.fn()} checkItem={fan} />)

    const row = await screen.findByRole('listitem', { name: 'fan_status：变化规则，状态转换' })
    expect(row.textContent).toContain('故障事件，重要')
    // A draft is in force nowhere and has produced nothing: no state, no counts.
    expect(row.textContent).not.toContain('生效')
    expect(row.querySelector('.relation-count')).toBeNull()
    expect(screen.getByText(/下图是这份草稿会生成的规则和事件/)).toBeInTheDocument()
  })
})
