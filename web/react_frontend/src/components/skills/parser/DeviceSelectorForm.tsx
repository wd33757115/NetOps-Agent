import React from 'react'
import { Col, Input, Row, Typography } from 'antd'

export interface DeviceSelectorDraft {
  vendor: string
  model: string
  family: string
  role: string
  site: string
  group: string
}

export const emptyDeviceSelector = (): DeviceSelectorDraft => ({
  vendor: '',
  model: '',
  family: '',
  role: '',
  site: '',
  group: '',
})

export const buildDeviceSelector = (
  value: DeviceSelectorDraft,
): Record<string, string> => Object.fromEntries(
  Object.entries(value)
    .map(([key, item]) => [key, item.trim()])
    .filter(([, item]) => Boolean(item)),
)

interface DeviceSelectorFormProps {
  value: DeviceSelectorDraft
  onChange: (value: DeviceSelectorDraft) => void
}

const fields: Array<{ key: keyof DeviceSelectorDraft; label: string; placeholder: string }> = [
  { key: 'vendor', label: '厂商', placeholder: '例如 Cisco、H3C、Huawei' },
  { key: 'model', label: '型号', placeholder: '精确设备型号' },
  { key: 'family', label: '设备族', placeholder: '例如 S5130、Catalyst 9K' },
  { key: 'role', label: '角色', placeholder: '例如 core、access' },
  { key: 'site', label: '站点', placeholder: '例如 西安园区' },
  { key: 'group', label: '分组', placeholder: '资产分组名称' },
]

const DeviceSelectorForm: React.FC<DeviceSelectorFormProps> = ({ value, onChange }) => (
  <Row gutter={[12, 12]}>
    {fields.map((field) => (
      <Col xs={24} sm={12} lg={8} key={field.key}>
        <Typography.Text type="secondary">{field.label}</Typography.Text>
        <Input
          value={value[field.key]}
          placeholder={field.placeholder}
          onChange={(event) => onChange({ ...value, [field.key]: event.target.value })}
        />
      </Col>
    ))}
  </Row>
)

export default DeviceSelectorForm
