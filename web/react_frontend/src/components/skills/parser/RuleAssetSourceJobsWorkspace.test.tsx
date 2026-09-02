import React from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const workbench = vi.fn(() => <div>source workbench</div>)

vi.mock('../../devices/OfflinePatrolPanel', () => ({
  OfflineFileJobWorkbench: (props: unknown) => workbench(props),
}))

import RuleAssetSourceJobsWorkspace from './RuleAssetSourceJobsWorkspace'

describe('RuleAssetSourceJobsWorkspace', () => {
  it('turns an AssetGap deep link into a scoped, pre-opened evidence import', () => {
    render(
      <MemoryRouter
        initialEntries={[
          '/skills/parser?workspace=production&subtab=source-jobs'
          + '&action=asset-gap-source&asset_gap_id=gap-1'
          + '&check_item_id=system.cpu_usage'
          + '&implementation_version_id=impl-cpu-v1'
          + '&command=display+cpu-usage',
        ]}
      >
        <RuleAssetSourceJobsWorkspace />
      </MemoryRouter>,
    )

    expect(screen.getByText('从生产资产缺口导入 CLI 样本')).toBeInTheDocument()
    expect(screen.getByText('gap-1')).toBeInTheDocument()
    expect(screen.getByText('system.cpu_usage')).toBeInTheDocument()
    expect(screen.getByText('impl-cpu-v1')).toBeInTheDocument()
    expect(screen.getByText('display cpu-usage')).toBeInTheDocument()
    expect(workbench).toHaveBeenCalledWith({
      purpose: 'rule_asset_source',
      initialJobType: 'parser_validation',
      initialJobName: 'system.cpu_usage CLI 样本',
    })
  })
})
