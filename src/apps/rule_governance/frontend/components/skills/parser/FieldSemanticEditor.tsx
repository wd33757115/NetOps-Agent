import React from 'react'
import { Checkbox, Collapse, Form, Input, Select, Typography } from 'antd'

export const semanticKinds = [
  { value: 'state', label: '状态' }, { value: 'text', label: '文本' },
  { value: 'gauge', label: '数值测量' }, { value: 'counter', label: '累计计数' },
  { value: 'threshold', label: '阈值参考' }, { value: 'enum', label: '枚举' },
  { value: 'boolean', label: '布尔' },
]

export default function FieldSemanticEditor({ name, field, fields }: {
  name: number; field: any; fields: any[]
}) {
  const kind = field?.semantic_definition?.semantic_type
  const numeric = ['gauge', 'counter', 'threshold'].includes(kind)
  const state = ['state', 'enum', 'boolean'].includes(kind)
  const path = (key: string) => [name, 'semantic_definition', key]
  const roles = field?.semantic_definition?.assessment_roles || []
  const form = Form.useFormInstance()
  return <Collapse size="small" style={{ marginBottom: 12 }} items={[{
    key: 'semantics', label: '编辑语义与规则用途',
    children: <>
      <Typography.Paragraph type="secondary">仅作用于当前检查项。已有语义可作为模板；保存草稿后预览影响并发布。</Typography.Paragraph>
      <Form.Item label="含义说明" name={path('description')} rules={[{ required: true, whitespace: true, message: '请填写字段含义' }]}>
        <Input.TextArea rows={2} placeholder="说明这个 Value 表达什么，以及缺失或未知值的含义" />
      </Form.Item>
      <div className="check-item-catalog-form-grid">
        <Form.Item label="语义类型" name={path('semantic_type')} rules={[{ required: true }]}>
          <Select options={semanticKinds} onChange={(value) => {
            form.setFieldValue(['canonical_fields', name, 'value_type'], ['gauge', 'counter', 'threshold'].includes(value) ? 'number' : value === 'boolean' ? 'boolean' : 'text')
            form.setFieldValue(['canonical_fields', name, 'compare_mode'], undefined)
          }} />
        </Form.Item>
        <Form.Item label="评估用途" name={path('assessment_roles')} rules={[{ required: true, type: 'array', min: 1 }]}>
          <Select mode="multiple" options={[
            { value: 'evidence', label: '仅展示', disabled: roles.some((r: string) => r !== 'evidence') },
            { value: 'current_state', label: '健康判断', disabled: roles.includes('evidence') || kind === 'text' },
            { value: 'change', label: '变化检测', disabled: roles.includes('evidence') },
            { value: 'reference', label: '阈值参考', disabled: roles.includes('evidence') },
          ]} />
        </Form.Item>
        <Form.Item name={[name, 'change_tracking']} valuePropName="checked" initialValue={true}
          className="check-item-catalog-form-wide"
          extra="关闭后仍采集、保存并展示该 Value；不参与普通变化统计，健康判断、设备阈值和基线趋势不受影响。">
          <Checkbox>参与普通变化比较</Checkbox>
        </Form.Item>
        {state && <>
          <Form.Item label="允许的状态值" name={path('allowed_values')} className="check-item-catalog-form-wide">
            <Select mode="tags" tokenSeparators={[',']} placeholder="例如 normal、failed、unknown" />
          </Form.Item>
          {Object.entries({ healthy: '正常状态', unhealthy: '异常状态', unknown: '未知状态', transitional: '过渡状态' }).map(([key, label]) =>
            <Form.Item key={key} label={label} name={[name, 'semantic_definition', 'state_groups', key]}>
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>)}
        </>}
        <Form.Item label="变化比较方式" name={[name, 'compare_mode']} extra="未指定时按语义类型与阈值关系自动选择。">
          <Select allowClear placeholder="按语义类型自动选择" options={[
            ...(field?.compare_mode === '__existing__' ? [{ value: '__existing__', label: '保留已有多种比较约束' }] : []),
            ...(!numeric ? [{ value: 'exact', label: '精确比较' }] : []),
            ...(state ? [{ value: 'state_transition', label: '状态转换' }] : []),
            ...(numeric ? [{ value: kind === 'counter' ? 'counter_delta' : 'numeric_delta', label: kind === 'counter' ? '计数增量' : '数值变化' }] : []),
            ...(numeric && kind !== 'counter' ? [{ value: 'threshold_and_deadband', label: '阈值比较' }, { value: 'threshold_low_and_deadband', label: '下限阈值比较' }, { value: 'dimension_dispatch', label: '已有维度策略' }] : []),
            ...(state ? [{ value: 'enum_transition', label: '枚举转换' }] : []),
            ...(kind === 'text' ? [{ value: 'hash_diff', label: '摘要比较' }] : []),
          ]} />
        </Form.Item>
        {numeric && <>
          <Typography.Paragraph className="check-item-catalog-form-wide" type="secondary">阈值来自设备输出的其他字段，缺失时不能判定健康。已有基线与高级约束会保留。</Typography.Paragraph>
          {Object.entries({ high: '上限字段', low: '下限字段', critical_high: '严重上限字段', critical_low: '严重下限字段' }).map(([key, label]) =>
            <Form.Item key={key} label={label} name={[name, 'threshold_fields', key]}>
              <Select allowClear showSearch options={fields.filter((f) => f.name && f.name !== field.name && ['number', 'integer', 'float', 'percent'].includes(f.value_type)).map((f) => ({ value: f.name, label: f.name }))} />
            </Form.Item>)}
        </>}
      </div>
    </>,
  }]} />
}
