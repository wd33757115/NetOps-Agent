import { describe, expect, it } from 'vitest'
import { buildEffectiveDevice } from './EffectiveDeviceForm'

describe('buildEffectiveDevice', () => {
  it('maps form fields to the collection policy device contract', () => {
    expect(buildEffectiveDevice({
      deviceId: ' device-1 ',
      vendor: 'H3C',
      model: 'S5130',
      family: '',
      role: 'access',
      site: '',
      commandFile: 'default-patrol',
    })).toEqual({
      device_id: 'device-1',
      vendor: 'H3C',
      model: 'S5130',
      role: 'access',
      command_file: 'default-patrol',
      attributes: {},
    })
  })
})
