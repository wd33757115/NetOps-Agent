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
      completeness_assessment: {
        passed: true,
        reason: '所有可见接口行均已解析。',
      },
    },
    simple_parser: {
      validation_mode: 'generation_sample_only',
      hard_gates: {
        compiled: true,
        primary_keys_valid: true,
      },
    },
  },
  capability_claim: 'Parser approval proves structured extraction only.',
  check_item_capabilities: [
    {
      schema_version: 'parser-check-item-capability/v1',
      check_item_id: 'interface.oper_status',
      check_item_version: 4,
      status: 'mapping_unverified',
      shape_assessment: {
        status: 'compatible',
        observed_roles: ['identity', 'state'],
        missing_required_roles: [],
      },
      target: {
        name: 'Interface operational status',
        required_semantic_roles: ['state'],
        required_observation_types: ['interface.oper_status'],
      },
      canonical_mapping: {
        status: 'mapping_unverified',
        mapped_observation_types: [],
        missing_required_observation_types: ['interface.oper_status'],
      },
      blockers: [
        {
          code: 'observation_contract_mapping_required',
          message: 'ObservationContract mapping is required',
        },
      ],
      claim_boundary: {
        parser_structure_proven: true,
        canonical_mapping_proven: false,
        check_item_runtime_ready: false,
      },
    },
  ],
  active_contract: {},
  parser_field_contract: {
    schema_version: 'parser-field-contract/v1',
    status: 'aligned',
    aligned: true,
    parser_spec_record_sets: { default: ['interface'] },
    template_record_sets: { default: ['interface'] },
    differences: [],
    errors: [],
  },
  review_readiness: { can_review: true, blockers: [] },
  semantic_review: null,
  activation_readiness: {
    can_activate: false,
    blockers: ['human semantic review has not been accepted'],
  },
  approval_readiness: {
    can_approve: false,
    blockers: ['human semantic review has not been accepted'],
  },
  rows: [
    {
      snapshot_id: 'snapshot-1',
      dataset_id: 'dataset-q3',
      device_id: 'SW1',
      command: 'display interface brief',
      raw_text: 'Interface Link\nGE1/0/1 UP',
      raw_text_sha256: 'b'.repeat(64),
      changed: true,
      current: {
        status: 'success',
        records: [{ interface: 'GE1/0/1', status: 'DOWN' }],
      },
      proposed: {
        status: 'success',
        records: [{ interface: 'GE1/0/1', status: 'UP' }],
      },
    },
  ],
}

describe('ParserTemplateApprovalDrawer', () => {
  it('shows frozen evidence without lifecycle actions in read-only mode', async () => {
    render(
      <ParserTemplateApprovalDrawer
        open
        readOnly
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={comparison}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('查看 Parser 审批证据')).toBeVisible()
    expect(screen.getByText('已发布 Parser')).toBeVisible()
    expect(screen.getByText('CLI 输出样本')).toBeVisible()
    expect(screen.getByText('原始快照')).toBeVisible()
    expect(screen.getAllByText('TextFSM 模板')[0]).toBeVisible()
    expect(screen.getAllByText('解析结果')[0]).toBeVisible()
    expect(screen.getByText(/GE1\/0\/1 UP/)).toBeVisible()
    expect(screen.getByText(/Value interface/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /保存人工复核/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /批准发布/ })).not.toBeInTheDocument()
  })

  it('shows raw CLI, parsed records and the exact TextFSM candidate before approval', async () => {
    const user = userEvent.setup()
    const onReview = vi.fn()
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={comparison}
        onReview={onReview}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('CLI 输出样本')).toBeVisible()
    expect(screen.getByText('候选已通过自动校验，等待工程师复核最终结果')).toBeVisible()
    expect(screen.getByText(/GE1\/0\/1 UP/)).toBeVisible()
    expect(screen.getAllByText('TextFSM 模板')[0]).toBeVisible()
    expect(screen.getAllByText('解析结果')[0]).toBeVisible()
    expect(screen.getByText(/Value interface/)).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'TextFSM 模板' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('单条解析记录')).toHaveTextContent('statusUP')

    await user.click(screen.getByRole('checkbox', { name: /记录完整且字段对应正确/ }))
    await user.click(screen.getByRole('checkbox', { name: /能稳定、唯一地标识记录/ }))

    await user.click(screen.getByRole('button', { name: /保存人工复核/ }))
    expect(onReview).toHaveBeenCalledWith('')
  })

  it('separates Parser approval from check-item and RuntimeBundle readiness', async () => {
    const user = userEvent.setup()
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={comparison}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '能力边界 (1)' }))
    expect(await screen.findByText('Parser 可审批不等于检查项已具备生产能力')).toBeVisible()
    expect(screen.getByText(/字段形态可承载目标语义/)).toBeVisible()
    expect(screen.getAllByText('interface.oper_status')).not.toHaveLength(0)
    expect(screen.getByText('ParserSpec 与 TextFSM 输出字段一致')).toBeVisible()
    expect(screen.getByText('检查项未就绪')).toBeVisible()
    expect(screen.getByText('observation_contract_mapping_required')).toBeVisible()
    expect(screen.getByText('ObservationContract mapping is required')).toBeVisible()
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
          review_readiness: {
            can_review: false,
            blockers: ['候选模板仅通过 0/1 个解析验证样本'],
          },
          approval_readiness: {
            can_approve: false,
            blockers: ['候选模板仅通过 0/1 个解析验证样本'],
          },
        }}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('候选模板暂不可复核')).toBeVisible()
    expect(screen.getByText(/候选模板仅通过 0\/1/)).toBeVisible()
    expect(screen.getByRole('button', { name: /保存人工复核/ })).toBeDisabled()
  })

  it('keeps release separate after semantic review without forcing a narrative note', async () => {
    const user = userEvent.setup()
    const onApprove = vi.fn()
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={{
          ...comparison,
          semantic_review: {
            schema_version: 'parser-human-semantic-review/v1',
            status: 'accepted',
            review_mode: 'human_after_ai',
            reviewed_by: 'review-engineer',
            reviewed_at: '2026-07-30T14:00:00Z',
            reason: '逐样本确认解析结果与主键',
            result_confirmed: true,
            primary_key_confirmed: true,
            evidence_digest: 'e'.repeat(64),
          },
          activation_readiness: { can_activate: true, blockers: [] },
          approval_readiness: { can_approve: true, blockers: [] },
          release_actor: {
            username: 'review-engineer',
            role: 'admin',
            same_as_semantic_reviewer: true,
            admin_override_applies: true,
            can_activate: true,
            blockers: [],
          },
        }}
        onReview={vi.fn()}
        onApprove={onApprove}
      />,
    )

    expect(await screen.findByText('人工语义复核已保存，等待独立发布审批')).toBeVisible()
    expect(screen.getByText('review-engineer')).toBeVisible()
    expect(screen.getByText('管理员代行发布审批')).toBeVisible()
    expect(screen.getByRole('button', { name: /批准发布/ })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: /批准发布/ }))

    expect(onApprove).toHaveBeenCalledWith('')
  })

  it('prevents the semantic reviewer from publishing when the account is not admin', async () => {
    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={{
          ...comparison,
          semantic_review: {
            schema_version: 'parser-human-semantic-review/v1',
            status: 'accepted',
            review_mode: 'human_after_ai',
            reviewed_by: 'review-engineer',
            reviewed_at: '2026-07-30T14:00:00Z',
            reason: '逐样本确认解析结果与主键',
            result_confirmed: true,
            primary_key_confirmed: true,
            evidence_digest: 'e'.repeat(64),
          },
          release_actor: {
            username: 'review-engineer',
            role: 'operator',
            same_as_semantic_reviewer: true,
            admin_override_applies: false,
            can_activate: false,
            blockers: ['当前账号已完成语义复核，发布必须由其他审批人执行'],
          },
          activation_readiness: {
            can_activate: false,
            blockers: ['当前账号已完成语义复核，发布必须由其他审批人执行'],
          },
          approval_readiness: {
            can_approve: false,
            blockers: ['当前账号已完成语义复核，发布必须由其他审批人执行'],
          },
        }}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('当前账号不能批准发布')).toBeVisible()
    expect(screen.getByText(/请切换其他具备发布权限的账号/)).toBeVisible()
    expect(screen.getByRole('button', { name: /批准发布/ })).toBeDisabled()
  })

  it('shows immutable NTC source and compatibility transformations beside the candidate', async () => {
    const user = userEvent.setup()
    const ntcComparison: ParserTemplateComparison = {
      ...comparison,
      candidate_template: {
        ...comparison.candidate_template,
        content: 'Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n  ^.\n',
      },
      candidate_contract: {
        ...comparison.candidate_contract,
        candidate_provenance: {
          provider: 'ntc_templates',
          package_version: '9.1.0',
          source_template: 'cisco_nxos_show_interface_status.textfsm',
          source_template_text: 'Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n  ^. -> Error\n',
          source_content_sha256: 'c'.repeat(64),
          content_sha256: 'd'.repeat(64),
          license: 'Apache-2.0',
          platform: 'cisco_nxos',
          platform_mapping_version: 1,
          transformation_version: 'ntc-platform-compat/v1',
          transformations: [
            {
              kind: 'catch_all_error_to_ignore',
              line_number: 5,
              source: '^. -> Error',
              derived: '^.',
            },
          ],
        },
      },
    }

    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={ntcComparison}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    expect(await screen.findByText('NTC 公共模板')).toBeVisible()
    expect(screen.getAllByText('TextFSM 模板')[0]).toBeVisible()
    expect(screen.getByText(/Value interface/)).toBeVisible()
    await user.click(screen.getByRole('tab', { name: '过程资产' }))

    expect(screen.getByText('NTC 模板来源')).toBeVisible()
    expect(screen.getAllByText(/cisco_nxos_show_interface_status\.textfsm/)).not.toHaveLength(0)
    expect(screen.getByText('Apache-2.0')).toBeVisible()
    expect(screen.getByText('ntc-platform-compat/v1')).toBeVisible()
    expect(screen.getByText('NTC 原始模板')).toBeVisible()
    expect(screen.getAllByText(/\^\. -> Error/)).not.toHaveLength(0)
  })

  it('lets the reviewer inspect every record set in a ParserBundle', async () => {
    const user = userEvent.setup()
    const bundleComparison: ParserTemplateComparison = {
      ...comparison,
      candidate_template: {
        ...comparison.candidate_template,
        category: 'interface_transceiver',
        content: JSON.stringify({
          format: 'netops.parser_bundle/v1',
          record_sets: [
            {
              name: 'interface_transceiver_status',
              template_text: 'Value interface_name (\\S+)\n\nStart\n  ^${interface_name} -> Record\n',
            },
            {
              name: 'interface_diagnostic_channel',
              template_text: 'Value interface_name (\\S+)\nValue channel_number (\\d+)\n\nStart\n  ^${interface_name} ${channel_number} -> Record\n',
            },
          ],
        }),
      },
      candidate_contract: {
        ...comparison.candidate_contract,
        primary_keys: [],
        record_sets: [
          {
            name: 'interface_transceiver_status',
            entity_type: 'interface_transceiver',
            primary_keys: ['interface_name'],
            required_fields: ['interface_name', 'diagnostic_status'],
          },
          {
            name: 'interface_diagnostic_channel',
            entity_type: 'interface_optical_channel',
            primary_keys: ['interface_name', 'channel_number'],
            required_fields: ['interface_name', 'channel_number', 'rx_power_dbm'],
          },
        ],
      },
      rows: [{
        ...comparison.rows[0],
        proposed: {
          status: 'success',
          record_count: 3,
          record_sets: [
            {
              name: 'interface_transceiver_status',
              entity_type: 'interface_transceiver',
              primary_keys: ['interface_name'],
              records: [
                { interface_name: 'FortyGigE1/0/49', diagnostic_status: 'available' },
              ],
            },
            {
              name: 'interface_diagnostic_channel',
              entity_type: 'interface_optical_channel',
              primary_keys: ['interface_name', 'channel_number'],
              records: [
                { interface_name: 'FortyGigE1/0/49', channel_number: 1, rx_power_dbm: -3.21 },
                { interface_name: 'FortyGigE1/0/49', channel_number: 2, rx_power_dbm: -3.45 },
              ],
            },
          ],
        },
      }],
    }

    render(
      <ParserTemplateApprovalDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        comparison={bundleComparison}
        onReview={vi.fn()}
        onApprove={vi.fn()}
      />,
    )

    expect((await screen.findAllByText('interface_transceiver_status'))[0]).toBeVisible()
    expect(screen.getByRole('definition', { name: 'available' })).toBeVisible()

    expect(screen.queryByRole('combobox', { name: '解析记录集' })).not.toBeInTheDocument()
    expect((await screen.findAllByText('interface_diagnostic_channel'))[0]).toBeVisible()
    expect(screen.getByText(/主键 interface_name \+ channel_number/)).toBeVisible()
    expect(screen.getByRole('cell', { name: '-3.21' })).toBeVisible()
    expect(screen.getByRole('cell', { name: '-3.45' })).toBeVisible()
    expect(screen.getByText(/Value channel_number/)).toBeVisible()
  })
})

