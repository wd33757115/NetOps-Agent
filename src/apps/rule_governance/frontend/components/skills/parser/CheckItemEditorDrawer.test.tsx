import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ParserCheckItem } from '@netops/services/api'
import CheckItemEditorDrawer from './CheckItemEditorDrawer'

const checkItem: ParserCheckItem = {
  check_item_id: 'environment.fan',
  check_item_version_id: 'environment-fan-v3',
  version: 3,
  name: 'Fan health',
  category: 'environment',
  description: 'Evaluate fan presence and operating state.',
  result_mode: 'structured',
  assessment_mode: 'health',
  canonical_contract: {
    entity_type: 'environment_fan',
    identity_dimensions: ['slot', 'fan'],
    observations: [{
      observation_type: 'environment.fan.state',
      semantic_type: 'state',
      canonical_field: 'fan_status',
    }],
    parser_field_vocabulary: {
      fan: { aliases: ['fan_id', 'fan_name'], role: 'identity' },
      fan_status: {
        aliases: ['status', 'state'],
        observation_type: 'environment.fan.state',
        role: 'state',
      },
    },
  },
  report_projection: 'fan',
  applicability: { sites: ['dc-a'] },
  freshness_policy: {
    max_observation_age: '24h',
    max_collection_span: '8h',
  },
  empty_result_policy: { classification: 'not_applicable' },
  coverage_policy: {
    model: 'derived_entity_set',
    source: 'previous_qualified_observation',
    first_observation: 'unable_to_determine',
  },
  status: 'active',
  digest: 'a'.repeat(64),
  implementation_count: 1,
  implementations: [],
}

describe('CheckItemEditorDrawer', () => {
  it('preserves advanced contract fields when they remain collapsed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <CheckItemEditorDrawer
        open
        mobile={false}
        checkItem={checkItem}
        canonicalCatalog={{ version: 1, items: [{
          id: 'environment.fan.state',
          description: 'Current fan state',
          semantic_type: 'state',
          value_types: ['text'],
          dimensions: [],
          assessment_roles: ['current_state'],
        }] }}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    await user.click(screen.getByRole('button', { name: /保存草稿并预览影响/ }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.canonical_contract.parser_field_vocabulary).toMatchObject({
      fan: { aliases: ['fan_id', 'fan_name'], role: 'identity' },
      slot: { role: 'identity' },
      fan_status: {
        aliases: ['status', 'state'],
        observation_type: 'environment.fan.state',
        role: 'state',
        value_type: 'state',
      },
    })
    expect(payload.applicability).toEqual(checkItem.applicability)
  })
})
