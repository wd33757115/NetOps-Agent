import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parserApi } from '../../../services/api'
import RuleGapsWorkspace from './RuleGapsWorkspace'

const LocationProbe = () => {
  const location = useLocation()
  return <output aria-label="current route">{`${location.pathname}${location.search}`}</output>
}

const renderWorkspace = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={['/skills/parser?workspace=runtime&subtab=rule-gaps']}>
      <QueryClientProvider client={client}>
        <RuleGapsWorkspace />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('RuleGapsWorkspace', () => {
  afterEach(() => vi.clearAllMocks())

  it('opens a production gap in the exact evidence-import scope without a generation action', async () => {
    vi.spyOn(parserApi, 'assetGaps').mockResolvedValue({
      items: [{
        id: 'gap-1',
        patrol_batch_id: 'patrol-batch-1',
        check_item_id: 'system.cpu_usage',
        implementation_version_id: 'impl-h3c-cpu-v1',
        canonical_command: 'display cpu-usage',
        gap_type: 'runtime_bundle_missing',
        purpose: 'production_patrol',
        status: 'open',
        reason_code: 'active_runtime_bundle_missing',
        occurrence_count: 12,
        first_seen_at: '2026-07-30T08:00:00Z',
        last_seen_at: '2026-07-31T08:00:00Z',
        recommended_action: {
          code: 'import_rule_samples',
          label: '导入规则样本',
          description: '只导入并确认证据。',
          target_url: (
            '/skills/parser?workspace=production&subtab=source-jobs'
            + '&action=asset-gap-source&asset_gap_id=gap-1'
            + '&check_item_id=system.cpu_usage'
            + '&implementation_version_id=impl-h3c-cpu-v1'
            + '&command=display+cpu-usage'
          ),
        },
      }],
      total: 1,
      limit: 20,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleGapsFromChanges').mockResolvedValue({
      days: 90,
      gaps: [],
      by_reason: {},
    })
    const user = userEvent.setup()

    renderWorkspace()

    expect(await screen.findByText('system.cpu_usage')).toBeInTheDocument()
    expect(screen.getByText('12 次')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /生成/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /导入规则样本/ }))

    expect(screen.getByLabelText('current route')).toHaveTextContent(
      '/skills/parser?workspace=production&subtab=source-jobs'
      + '&action=asset-gap-source&asset_gap_id=gap-1'
      + '&check_item_id=system.cpu_usage'
      + '&implementation_version_id=impl-h3c-cpu-v1'
      + '&command=display+cpu-usage',
    )
  })

  it('keeps historical change gaps as evidence-locating work, not direct job seeding', async () => {
    vi.spyOn(parserApi, 'assetGaps').mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleGapsFromChanges').mockResolvedValue({
      days: 90,
      by_reason: { event_rule_missing: 4 },
      gaps: [{
        observation_type: 'interface.oper_status',
        command: 'display interface brief',
        vendor: 'H3C',
        gap_kind: 'event_rule_missing',
        change_count: 4,
        suggested_job_type: 'EVENT_RULE',
        top_reason: 'event_rule_missing',
      }],
    })
    const user = userEvent.setup()

    renderWorkspace()
    await user.click(screen.getByRole('tab', { name: '变化事件缺口' }))

    expect(await screen.findByText('interface.oper_status')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导入离线样本/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /seed/i })).not.toBeInTheDocument()
  })
})
