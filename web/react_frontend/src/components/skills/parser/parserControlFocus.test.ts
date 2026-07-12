import { describe, expect, it } from 'vitest'
import { hasFocusMatched, resolveParserControlFocus } from './parserControlFocus'

const resolve = (query: string) => resolveParserControlFocus(new URLSearchParams(query))

describe('resolveParserControlFocus', () => {
  it('maps every governed asset focus to its canonical workspace', () => {
    expect(resolve('focus=parser-template&id=tpl-1')).toMatchObject({
      workspace: 'parser-assets',
      subWorkspace: 'templates',
    })
    expect(resolve('focus=observation-contract&id=contract-1')).toMatchObject({
      workspace: 'rule-assets',
      subWorkspace: 'observation-contracts',
    })
    expect(resolve('focus=change-policy&id=policy-1')).toMatchObject({
      workspace: 'rule-assets',
      subWorkspace: 'change-policies',
    })
    expect(resolve('focus=event-rule&id=rule-1')).toMatchObject({
      workspace: 'rule-assets',
      subWorkspace: 'event-rules',
    })
  })

  it('ignores a conflicting workspace instead of creating an impossible tab pair', () => {
    expect(resolve('focus=event-rule&id=rule-1&workspace=parser-assets&subtab=templates'))
      .toMatchObject({
        workspace: 'rule-assets',
        subWorkspace: 'event-rules',
        fallbackReason: 'workspace_mismatch',
      })
  })

  it('reports unsupported focus, missing id and invalid subtab fallbacks', () => {
    expect(resolve('focus=unknown&id=x')).toMatchObject({
      workspace: 'today',
      fallbackReason: 'unsupported_focus',
    })
    expect(resolve('focus=parser-template')).toMatchObject({
      workspace: 'parser-assets',
      subWorkspace: 'templates',
      fallbackReason: 'missing_focus_id',
    })
    expect(resolve('workspace=governance&subtab=templates')).toMatchObject({
      workspace: 'governance',
      subWorkspace: null,
      fallbackReason: 'invalid_subtab',
    })
  })

  it('explains indirect snapshot and change focus semantics', () => {
    expect(resolve('focus=snapshot&id=snapshot-1').focusHint).toContain('parser_template_id')
    expect(resolve('focus=change&id=change-1').focusHint).toContain('change_policy_id')
  })
})

describe('hasFocusMatched', () => {
  it('matches ids across asset version aliases', () => {
    const focus = resolve('focus=change-policy&id=policy-version-1')
    expect(hasFocusMatched(focus, { versionId: 'policy-version-1' })).toBe(true)
    expect(hasFocusMatched(focus, { policyId: 'policy-1' })).toBe(false)
  })
})
