import React from 'react'
import { render, screen } from '@testing-library/react'
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
  it('explains a blocked Agent loop and preserves attempt evidence', async () => {
    render(
      <ParserJobDetailDrawer
        open
        onClose={vi.fn()}
        isMobile={false}
        loading={false}
        job={blockedJob}
      />,
    )

    expect(await screen.findByText(/Parser Agent 已阻断/)).toBeVisible()
    expect(screen.getByText('生成与验证时间线')).toBeVisible()
    expect(screen.getByText('第 1 轮')).toBeVisible()
    expect(screen.getByText('确定性验证')).toBeVisible()
    expect(screen.getByText('20 / 24000')).toBeVisible()
    expect(screen.getByText('300 秒')).toBeVisible()
    expect(screen.getByText(/deepseek-chat.*45 ms.*20 tokens/)).toBeVisible()
    expect(screen.getByText(/missing_entities：样本中的接口未被完整解析/)).toBeVisible()
  })
})
