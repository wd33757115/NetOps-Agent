import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import RuleAssetProductionWorkbench from './RuleAssetProductionWorkbench'

vi.mock('./CheckItemCatalogWorkspace', () => ({
  default: ({ initialOfflineJobId }: { initialOfflineJobId?: string }) => (
    <div data-testid="catalog">{initialOfflineJobId}</div>
  ),
}))

vi.mock('./RuleAssetSourceJobsWorkspace', () => ({
  default: () => <div data-testid="sample-jobs" />,
}))

vi.mock('./RuleAssetBundleWorkspace', () => ({
  default: ({ initialSearch, initialBundleId }: {
    initialSearch?: string
    initialBundleId?: string
  }) => <div data-testid="bundle-review">{initialSearch}|{initialBundleId}</div>,
}))

vi.mock('../../devices/OfflineCommandAssetUnits', () => ({
  default: ({ jobId, initialSearch }: { jobId: string, initialSearch?: string }) => (
    <div data-testid="sample-parser-flow">{jobId}|{initialSearch}</div>
  ),
}))

describe('RuleAssetProductionWorkbench', () => {
  it('keeps command adapters inside the unified check-item catalog', () => {
    const onViewChange = vi.fn()
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={onViewChange}
      />,
    )

    expect(screen.getByRole('button', { name: /检查项目录/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /样本导入/ })).toBeInTheDocument()
    expect(screen.queryByText('Observation Contract')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /命令适配器/ })).not.toBeInTheDocument()
    expect(screen.getByTestId('catalog')).toBeInTheDocument()

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

    expect(screen.getByTestId('catalog')).toBeInTheDocument()
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

  it('opens a focused sample command in the catalog-context parser flow', () => {
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={vi.fn()}
        focusOfflineJobId="offline-real-sample"
        initialSearch="display arp"
        focusAction="parser"
      />,
    )

    expect(screen.getByTestId('sample-parser-flow')).toHaveTextContent(
      'offline-real-sample|display arp',
    )
  })

  it('keeps the catalog scoped to the source job when opened for classification', () => {
    render(
      <RuleAssetProductionWorkbench
        view="catalog"
        onViewChange={vi.fn()}
        focusOfflineJobId="offline-real-sample"
        focusAction="classify_check_items"
      />,
    )

    expect(screen.getByTestId('catalog')).toHaveTextContent('offline-real-sample')
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

  it('opens the bundle review list from the release route without a focused bundle', () => {
    render(
      <RuleAssetProductionWorkbench
        view="commands"
        onViewChange={vi.fn()}
        focusAction="bundle-review"
      />,
    )

    expect(screen.getByTestId('bundle-review')).toBeInTheDocument()
  })
})
