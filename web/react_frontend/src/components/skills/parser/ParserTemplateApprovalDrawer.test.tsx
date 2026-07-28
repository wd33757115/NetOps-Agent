import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ParserTemplateComparison } from '../../../services/api'
import ParserTemplateApprovalDrawer from './ParserTemplateApprovalDrawer'

const comparison: ParserTemplateComparison = {
  template_id: 'template-1',
  active_template_id: 'template-active',
  sample_count: 1,
  passed_samples: 1,
  candidate_contract_sha256: 'contract-new',
  active_contract_sha256: 'contract-old',
  contract_changed: true,
  candidate_template: {
    id: 'template-1',
    vendor: 'H3C',
    profile: 'S5130S',
    canonical_command: 'display interface brief',
    category: 'interface',
    version: 'v2',
    status: 'pending_approval',
    sha256: 'a'.repeat(64),
    hash_valid: true,
    content: 'Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n',
    validation: {},
  },
  candidate_contract: {
    primary_keys: ['interface'],
    ai_review: {
      passed: true,
      summary: '记录完整，主键稳定。',
      primary_key_assessment: { passed: true, reason: '接口名唯一且稳定。' },
      completeness_assessment: { passed: true, reason: '所有可见接口行均已解析。' },
    },
    simple_parser: {
      validation_mode: 'generation_sample_only',
      hard_gates: {
        compiled: true,
        primary_keys_valid: true,
      },
    },
  },
  active_contract: {},
  approval_readiness: { can_approve: true, blockers: [] },
  rows: [{
    snapshot_id: 'snapshot-1',
    dataset_id: 'dataset-q3',
    device_id: 'SW1',
    command: 'display interface brief',
    raw_text: 'Interface Link\nGE1/0/1 UP',
    raw_text_sha256: 'b'.repeat(64),
    changed: true,
    current: { status: 'success', records: [{ interface: 'GE1/0/1', status: 'DOWN' }] },
    proposed: { status: 'success', records: [{ interface: 'GE1/0/1', status: 'UP' }] },
  }],
}

describe('ParserTemplateApprovalDrawer', () => {
  it('shows raw CLI, parsed records and the exact TextFSM candidate before approval', async () => {
    const user = userEvent.setup()
    const onApprove = vi.fn()
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={comparison}
        onApprove={onApprove}
      />,
    )

    expect(await screen.findByText('命令样本原始数据')).toBeVisible()
    expect(screen.getByText('候选已通过自动校验，等待确认最终结果')).toBeVisible()
    expect(screen.getByText(/GE1\/0\/1 UP/)).toBeVisible()
    expect(screen.getByText('候选模板解析结果')).toBeVisible()
    expect(screen.getByText(/"status": "UP"/)).toBeVisible()

    await user.click(screen.getByRole('checkbox', { name: /记录完整且字段对应正确/ }))
    await user.click(screen.getByRole('checkbox', { name: /能稳定、唯一地标识记录/ }))
    await user.click(screen.getByRole('tab', { name: 'TextFSM 模板' }))
    expect(screen.getByText(/Value interface/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: /确认并发布/ }))
    expect(onApprove).toHaveBeenCalledTimes(1)
  })

  it('blocks activation when approval evidence is incomplete', async () => {
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile
        loading={false}
        comparison={{
          ...comparison,
          approval_readiness: { can_approve: false, blockers: ['候选模板仅通过 0/1 个解析验证样本'] },
        }}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('候选模板暂不可发布')).toBeVisible()
    expect(screen.getByText(/候选模板仅通过 0\/1/)).toBeVisible()
    expect(screen.getByRole('button', { name: /确认并发布/ })).toBeDisabled()
  })
})
