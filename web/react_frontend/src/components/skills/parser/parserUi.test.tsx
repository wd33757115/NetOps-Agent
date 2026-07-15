import { describe, expect, it } from 'vitest'

import { governanceAlertLabel } from './parserUi'

describe('governanceAlertLabel', () => {
  it('translates known governance alerts for the operator control plane', () => {
    expect(governanceAlertLabel('rule_replay_collision', 'fallback')).toBe(
      '规则历史回放发现碰撞，审批发布已阻断',
    )
  })

  it('keeps an unknown backend message for forward compatibility', () => {
    expect(governanceAlertLabel('new_alert', 'New alert')).toBe('New alert')
  })
})
