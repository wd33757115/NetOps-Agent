import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ParserJobItem } from '../../../services/api'
import { ParserJobDetailDrawer } from './ParserDetailDrawers'

const blockedJob: ParserJobItem = {
  id: 'job-1',
  job_type: 'CREATE_TEMPLATE',
  vendor: 'H3C',
  model: 'S6860-54HF',
  profile: 'S6860-54HF',
  canonical_command: 'display interface brief',
  status: 'failed',
  source_mode: 'parser_validation',
  sample_count: 4,
  attempts: 1,
  agent_state_json: {
    status: 'blocked',
    stop_reason: 'validation_stagnated',
    token_usage_total: 20,
  },
  agent_budget_json: {
    token_budget: 24000,
    time_budget_seconds: 300,
  },
  attempt_summary: {
    total: 1,
    contract_revisions: 0,
    template_attempts: 1,
  },
  generation_attempts: [
    {
      id: 'attempt-1',
      job_id: 'job-1',
      attempt_number: 1,
      stage: 'validation',
      action: 'repair_entity_anchor',
      model_provider: 'openai-compatible',
      model_name: 'deepseek-chat',
      textfsm_runtime_version: '2.1.0',
      dialect_version: 'textfsm-2.1.0-netops-v1',
      dialect_hash: 'dialect-hash',
      prompt_version: 'textfsm-generator-v1',
      validation_report_json: {
        diagnostics: [
          {
            code: 'missing_entities',
            message: '样本中的接口未被完整解析',
          },
        ],
      },
      status: 'failed',
      token_usage_json: {
        input_tokens: 12,
        output_tokens: 8,
        total_tokens: 20,
      },
      latency_ms: 45,
      created_at: '2026-07-13T12:00:00Z',
    },
  ],
  created_at: '2026-07-13T11:59:00Z',
  updated_at: '2026-07-13T12:00:00Z',
}

describe('ParserJobDetailDrawer', () => {
  it('counts and labels bounded DeepSeek tool-loop rounds', async () => {
    const toolLoopJob: ParserJobItem = {
      ...blockedJob,
      pipeline_version: 'simple_v2',
      model_calls_used: 2,
      generation_attempts: [
        {
          ...blockedJob.generation_attempts![0],
          action: 'llm_tool_round_1',
          stage: 'template',
        },
      ],
    }
    render(
      <ParserJobDetailDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        job={toolLoopJob}
      />,
    )

    expect(await screen.findByText('2 / 3')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: '生成记录 (1)' }))
    expect(screen.getByText('LLM 生成与本地校验')).toBeVisible()
  })

  it('marks a legacy Agent run as history and preserves attempt evidence', async () => {
    render(
      <ParserJobDetailDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        job={blockedJob}
      />,
    )

    expect(await screen.findByText(/历史 Parser Agent 任务已终止/)).toBeVisible()
    expect(screen.getByText('20 / 24000')).toBeVisible()
    expect(screen.getByText('300 秒')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: '生成记录 (1)' }))
    expect(screen.getByText('第 1 轮')).toBeVisible()
    expect(screen.getByText('确定性验证')).toBeVisible()
    expect(screen.getByText(/deepseek-chat.*45 ms.*20 tokens/)).toBeVisible()
    expect(screen.getByText(/missing_entities：样本中的接口未被完整解析/)).toBeVisible()
  })

  it('lets an engineer confirm ParserSpec while dispatch stays gated', async () => {
    const onSaveSpec = vi.fn().mockResolvedValue({ success: true })
    const simpleJob: ParserJobItem = {
      ...blockedJob,
      id: 'simple-job',
      status: 'collecting',
      pipeline_version: 'simple_v2',
      parser_spec_status: 'draft',
      parser_spec_json: {
        entity: 'interface',
        primary_keys: ['interface'],
        fields: {
          interface: { required: true, type: 'interface_name' },
        },
      },
      generation_readiness: {
        ready: false,
        blockers: ['parser_spec_not_confirmed'],
        warnings: ['independent_acceptance_missing'],
        validation_mode: 'generation_sample_only',
        role_counts: { generation: 1, acceptance: 0, unassigned: 1 },
      },
      agent_state_json: undefined,
      generation_attempts: [],
    }
    render(
      <ParserJobDetailDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        job={simpleJob}
        onSaveSpec={onSaveSpec}
        onDispatch={vi.fn().mockResolvedValue({ success: true })}
      />,
    )

    expect(await screen.findByText(/ParserSpec 尚未确认/)).toBeVisible()
    expect(screen.getByRole('button', { name: '调度生成' })).toBeDisabled()

    await userEvent.click(screen.getByRole('tab', { name: 'ParserSpec' }))
    fireEvent.change(screen.getByLabelText('ParserSpec 变更原因'), {
      target: { value: '字段与主键已核对' },
    })
    await userEvent.click(screen.getByRole('button', { name: '确认 ParserSpec' }))

    await waitFor(() => expect(onSaveSpec).toHaveBeenCalledWith(
      'simple-job',
      simpleJob.parser_spec_json,
      true,
      '字段与主键已核对',
    ))
  })

  it('shows sample evidence instead of using the dataset id as the sample label', async () => {
    const onSuggestSpec = vi.fn().mockResolvedValue({ success: true })
    const job: ParserJobItem = {
      ...blockedJob,
      id: 'sample-job',
      status: 'collecting',
      pipeline_version: 'simple_v2',
      parser_spec_status: 'missing',
      samples: [
        {
          id: 'sample-1',
          snapshot_id: 'snapshot-1',
          snapshot_source: 'postgresql',
          dataset_id: 'offline-internal-sample',
          device_id: 'device-1',
          output_signature: 'signature-1',
          sample_role: 'unassigned',
          sanitization_status: 'sanitized',
          sanitized_text: 'M-LAG is not configured.',
          sanitized_preview: 'M-LAG is not configured.',
          content_classification: {
            status: 'valid_empty',
            confidence: 1,
            reason: 'not configured',
            recommended_role: 'negative',
          },
          source_context: {
            device_name: 'XA-SW-01',
            management_ip: '198.51.2.29',
            model: 'S5130S-54S-HI-G',
            source_file_name: 'XA-SW-01.txt',
            observed_at: '2026-07-01T00:00:00+08:00',
          },
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
    }

    render(
      <ParserJobDetailDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        job={job}
        onSuggestSpec={onSuggestSpec}
      />,
    )

    await userEvent.click(screen.getAllByRole('tab')[2])
    expect(screen.getByText('XA-SW-01')).toBeVisible()
    expect(screen.getByText('XA-SW-01.txt')).toBeVisible()
    expect(screen.getByText('合法空输出')).toBeVisible()
    expect(screen.queryByText('offline-internal-sample')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /确\s*认/ }))
    const roleDrawer = screen.getByText('配置样本角色').closest('.ant-drawer-content') as HTMLElement
    expect(roleDrawer).toBeVisible()
    expect(document.querySelector('.ant-modal')).not.toBeInTheDocument()
    await userEvent.click(within(roleDrawer).getByRole('button', { name: /取\s*消/ }))

    await userEvent.click(screen.getByRole('button', { name: '查看样本内容' }))
    const evidenceDrawer = screen.getByText('样本证据').closest('.ant-drawer-content') as HTMLElement
    expect(evidenceDrawer).toBeVisible()
    expect(within(evidenceDrawer).getByText('offline-internal-sample')).toBeVisible()
    expect(document.querySelector('.ant-modal')).not.toBeInTheDocument()
    await userEvent.click(within(evidenceDrawer).getByRole('button', { name: 'Close' }))

    await userEvent.click(screen.getAllByRole('tab')[1])
    await userEvent.click(screen.getByRole('button', { name: /生成 ParserSpec 草稿/ }))
    expect(onSuggestSpec).toHaveBeenCalledWith('sample-job', '')
  })
})
