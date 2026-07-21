import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ParserRuntimeRuleAssetBundle } from '../../../services/api'
import RuntimeAssetsWorkspace from './RuntimeAssetsWorkspace'

const runtimeAssets = (
  overrides: Partial<ParserRuntimeRuleAssetBundle> = {},
): ParserRuntimeRuleAssetBundle => ({
  ready: true,
  collection_ready: true,
  analysis_ready: true,
  event_evaluation_ready: false,
  digest: 'runtime-digest',
  observation_contracts: { 'textfsm:interface': {} },
  change_policies: { 'interface.oper_status': {} },
  event_rules: [],
  missing_observation_contracts: [],
  missing_change_policies: [],
  missing_event_rules: ['interface.oper_status'],
  observation_types: ['interface.oper_status'],
  counts: { observation_contracts: 1, change_policies: 1, event_rules: 0 },
  ...overrides,
})

const renderWorkspace = (assets: ParserRuntimeRuleAssetBundle) => render(
  <RuntimeAssetsWorkspace
    assets={assets}
    loading={false}
    activeCounts={{ contracts: 0, policies: 0, eventRules: 0 }}
    schemas={[['textfsm', 'interface']]}
    observationTypes={['interface.oper_status']}
    isMobile={false}
  />,
)

describe('RuntimeAssetsWorkspace', () => {
  it('does not show a full-ready result when only analysis is ready', () => {
    renderWorkspace(runtimeAssets())

    expect(screen.getByText('变化分析可用，但事件评估尚未就绪')).toBeVisible()
    expect(screen.queryByText('巡检规则运行时全链路已就绪')).not.toBeInTheDocument()
    expect(screen.getByText('缺 1 个事件规则覆盖')).toBeVisible()
  })

  it('shows full readiness only when event evaluation is ready', () => {
    renderWorkspace(runtimeAssets({
      event_evaluation_ready: true,
      event_rules: [{ id: 'event-rule-1' }],
      missing_event_rules: [],
      counts: { observation_contracts: 1, change_policies: 1, event_rules: 1 },
    }))

    expect(screen.getByText('巡检规则运行时全链路已就绪')).toBeVisible()
    expect(screen.getByText('事件规则完整')).toBeVisible()
  })
})
