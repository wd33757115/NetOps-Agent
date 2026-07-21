import { describe, expect, it } from 'vitest'
import { buildDeviceSelector, emptyDeviceSelector } from './DeviceSelectorForm'

describe('buildDeviceSelector', () => {
  it('omits empty values and preserves supported selector dimensions', () => {
    expect(buildDeviceSelector({
      ...emptyDeviceSelector(),
      vendor: ' H3C ',
      role: 'core',
      site: ' ',
    })).toEqual({ vendor: 'H3C', role: 'core' })
  })
})
