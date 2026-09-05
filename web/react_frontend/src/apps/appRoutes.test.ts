import { describe, expect, it, vi } from 'vitest'
import { businessHref } from './appRoutes'

describe('business route compatibility', () => {
  it('preserves evidence query and hash through patrol redirects', () => {
    expect(
      businessHref(
        '/devices?tab=patrol&patrol_batch_id=b1&patrol_view=offline#result',
      ),
    ).toBe('/apps/patrol?patrol_batch_id=b1&patrol_view=offline#result')
    expect(businessHref('/devices?tab=overview')).toBe('/devices?tab=overview')
  })
  it('preserves source job and legacy focus links', () => {
    expect(
      businessHref('/skills/parser/source-jobs/job-1?tab=sources#files'),
    ).toBe('/apps/patrol-rules/source-jobs/job-1?tab=sources#files')
    expect(
      businessHref('/skills/parser?workspace=inventory&focus=event-rule&id=e1'),
    ).toBe('/apps/patrol-rules?workspace=inventory&focus=event-rule&id=e1')
  })
  it('supports turning off the workbench without redirect loops', async () => {
    vi.stubEnv('VITE_WORKBENCH_ENABLED', 'false')
    vi.resetModules()
    try {
      const { businessHref: fallback } = await import('./appRoutes')
      expect(fallback('/apps/patrol?patrol_batch_id=b1')).toBe(
        '/devices?patrol_batch_id=b1&tab=patrol',
      )
      expect(fallback('/apps/patrol-rules/source-jobs/j1?tab=sources')).toBe(
        '/skills/parser/source-jobs/j1?tab=sources',
      )
      expect(fallback('/devices?tab=patrol')).toBe('/devices?tab=patrol')
    } finally {
      vi.unstubAllEnvs()
      vi.resetModules()
    }
  })
})
