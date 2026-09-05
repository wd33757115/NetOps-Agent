import React from 'react'
import { Select } from 'antd'
import type { ManagedDevice } from '../../services/api'

export default function PatrolDeviceSelector({
  devices,
  selected,
  onChange,
}: {
  devices: ManagedDevice[]
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  return (
    <Select
      mode="multiple"
      allowClear
      showSearch
      optionFilterProp="label"
      aria-label="巡检设备"
      placeholder="选择巡检设备"
      value={selected}
      options={devices.map((device) => ({
        value: device.device_id,
        label: `${device.device_name} · ${device.management_ip || device.ip || ''}`,
      }))}
      onChange={onChange}
    />
  )
}
