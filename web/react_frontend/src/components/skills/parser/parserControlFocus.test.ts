import { describe, expect, it } from 'vitest'
import {
  hasFocusMatched,
  parserWorkspaceTabs,
  resolveParserControlFocus,
} from './parserControlFocus'

const resolve = (query: string) => resolveParserControlFocus(new URLSearchParams(query))

describe('resolveParserControlFocus', () => {
  it('maps every governed asset focus to its canonical workspace', () => {
    expect(resolve('focus=parser-template&id=tpl-1')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
    })
    expect(resolve('focus=observation-contract&id=contract-1')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
    })
    expect(resolve('focus=change-policy&id=policy-1')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
    })
    expect(resolve('focus=event-rule&id=rule-1')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
    })
  })

  it('ignores a conflicting workspace instead of creating an impossible tab pair', () => {
    expect(resolve('focus=event-rule&id=rule-1&workspace=runtime&subtab=runtime-assets'))
      .toMatchObject({
        workspace: 'inventory',
        subWorkspace: 'published-assets',
        fallbackReason: 'workspace_mismatch',
      })
  })

  it('reports unsupported focus, missing id and invalid subtab fallbacks', () => {
    expect(resolve('focus=unknown&id=x')).toMatchObject({
      workspace: 'production',
      fallbackReason: 'unsupported_focus',
    })
    expect(resolve('focus=parser-template')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
      fallbackReason: 'missing_focus_id',
    })
    expect(resolve('workspace=audit&subtab=templates')).toMatchObject({
      workspace: 'audit',
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

describe('parser workspace navigation', () => {
  it('keeps offline evidence ingestion inside rule asset production', () => {
    expect(parserWorkspaceTabs.production).toEqual(['asset-production-units', 'source-jobs'])
    expect(resolve('')).toMatchObject({
      workspace: 'production',
      subWorkspace: null,
    })
    expect(resolve('workspace=production&subtab=source-jobs')).toMatchObject({
      workspace: 'production',
      subWorkspace: 'source-jobs',
    })
  })

  it('keeps governance evidence reachable from its product workspace', () => {
    expect(parserWorkspaceTabs.generation).toContain('rule-gaps')
    expect(parserWorkspaceTabs.generation).toContain('agent-loop-attempts')
    expect(parserWorkspaceTabs.release).toContain('replay-reports')
    expect(parserWorkspaceTabs.inventory).toEqual(['published-assets'])
    expect(parserWorkspaceTabs.runtime).toEqual(['coverage-backfill'])
    expect(parserWorkspaceTabs.audit).toEqual(['audit-log'])
  })

  it('maps legacy technical tabs into the new product pages', () => {
    expect(resolve('workspace=inventory&subtab=event-rules')).toMatchObject({
      workspace: 'inventory',
      subWorkspace: 'published-assets',
    })
    expect(resolve('workspace=runtime&subtab=asset-chain')).toMatchObject({
      workspace: 'runtime',
      subWorkspace: 'coverage-backfill',
    })
    expect(resolve('workspace=audit&subtab=approval-audit')).toMatchObject({
      workspace: 'audit',
      subWorkspace: 'audit-log',
    })
    expect(resolve('workspace=generation&subtab=rule-generation&command=display+power'))
      .toMatchObject({
        workspace: 'production',
        subWorkspace: 'asset-production-units',
        legacyRedirect: true,
        productAction: 'rule-generation',
        focusHint: 'display power',
      })
    expect(resolve('workspace=release&subtab=asset-bundles')).toMatchObject({
      workspace: 'production',
      subWorkspace: 'asset-production-units',
      legacyRedirect: true,
      productAction: 'bundle-review',
    })
    expect(resolve('workspace=inbox&subtab=backfills')).toMatchObject({
      workspace: 'runtime',
      subWorkspace: 'coverage-backfill',
      legacyRedirect: true,
      productAction: 'backfill',
    })
  })
})
