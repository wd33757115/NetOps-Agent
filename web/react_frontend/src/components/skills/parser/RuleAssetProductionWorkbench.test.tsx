import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import RuleAssetProductionWorkbench from './RuleAssetProductionWorkbench'

vi.mock('./CheckItemCatalogWorkspace', () => ({
  default: ({ mode }: { mode: string }) => <div data-testid={`catalog-${mode}`} />,
}))

vi.mock('./RuleAssetSourceJobsWorkspace', () => ({
  default: () => <div data-testid="sample-jobs" />,
}))

vi.mock('../../devices/OfflineCommandAssetUnits', () => ({
  default: ({ jobId, initialSearch, focusAction, generationContent }: {
    jobId: string
    initialSearch?: string
    focusAction?: string
    generationContent?: React.ReactNode
  }) => (
    <div data-testid="offline-command-assets">
      {jobId}|{initialSearch}|{focusAction}
      {generationContent}
    </div>
  ),
}))

vi.mock('./FocusedRuleAssetGeneration', () => ({
  default: ({ seedJobId, datasetIds }: { seedJobId?: string; datasetIds: string[] }) => (
    <div data-testid="focused-generation">{seedJobId}|{datasetIds.join(',')}</div>
  ),
}))

vi.mock('./RuleAssetBundleWorkspace', () => ({
  default: ({ initialSearch, initialBundleId }: {
    initialSearch?: string
    initialBundleId?: string
  }) => <div data-testid="bundle-review">{initialSearch}|{initialBundleId}</div>,
}))

describe('RuleAssetProductionWorkbench', () => {
  it('exposes only the three product concepts', () => {
    const onViewChange = vi.fn()
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={onViewChange}
      />,
    )

    expect(screen.getByRole('button', { name: /检查项目录/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /命令适配器/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /样本任务/ })).toBeInTheDocument()
    expect(screen.queryByText('Observation Contract')).not.toBeInTheDocument()
    expect(screen.getByTestId('catalog-command_adapters')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /检查项目录/ }))
    expect(onViewChange).toHaveBeenCalledWith('catalog')
  })

  it('redirects the retired policy view to the check-item catalog', () => {
    render(
      <RuleAssetProductionWorkbench
        view="policies"
        onViewChange={vi.fn()}
      />,
    )

    expect(screen.getByTestId('catalog-check_items')).toBeInTheDocument()
  })

  it('opens sample jobs without exposing the retired command asset chain', () => {
    render(
      <RuleAssetProductionWorkbench
        view="sources"
        onViewChange={vi.fn()}
      />,
    )

    expect(screen.getByTestId('sample-jobs')).toBeInTheDocument()
    expect(screen.queryByText('运行包')).not.toBeInTheDocument()
  })

  it('opens a focused sample command directly in its production flow', () => {
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={vi.fn()}
        focusOfflineJobId="offline-real-sample"
        initialSearch="display arp"
        focusAction="parser"
      />,
    )

    expect(screen.getByTestId('offline-command-assets')).toHaveTextContent(
      'offline-real-sample|display arp|parser',
    )
    expect(screen.queryByTestId('catalog-command_adapters')).not.toBeInTheDocument()
  })

  it('injects the focused generation flow for a command seed', () => {
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={vi.fn()}
        focusOfflineJobId="offline-real-sample"
        focusRuleSeedJobId="seed-display-arp"
        datasetIds={['offline-real-sample-period']}
        initialSearch="display arp"
        focusAction="rule-generation"
      />,
    )

    expect(screen.getByTestId('focused-generation')).toHaveTextContent(
      'seed-display-arp|offline-real-sample-period',
    )
  })

  it('opens the focused bundle review from a command release action', () => {
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={vi.fn()}
        focusOfflineJobId="offline-real-sample"
        focusBundleId="bundle-display-arp"
        focusAction="release"
      />,
    )

    expect(screen.getByTestId('bundle-review')).toHaveTextContent(
      'offline-real-sample|bundle-display-arp',
    )
    expect(screen.queryByTestId('offline-command-assets')).not.toBeInTheDocument()
  })
})
