import React from 'react'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ruleAssetSourceApi } from '../../services/api'
import OfflineCommandAssetUnits from './OfflineCommandAssetUnits'

describe('OfflineCommandAssetUnits', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the backend check-item grouping and unclassified commands', async () => {
    const unit = {
      id: 'unit-unclassified',
      offline_job_id: 'offline-1',
      asset_key: 'H3C/S5130/display vendor-special-state',
      vendor: 'H3C',
      model: 'S5130',
      models: ['S5130'],
      profile: 'S5130',
      canonical_command: 'display vendor-special-state',
      handling: 'structured',
      asset_mode: 'full_chain',
      status: 'blocked',
      current_stage: 'identity_grouping',
      current_stage_label: '检查项归类',
      progress_percent: 20,
      sample_count: 2,
      device_count: 1,
      output_signature_count: 1,
      blocker_count: 1,
      parse_counts: { parsed: 2 },
      stage_statuses: {},
      projection_version: 11,
    }
    vi.spyOn(ruleAssetSourceApi, 'commandAssetUnits').mockResolvedValue({
      items: [unit],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
      summary: {
        total: 1,
        completed: 0,
        running: 0,
        needs_review: 0,
        blocked: 1,
        by_status: { blocked: 1 },
        by_stage: { identity_grouping: 1 },
        by_mode: { full_chain: 1 },
      },
    } as never)
    vi.spyOn(ruleAssetSourceApi, 'checkItemProductionGroups').mockResolvedValue({
      items: [{
        id: 'check-item:__unclassified__',
        check_item_id: null,
        check_item_version_id: null,
        name: '待归类检查项',
        display_name: '待归类检查项',
        category: 'unclassified',
        description: '尚未绑定检查项',
        display_description: '尚未绑定检查项',
        classification_required: true,
        governance_status: 'blocked',
        governance_bucket: 'exception',
        current_stage: 'identity_grouping',
        current_stage_label: '检查项归类',
        progress_percent: 20,
        sample_count: 2,
        evidence_device_count: 1,
        implementation_count: 1,
        command_count: 1,
        blocker_count: 1,
        vendors: ['H3C'],
        implementations: [{ ...unit, mapping_status: 'unclassified' }],
      }],
      page: 1,
      page_size: 100,
      total: 1,
      page_count: 1,
      summary: {
        total: 1,
        action_required: 0,
        in_progress: 0,
        awaiting_approval: 0,
        published: 0,
        not_applicable: 0,
        exception: 1,
        unclassified: 1,
        by_bucket: { exception: 1 },
        by_status: { blocked: 1 },
      },
    } as never)

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <OfflineCommandAssetUnits jobId="offline-1" />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('region', { name: '按检查项归类' })).toBeVisible()
    expect(screen.getByText('待归类检查项（1）')).toBeVisible()
    expect(screen.getAllByText('display vendor-special-state').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '在检查项目录归类' })).toBeVisible()
  })
})
