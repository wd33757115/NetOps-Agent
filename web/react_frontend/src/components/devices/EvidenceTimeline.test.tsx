import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import EvidenceTimeline from './EvidenceTimeline'
import type { DeviceEvidenceTimelineItem, DeviceExecutionPlanRecord } from '../../services/api'

const baseItem = (overrides: Partial<DeviceEvidenceTimelineItem> = {}): DeviceEvidenceTimelineItem => ({
  workflow_created_at: '2026-07-12T00:00:00Z',
  workflow_status: 'completed',
  snapshot_count: 1,
  observation_count: 1,
  change_count: 1,
  event_count: 1,
  parse_counts: { parsed: 1 },
  steps: [],
  snapshots: [
    {
      snapshot_id: 'snap-1',
      command: 'show version',
      parse_status: 'parsed',
      output_status: 'ok',
      fact_status: 'done',
      normalization_status: 'done',
      parser_template_id: 'tpl-1',
      observation_contract_id: 'contract-1',
      raw_text_hash: 'rawhash123',
      evidence_fingerprint: 'fp-snap-1',
      execution_status: 'done',
      observed_at: null,
    },
  ],
  observations: [
    {
      observation_id: 'obs-1',
      snapshot_id: 'snap-1',
      entity_type: 'interface',
      entity_key: 'Ethernet1/1',
      field: 'oper_status',
      value: 'up',
      value_text: 'up',
      value_number: null,
      confidence: 1,
    },
  ],
  changes: [
    {
      change_id: 'chg-1',
      entity_type: 'interface',
      entity_key: 'Ethernet1/1',
      field: 'oper_status',
      change_type: 'up_down',
      event_status: 'no_event',
      old_value: 'down',
      new_value: 'up',
      old_number: null,
      new_number: null,
      decision_reason: 'policy missing',
      comparison_type: 'neq',
      detected_at: null,
      no_event_reason: 'policy missing',
      change_policy_id: 'policy-1',
      change_policy_version_id: '1',
    },
  ],
  events: [
    {
      event_id: 'evt-1',
      event_type: 'InterfaceUp',
      severity: 'warning',
      entity_type: 'interface',
      entity_key: 'Ethernet1/1',
      status: 'created',
      new_value: 'up',
      old_value: 'down',
      source_change_id: 'chg-1',
      policy_snapshot: null,
      occurred_at: '2026-01-01T00:00:00Z',
      event_rule_id: 'rule-1',
      event_rule_version: 1,
      change_policy_id: 'policy-1',
      change_policy_version_id: 1,
    },
  ],
  event_occurrence_count: 2,
  ...overrides,
})

describe('EvidenceTimeline', () => {
  it('should expose policy/no_event/collision reasons separately and provide navigation callbacks', async () => {
    const navigate = vi.fn()
    const item = baseItem({
      workflow_run_id: 'wf-1',
      policy_blocking_reason: 'template_missing',
      policy_blocking_reasons: ['template_missing'],
      policy_blocking_count: 1,
      no_event_count: 1,
      changes: [
        ...[
          {
            change_id: 'chg-1',
            entity_type: 'interface',
            entity_key: 'Ethernet1/1',
            field: 'oper_status',
            change_type: 'up_down',
            event_status: 'no_event',
            old_value: 'down',
            new_value: 'up',
            old_number: null,
            new_number: null,
            decision_reason: 'policy missing',
            no_event_reason: 'policy missing',
            comparison_type: 'neq',
            detected_at: null,
            change_policy_id: 'policy-1',
            change_policy_version_id: '1',
          },
        ],
        {
          change_id: 'chg-2',
          entity_type: 'interface',
          entity_key: 'Ethernet1/2',
          field: 'oper_status',
          change_type: 'up_down',
          event_status: 'collision',
          old_value: 'down',
          new_value: 'up',
          old_number: null,
          new_number: null,
          collision_reason: 'version conflict',
          comparison_type: 'neq',
          detected_at: null,
          change_policy_id: 'policy-1',
          change_policy_version_id: '1',
        } as DeviceEvidenceTimelineItem['changes'][number],
      ],
    })

    render(
      <EvidenceTimeline
        items={[item]}
        executionPlans={[] as DeviceExecutionPlanRecord[]}
        onNavigate={navigate}
      />,
    )

    expect(screen.getByTestId('evidence-policy-blocking')).toHaveTextContent('策略阻塞：template_missing')
    expect(screen.getByText('未报事件：policy missing')).toBeInTheDocument()
    expect(screen.getByText('碰撞阻塞：version conflict')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '跳转到workflow-run' }))
    expect(navigate).toHaveBeenCalledWith('workflow-run', 'wf-1')

    const parserLink = screen.getByRole('button', { name: '跳转到parser-template' })
    await userEvent.click(parserLink)
    expect(navigate).toHaveBeenCalledWith('parser-template', 'tpl-1')

    await userEvent.click(screen.getAllByTestId('evidence-change-policy-id-1')[0])
    expect(navigate).toHaveBeenCalledWith('change-policy', '1')
  })

  it('should prevent duplicate key warnings when reasons repeat', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    const item = baseItem({
      workflow_run_id: 'wf-dup',
      no_event_count: 2,
      changes: [
        {
          change_id: 'chg-1',
          entity_type: 'interface',
          entity_key: 'Eth1',
          field: 'oper_status',
          change_type: 'up_down',
          event_status: 'no_event',
          old_value: 'down',
          new_value: 'up',
          old_number: null,
          new_number: null,
          no_event_reason: 'policy missing',
          decision_reason: 'policy missing',
          comparison_type: 'neq',
          detected_at: null,
        } as DeviceEvidenceTimelineItem['changes'][number],
        {
          change_id: 'chg-2',
          entity_type: 'interface',
          entity_key: 'Eth2',
          field: 'oper_status',
          change_type: 'up_down',
          event_status: 'no_event',
          old_value: 'down',
          new_value: 'up',
          old_number: null,
          new_number: null,
          no_event_reason: 'policy missing',
          decision_reason: 'policy missing',
          comparison_type: 'neq',
          detected_at: null,
        } as DeviceEvidenceTimelineItem['changes'][number],
      ],
    })

    render(
      <EvidenceTimeline
        items={[item]}
        executionPlans={[] as DeviceExecutionPlanRecord[]}
      />,
    )

    expect(screen.getAllByText('未报事件：policy missing')).toHaveLength(1)
    const hasDuplicateKeyWarning = consoleError.mock.calls.some((entry) => (
      String(entry[0]).includes('Encountered two children with the same key')
    ))
    expect(hasDuplicateKeyWarning).toBe(false)
    consoleError.mockRestore()
  })

  it('should expose unknown closure reason when events are no_event but reasons are empty', () => {
    const item = baseItem({
      no_event_count: 1,
      changes: [
        {
          change_id: 'chg-1',
          entity_type: 'interface',
          entity_key: 'Eth1',
          field: 'admin_status',
          change_type: 'admin_status',
          event_status: 'no_event',
          old_value: 'up',
          new_value: 'down',
          old_number: null,
          new_number: null,
          comparison_type: 'neq',
          detected_at: null,
        } as DeviceEvidenceTimelineItem['changes'][number],
      ],
      events: [],
      event_count: 0,
    })

    render(
      <EvidenceTimeline
        items={[item]}
        executionPlans={[] as DeviceExecutionPlanRecord[]}
      />,
    )

    expect(screen.getByText('未分类：存在未报事件但未输出具体原因')).toBeInTheDocument()
  })
})
