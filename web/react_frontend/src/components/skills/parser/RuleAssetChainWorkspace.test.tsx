import React from 'react'
import { render, screen } from '@testing-library/react'
import RuleAssetChainWorkspace from './RuleAssetChainWorkspace'
import type { RuleAssetChainRow } from './parserWorkspaceModel'

const row = (overrides: Partial<RuleAssetChainRow> = {}): RuleAssetChainRow => ({
  observationType: 'interface.oper_status',
  entityTypes: ['interface_l2'],
  contracts: [],
  policies: [],
  rules: [],
  activeContracts: [],
  activePolicies: [],
  activeRules: [],
  status: 'blocked',
  ...overrides,
})

describe('RuleAssetChainWorkspace', () => {
  it('shows a blocked chain and its actionable missing assets', () => {
    render(<RuleAssetChainWorkspace rows={[row()]} />)

    expect(screen.getByText('interface.oper_status')).toBeVisible()
    expect(screen.getByText('阻塞')).toBeVisible()
    expect(screen.getByText('缺少 Active Contract')).toBeVisible()
    expect(screen.getByText('缺少 Active Policy')).toBeVisible()
  })

  it('shows an explicit empty state', () => {
    render(<RuleAssetChainWorkspace rows={[]} />)

    expect(screen.getByText('暂无可识别的 Observation Type 资产')).toBeVisible()
  })
})
