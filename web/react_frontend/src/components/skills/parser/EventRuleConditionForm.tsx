import React from 'react'
import { Col, Form, Input, InputNumber, Row, Select } from 'antd'

export interface EventRuleConditionDraft {
  observationType: string
  entityType: string
  field: string
  kind: string
  threshold: number
  hysteresis: number
  referenceField: string
  fromGroup: 'up' | 'down' | 'other'
  toGroup: 'up' | 'down' | 'other'
}

interface EventRuleConditionFormProps {
  value: EventRuleConditionDraft
  onChange: (value: EventRuleConditionDraft) => void
}

const thresholdKinds = new Set([
  'threshold',
  'threshold_lte',
  'threshold_recovery',
  'threshold_low_recovery',
])

const dynamicKinds = new Set([
  'threshold_field_gte',
  'threshold_field_lte',
  'threshold_field_high_recovery',
  'threshold_field_low_recovery',
])

const recoveryKinds = new Set([
  'threshold_low_recovery',
  'threshold_field_high_recovery',
  'threshold_field_low_recovery',
])

export const buildEventRuleCondition = (
  draft: EventRuleConditionDraft,
): Record<string, unknown> => {
  const base: Record<string, unknown> = {
    kind: draft.kind,
    observation_types: [draft.observationType.trim()],
    fields: [draft.field.trim()],
  }
  if (draft.entityType.trim()) base.entity_types = [draft.entityType.trim()]
  if (draft.kind === 'threshold') return { ...base, gte: draft.threshold }
  if (draft.kind === 'threshold_lte') return { ...base, lte: draft.threshold }
  if (draft.kind === 'threshold_recovery') return { ...base, lt: draft.threshold }
  if (draft.kind === 'threshold_low_recovery') {
    return { ...base, low: draft.threshold, hysteresis: draft.hysteresis }
  }
  if (dynamicKinds.has(draft.kind)) {
    return {
      ...base,
      reference_field: draft.referenceField.trim(),
      ...(recoveryKinds.has(draft.kind) ? { hysteresis: draft.hysteresis } : {}),
    }
  }
  if (draft.kind === 'transition') {
    return {
      ...base,
      from_group: draft.fromGroup,
      to_group: draft.toGroup,
      allow_initial_target: false,
    }
  }
  return base
}

const EventRuleConditionForm: React.FC<EventRuleConditionFormProps> = ({ value, onChange }) => {
  const update = <K extends keyof EventRuleConditionDraft>(
    key: K,
    nextValue: EventRuleConditionDraft[K],
  ) => onChange({ ...value, [key]: nextValue })

  return (
    <Form layout="vertical" requiredMark="optional">
      <Row gutter={[12, 0]}>
        <Col xs={24} md={12} lg={6}>
          <Form.Item label="Observation Type" required>
            <Input
              value={value.observationType}
              onChange={(event) => update('observationType', event.target.value)}
              placeholder="interface.oper_status"
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item label="监测字段" required>
            <Input
              value={value.field}
              onChange={(event) => update('field', event.target.value)}
              placeholder="status 或 rx_power_dbm"
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item label="实体类型">
            <Input
              value={value.entityType}
              onChange={(event) => update('entityType', event.target.value)}
              placeholder="interface_l2"
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item label="条件类型" required>
            <Select
              value={value.kind}
              onChange={(nextValue) => update('kind', nextValue)}
              options={[
                { value: 'transition', label: '状态转换' },
                { value: 'threshold', label: '高于固定阈值' },
                { value: 'threshold_lte', label: '低于固定阈值' },
                { value: 'threshold_recovery', label: '高阈值恢复' },
                { value: 'threshold_low_recovery', label: '低阈值恢复' },
                { value: 'threshold_field_gte', label: '高于设备阈值字段' },
                { value: 'threshold_field_lte', label: '低于设备阈值字段' },
                { value: 'threshold_field_high_recovery', label: '设备高阈值恢复' },
                { value: 'threshold_field_low_recovery', label: '设备低阈值恢复' },
                { value: 'entity_status_down', label: '实体状态异常' },
              ]}
            />
          </Form.Item>
        </Col>
        {thresholdKinds.has(value.kind) && (
          <Col xs={24} md={12} lg={6}>
            <Form.Item label="阈值" required>
              <InputNumber
                style={{ width: '100%' }}
                value={value.threshold}
                onChange={(nextValue) => update('threshold', Number(nextValue ?? 0))}
              />
            </Form.Item>
          </Col>
        )}
        {dynamicKinds.has(value.kind) && (
          <Col xs={24} md={12} lg={6}>
            <Form.Item label="设备阈值字段" required>
              <Input
                value={value.referenceField}
                onChange={(event) => update('referenceField', event.target.value)}
                placeholder="rx_low_warning_dbm"
              />
            </Form.Item>
          </Col>
        )}
        {recoveryKinds.has(value.kind) && (
          <Col xs={24} md={12} lg={6}>
            <Form.Item label="恢复滞回值" required>
              <InputNumber
                min={0}
                style={{ width: '100%' }}
                value={value.hysteresis}
                onChange={(nextValue) => update('hysteresis', Number(nextValue ?? 0))}
              />
            </Form.Item>
          </Col>
        )}
        {value.kind === 'transition' && (
          <>
            <Col xs={24} md={12} lg={6}>
              <Form.Item label="原状态组" required>
                <Select
                  value={value.fromGroup}
                  onChange={(nextValue) => update('fromGroup', nextValue)}
                  options={['up', 'down', 'other'].map((item) => ({ value: item, label: item }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12} lg={6}>
              <Form.Item label="目标状态组" required>
                <Select
                  value={value.toGroup}
                  onChange={(nextValue) => update('toGroup', nextValue)}
                  options={['up', 'down', 'other'].map((item) => ({ value: item, label: item }))}
                />
              </Form.Item>
            </Col>
          </>
        )}
      </Row>
    </Form>
  )
}

export default EventRuleConditionForm
