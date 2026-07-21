import React from 'react'
import { Col, Input, Row, Typography } from 'antd'

export interface EffectiveDeviceDraft {
  deviceId: string
  vendor: string
  model: string
  family: string
  role: string
  site: string
  commandFile: string
}

export const buildEffectiveDevice = (value: EffectiveDeviceDraft): Record<string, unknown> => {
  const result: Record<string, unknown> = {
    device_id: value.deviceId.trim(),
    attributes: {},
  }
  const mappings: Array<[keyof EffectiveDeviceDraft, string]> = [
    ['vendor', 'vendor'],
    ['model', 'model'],
    ['family', 'family'],
    ['role', 'role'],
    ['site', 'site'],
    ['commandFile', 'command_file'],
  ]
  mappings.forEach(([source, target]) => {
    const item = value[source].trim()
    if (item) result[target] = item
  })
  return result
}

interface EffectiveDeviceFormProps {
  value: EffectiveDeviceDraft
  onChange: (value: EffectiveDeviceDraft) => void
}

const fields: Array<{ key: keyof EffectiveDeviceDraft; label: string; placeholder: string }> = [
  { key: 'deviceId', label: '设备 ID', placeholder: '必填，例如 device-1' },
  { key: 'vendor', label: '厂商', placeholder: '例如 Cisco、H3C、Huawei' },
  { key: 'model', label: '型号', placeholder: '设备型号' },
  { key: 'family', label: '设备族', placeholder: '设备系列或家族' },
  { key: 'role', label: '角色', placeholder: '例如 core、access' },
  { key: 'site', label: '站点', placeholder: '设备所属站点' },
  { key: 'commandFile', label: '兼容命令集', placeholder: '可选旧 command_file 名称' },
]

const EffectiveDeviceForm: React.FC<EffectiveDeviceFormProps> = ({ value, onChange }) => (
  <Row gutter={[12, 12]}>
    {fields.map((field) => (
      <Col xs={24} sm={12} lg={8} key={field.key}>
        <Typography.Text type="secondary">{field.label}</Typography.Text>
        <Input value={value[field.key]} placeholder={field.placeholder}
          onChange={(event) => onChange({ ...value, [field.key]: event.target.value })} />
      </Col>
    ))}
  </Row>
)

export default EffectiveDeviceForm
