import React from 'react'
import { Button, Checkbox, Collapse, Form, Input, InputNumber, Select, Tag, Typography } from 'antd'

export const semanticKinds = [
  { value: 'state', label: '状态' }, { value: 'text', label: '文本' },
  { value: 'gauge', label: '数值测量' }, { value: 'counter', label: '累计计数' },
  { value: 'threshold', label: '阈值参考' }, { value: 'enum', label: '枚举' },
  { value: 'boolean', label: '布尔' },
]

export type FieldUse = 'evidence' | 'current_state' | 'change' | 'reference'

export const fieldUses: Record<FieldUse, { label: string; hint: string; color?: string }> = {
  evidence: { label: '仅展示', hint: '只进报告，不判断' },
  current_state: { label: '健康判断', hint: '当前值不正常就是异常', color: 'red' },
  change: { label: '变化检测', hint: '和上次巡检不同时提醒', color: 'blue' },
  reference: { label: '阈值参考', hint: '给其他字段当阈值', color: 'cyan' },
}

const stateKinds = ['state', 'enum', 'boolean']
const numberTypes = ['number', 'integer', 'float', 'percent']

/** The uses a Value of this kind can have; the first is its default. */
export const usesForKind = (kind?: string): FieldUse[] => (
  kind === 'threshold' ? ['reference', 'evidence']
    : kind === 'gauge' || stateKinds.includes(kind || '') ? ['evidence', 'current_state', 'change']
      : ['evidence', 'change']
)

/** A Value has one use. Stored data may list several; the strongest one is what runs. */
export const pickUse = (roles: string[] | undefined, kind?: string): FieldUse => {
  const allowed = usesForKind(kind)
  return (['current_state', 'change', 'reference', 'evidence'] as FieldUse[])
    .find((use) => roles?.includes(use) && allowed.includes(use)) || allowed[0]
}

export const trendKindForUnit = (unit?: string) => {
  const value = (unit || '').trim().toLowerCase()
  return value === 'percent' || value === '%' ? 'utilization_increase' : value === 'dbm' ? 'linear_power_drop' : undefined
}

export interface FieldRulePreview {
  outcome: string; lines: string[]; warnings: string[]
  /** What the field compiles to, why, and which row of the guide says so. */
  summary?: string; reason?: string; guide_row?: string
}
export interface FieldRulesInForce { lines: string[]; stale_count: number; stale_versions: number[] }

/** A Value that is only shown or only referenced has no rule, before or after saving. */
const unchanged = (preview: FieldRulePreview, inForce: FieldRulesInForce) => (
  ['evidence', 'reference'].includes(preview.outcome)
    ? inForce.lines.length === 0
    : preview.lines.join('\n') === inForce.lines.join('\n')
)

export default function FieldSemanticEditor({ name, field, fields, preview, inForce, onOpenGuide }: {
  name: number; field: any; fields: any[]; preview?: FieldRulePreview; inForce?: FieldRulesInForce
  onOpenGuide?: (row: string, field: string) => void
}) {
  const form = Form.useFormInstance()
  const kind = field?.semantic_definition?.semantic_type
  const use = pickUse(field?.semantic_definition?.assessment_roles, kind)
  const state = stateKinds.includes(kind)
  const numericHealth = kind === 'gauge' && use === 'current_state'
  const trendKind = trendKindForUnit(field?.unit)
  const groups = field?.semantic_definition?.state_groups || {}
  const grouped = Boolean(groups.healthy?.length && groups.unhealthy?.length)
  const path = (key: string) => [name, 'semantic_definition', key]
  const set = (key: string, value: unknown) => form.setFieldValue(['canonical_fields', name, key], value)
  const thresholdOptions = fields
    .filter((f) => f?.name && f.name !== field?.name && numberTypes.includes(f.value_type)
      && (f.unit || '').toLowerCase() === (field?.unit || '').toLowerCase())
    .map((f) => ({ value: f.name, label: f.name }))
  return <>
    <div className="check-item-field-outcome" aria-label={`${field?.name || '字段'} 的巡检行为`}>
      <Tag color={fieldUses[use].color}>{fieldUses[use].label}</Tag>
      {preview?.summary && (
        <div className="check-item-field-generates">
          <Typography.Text strong>{preview.summary}</Typography.Text>
          <Typography.Text type="secondary">
            {preview.reason}
            {onOpenGuide && preview.guide_row && (
              <Button type="link" size="small" className="check-item-field-generates__link"
                onClick={() => onOpenGuide(preview.guide_row as string, field?.name || '')}>
                规则是怎么生成的
              </Button>
            )}
          </Typography.Text>
        </div>
      )}
      {preview ? <>
        {preview.lines.map((line) => <Typography.Text key={line}>{line}</Typography.Text>)}
        {preview.warnings.map((line) => <Typography.Text key={line} type="warning">{line}</Typography.Text>)}
      </> : <Typography.Text type="secondary">{fieldUses[use].hint}</Typography.Text>}
      {preview && inForce && (unchanged(preview, inForce)
        ? <Typography.Text type="secondary">与现在生效的规则一致。</Typography.Text>
        : <div className="check-item-field-in-force">
          <Typography.Text type="secondary">现在生效（保存并发布后将被上面的内容取代）：</Typography.Text>
          {inForce.lines.length
            ? inForce.lines.map((line) => <Typography.Text key={line} type="secondary">{line}</Typography.Text>)
            : <Typography.Text type="secondary">没有生效的规则。</Typography.Text>}
        </div>)}
      {!!inForce?.stale_count && <Typography.Text type="warning">
        另有 {inForce.stale_count} 条绑定在旧版本 v{inForce.stale_versions.join('、v')} 上的规则仍为生效状态。
      </Typography.Text>}
    </div>
    <Collapse size="small" style={{ marginBottom: 12 }} items={[{
      key: 'semantics', label: '编辑语义与规则用途',
      children: <>
        <Form.Item label="含义说明" name={path('description')} rules={[{ required: true, whitespace: true, message: '请填写字段含义' }]}>
          <Input.TextArea rows={2} placeholder="说明这个 Value 表达什么，以及缺失或未知值的含义" />
        </Form.Item>
        <div className="check-item-catalog-form-grid">
          <Form.Item label="语义类型" name={path('semantic_type')} rules={[{ required: true }]}>
            <Select options={semanticKinds} onChange={(value) => {
              set('value_type', ['gauge', 'counter', 'threshold'].includes(value) ? 'number' : value === 'boolean' ? 'boolean' : 'text')
              set('compare_mode', undefined)
              // A use the new kind cannot have falls back to that kind's default.
              form.setFieldValue(['canonical_fields', ...path('assessment_roles')], [pickUse([use], value)])
            }} />
          </Form.Item>
          <Form.Item label="这个值怎么用" name={path('assessment_roles')} rules={[{ required: true }]}
            getValueProps={(roles) => ({ value: pickUse(roles, kind) })} normalize={(value) => [value]}>
            <Select aria-label="这个值怎么用" options={usesForKind(kind).map((value) => ({
              value, label: `${fieldUses[value].label} · ${fieldUses[value].hint}`,
            }))} onChange={(value) => { if (value === 'change') set('change_tracking', true) }} />
          </Form.Item>

          {state && use !== 'evidence' && <>
            <Typography.Paragraph className="check-item-catalog-form-wide" type="secondary">
              {use === 'current_state'
                ? '当前值属于“异常状态”即判定异常；必须填写正常和异常状态。'
                : '填写正常和异常状态后，变坏会产生故障事件、恢复会产生恢复事件；不填则任何变化都只是提示。'}
            </Typography.Paragraph>
            <Form.Item label="允许的状态值" name={path('allowed_values')} className="check-item-catalog-form-wide">
              <Select mode="tags" tokenSeparators={[',']} placeholder="例如 normal、failed、unknown；留空表示不限制" />
            </Form.Item>
            {Object.entries({ healthy: '正常状态', unhealthy: '异常状态', unknown: '未知状态', transitional: '过渡状态' }).map(([key, label]) =>
              <Form.Item key={key} label={label} name={[name, 'semantic_definition', 'state_groups', key]}>
                <Select mode="tags" tokenSeparators={[',']} />
              </Form.Item>)}
          </>}

          {use === 'change' && <Form.Item label="变化比较方式" name={[name, 'compare_mode']}
            extra={state && grouped && field?.compare_mode === 'exact'
              ? <Typography.Text type="warning">选精确比较后不再区分变坏和恢复，只产生提示事件。要保留故障和恢复事件，请清空这一项。</Typography.Text>
              : '未指定时按语义类型自动选择。'}>
            <Select allowClear placeholder="按语义类型自动选择" options={[
              ...(field?.compare_mode === '__existing__' ? [{ value: '__existing__', label: '保留已有多种比较约束' }] : []),
              ...(kind === 'gauge' ? [{ value: 'numeric_delta', label: '数值变化' }]
                : kind === 'counter' ? [{ value: 'counter_delta', label: '计数增量' }]
                  : [{ value: 'exact', label: '精确比较' }]),
              ...(state ? [{ value: 'state_transition', label: '状态转换' }, { value: 'enum_transition', label: '枚举转换' }] : []),
              ...(kind === 'text' ? [{ value: 'hash_diff', label: '摘要比较' }] : []),
            ]} />
          </Form.Item>}

          {numericHealth && <>
            <Typography.Paragraph className="check-item-catalog-form-wide" type="secondary">
              阈值取自设备在同一条记录里输出的其他字段，单位必须相同；同一侧选了多个时取较严格的一个。设备没有输出阈值时结果为“无法判定”。
            </Typography.Paragraph>
            <Form.Item label="上限阈值字段" name={[name, 'threshold_high']}>
              <Select mode="multiple" maxCount={2} allowClear options={thresholdOptions} placeholder="高于它即异常" />
            </Form.Item>
            <Form.Item label="下限阈值字段" name={[name, 'threshold_low']}>
              <Select mode="multiple" maxCount={2} allowClear options={thresholdOptions} placeholder="低于它即异常" />
            </Form.Item>
            <Form.Item label="趋势提醒" name={[name, 'trend', 'kind']} className="check-item-catalog-form-wide"
              extra={trendKind ? '与上次巡检比较，只作提醒，不计入异常。' : '仅单位为 percent 或 dbm 的字段支持趋势提醒。'}>
              <Select disabled={!trendKind} options={[
                { value: 'none', label: '不启用' },
                ...(trendKind === 'utilization_increase' ? [{ value: trendKind, label: '利用率上升' }] : []),
                ...(trendKind === 'linear_power_drop' ? [{ value: trendKind, label: '光功率下降' }] : []),
              ]} />
            </Form.Item>
            {field?.trend?.kind && field.trend.kind !== 'none' && <>
              <Form.Item label={field.trend.kind === 'linear_power_drop' ? '线性功率下降超过（%）' : '相对上次上升超过（%）'}
                name={[name, 'trend', 'ratio_percent']} rules={[{ required: true, message: '请填写比例' }]}>
                <InputNumber min={1} max={100} style={{ width: '100%' }} />
              </Form.Item>
              {field.trend.kind === 'utilization_increase'
                ? <Form.Item label="且至少上升（百分点）" name={[name, 'trend', 'min_increase_points']} rules={[{ required: true, message: '请填写百分点' }]}>
                  <InputNumber min={1} max={100} style={{ width: '100%' }} />
                </Form.Item>
                : <>
                  <Form.Item label="确认同一模块的字段" name={[name, 'trend', 'identity_fields']}>
                    <Select mode="multiple" allowClear options={fields.filter((f) => f?.name && f.name !== field.name).map((f) => ({ value: f.name, label: f.name }))} />
                  </Form.Item>
                  <Form.Item name={[name, 'trend', 'require_identity']} valuePropName="checked" className="check-item-catalog-form-wide">
                    <Checkbox>模块身份缺失时不比较</Checkbox>
                  </Form.Item>
                </>}
            </>}
          </>}
        </div>
      </>,
    }]} />
  </>
}
