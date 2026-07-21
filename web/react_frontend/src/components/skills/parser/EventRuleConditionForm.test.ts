import { describe, expect, it } from 'vitest'
import { buildEventRuleCondition, type EventRuleConditionDraft } from './EventRuleConditionForm'

const draft = (overrides: Partial<EventRuleConditionDraft> = {}): EventRuleConditionDraft => ({
  observationType: 'interface.optical.rx_power',
  entityType: 'interface_optical',
  field: 'rx_power_dbm',
  kind: 'threshold_field_lte',
  threshold: -17,
  hysteresis: 2,
  referenceField: 'rx_low_warning_dbm',
  fromGroup: 'up',
  toGroup: 'down',
  ...overrides,
})

describe('buildEventRuleCondition', () => {
  it('builds a device-threshold condition with exact observation coverage', () => {
    expect(buildEventRuleCondition(draft())).toEqual({
      kind: 'threshold_field_lte',
      observation_types: ['interface.optical.rx_power'],
      entity_types: ['interface_optical'],
      fields: ['rx_power_dbm'],
      reference_field: 'rx_low_warning_dbm',
    })
  })

  it('builds a transition without allowing an initial false recovery', () => {
    expect(buildEventRuleCondition(draft({
      observationType: 'interface.oper_status',
      entityType: 'interface_l2',
      field: 'status',
      kind: 'transition',
    }))).toMatchObject({
      kind: 'transition',
      observation_types: ['interface.oper_status'],
      from_group: 'up',
      to_group: 'down',
      allow_initial_target: false,
    })
  })
})
