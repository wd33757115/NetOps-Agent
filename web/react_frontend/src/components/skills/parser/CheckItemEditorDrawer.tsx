import React, { useEffect } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'

import type {
  CanonicalObservationCatalog,
  CheckItemCatalogCheckItemDraftInput,
  ParserCheckItem,
} from '../../../services/api'

interface CheckItemEditorDrawerProps {
  open: boolean
  mobile: boolean
  checkItem?: ParserCheckItem
  canonicalCatalog?: CanonicalObservationCatalog
  canonicalLoading?: boolean
  canonicalError?: string
  loading?: boolean
  onClose: () => void
  onSubmit: (payload: CheckItemCatalogCheckItemDraftInput) => void
}

interface CheckItemFormValues {
  id: string
  name: string
  category: string
  description?: string
  result_mode: CheckItemCatalogCheckItemDraftInput['result_mode']
  assessment_mode: CheckItemCatalogCheckItemDraftInput['assessment_mode']
  entity_type: string
  identity_dimensions: string[]
  canonical_fields: Array<{
    name: string
    observation_type: string
    value_type: string
    unit?: string
    required: boolean
  }>
  tracked_fields: string[]
  evaluation_json: string
  events_json: string
  report_projection: string
  max_observation_age?: string
  max_collection_span?: string
  empty_classification: string
  coverage_model: string
  expected_count?: number
  coverage_source?: string
  first_observation?: string
  canonical_contract_json: string
  applicability_json: string
  reason?: string
}

const jsonObject = (value: string | undefined, label: string): Record<string, unknown> => {
  try {
    const parsed = JSON.parse(value || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
    return parsed
  } catch {
    throw new Error(`${label}不是有效 JSON 对象`)
  }
}

const jsonArray = (value: string | undefined, label: string): Array<Record<string, unknown>> => {
  try {
    const parsed = JSON.parse(value || '[]')
    if (!Array.isArray(parsed) || parsed.some((item) => !item || Array.isArray(item) || typeof item !== 'object')) {
      throw new Error()
    }
    return parsed as Array<Record<string, unknown>>
  } catch {
    throw new Error(`${label}不是有效 JSON 数组`)
  }
}

const initialValues = (checkItem?: ParserCheckItem): CheckItemFormValues => {
  const contract = checkItem?.canonical_contract || {}
  const observations = Array.isArray(contract.observations)
    ? contract.observations as Array<Record<string, unknown>>
    : []
  return {
    id: checkItem?.check_item_id || '',
    name: checkItem?.name || '',
    category: checkItem?.category || 'system',
    description: checkItem?.description || '',
    result_mode: (checkItem?.result_mode || 'structured') as CheckItemFormValues['result_mode'],
    assessment_mode: (checkItem?.assessment_mode || 'health') as CheckItemFormValues['assessment_mode'],
    entity_type: String(contract.entity_type || 'device'),
    identity_dimensions: Array.isArray(contract.identity_dimensions)
      ? contract.identity_dimensions.map(String)
      : ['device_id'],
    canonical_fields: observations.map((item) => ({
      name: String(item.canonical_field || item.observation_type || ''),
      observation_type: String(item.observation_type || ''),
      value_type: String(item.value_type || item.semantic_type || 'text'),
      unit: String(item.unit || ''),
      required: item.required !== false,
    })),
    tracked_fields: Array.isArray((contract.change as Record<string, unknown> | undefined)?.tracked_fields)
      ? ((contract.change as Record<string, unknown>).tracked_fields as unknown[]).map(String)
      : observations.map((item) => String(item.canonical_field || '')).filter(Boolean),
    evaluation_json: JSON.stringify(Array.isArray(contract.evaluation) ? contract.evaluation : [], null, 2),
    events_json: JSON.stringify(Array.isArray(contract.events) ? contract.events : [], null, 2),
    report_projection: checkItem?.report_projection || 'auto',
    max_observation_age: String(checkItem?.freshness_policy?.max_observation_age || '24h'),
    max_collection_span: String(checkItem?.freshness_policy?.max_collection_span || '8h'),
    empty_classification: String(checkItem?.empty_result_policy?.classification || 'invalid'),
    coverage_model: String(checkItem?.coverage_policy?.model || 'non_exhaustive'),
    expected_count: checkItem?.coverage_policy?.expected_count == null
      ? undefined
      : Number(checkItem.coverage_policy.expected_count),
    coverage_source: String(checkItem?.coverage_policy?.source || ''),
    first_observation: String(checkItem?.coverage_policy?.first_observation || ''),
    canonical_contract_json: JSON.stringify(contract, null, 2),
    applicability_json: JSON.stringify(checkItem?.applicability || {}, null, 2),
    reason: '',
  }
}

const CheckItemEditorDrawer: React.FC<CheckItemEditorDrawerProps> = ({
  open,
  mobile,
  checkItem,
  canonicalCatalog,
  canonicalLoading,
  canonicalError,
  loading,
  onClose,
  onSubmit,
}) => {
  const [form] = Form.useForm<CheckItemFormValues>()
  const resultMode = Form.useWatch('result_mode', form)
  const assessmentMode = Form.useWatch('assessment_mode', form)
  const coverageModel = Form.useWatch('coverage_model', form)

  useEffect(() => {
    if (open) form.setFieldsValue(initialValues(checkItem))
  }, [checkItem, form, open])

  const buildCanonicalContract = (values: CheckItemFormValues) => {
    const contract = jsonObject(values.canonical_contract_json, '标准输出契约')
    const existingRows = new Map(
      (Array.isArray(contract.observations)
        ? contract.observations as Array<Record<string, unknown>>
        : [])
        .map((row) => [String(row.observation_type || ''), row]),
    )
    const definitions = new Map(
      (canonicalCatalog?.items || []).map((item) => [item.id, item]),
    )
    const observations = (values.canonical_fields || []).map((field) => {
      const observationType = field.observation_type
      const existing = existingRows.get(observationType)
      const definition = definitions.get(observationType)
      return {
        ...(existing || {}),
        observation_type: observationType,
        canonical_field: field.name.trim(),
        semantic_type: definition?.semantic_type || existing?.semantic_type || field.value_type,
        value_type: field.value_type,
        ...(field.unit?.trim()
          ? { unit: field.unit.trim() }
          : definition?.canonical_unit
            ? { unit: definition.canonical_unit }
            : {}),
        ...(definition?.dimensions?.length ? { dimensions: definition.dimensions } : {}),
        required: field.required,
      }
    })
    const vocabulary = {
      ...(typeof contract.parser_field_vocabulary === 'object' && contract.parser_field_vocabulary
        ? contract.parser_field_vocabulary as Record<string, unknown>
        : {}),
    }
    for (const identity of values.identity_dimensions || []) {
      if (identity !== 'device_id') {
        vocabulary[identity] = {
          ...(typeof vocabulary[identity] === 'object' && vocabulary[identity]
            ? vocabulary[identity] as Record<string, unknown>
            : {}),
          role: 'identity',
        }
      }
    }
    for (const observation of observations) {
      const fieldName = String(observation.canonical_field || '')
      if (!fieldName) continue
      vocabulary[fieldName] = {
        ...(typeof vocabulary[fieldName] === 'object' && vocabulary[fieldName]
          ? vocabulary[fieldName] as Record<string, unknown>
          : {}),
        observation_type: observation.observation_type,
        role: observation.semantic_type === 'state' ? 'state' : 'metric',
        value_type: observation.value_type,
        ...(observation.unit ? { unit: observation.unit } : {}),
      }
    }
    return {
      ...contract,
      entity_type: values.entity_type,
      identity_dimensions: values.identity_dimensions || [],
      observations,
      parser_field_vocabulary: vocabulary,
      change: {
        ...(typeof contract.change === 'object' && contract.change ? contract.change : {}),
        tracked_fields: values.tracked_fields || [],
      },
      evaluation: jsonArray(values.evaluation_json, '当前值判断规则'),
      events: jsonArray(values.events_json, '事件规则'),
    }
  }

  const synchronizeContract = () => {
    try {
      const contract = buildCanonicalContract(form.getFieldsValue())
      form.setFieldValue('canonical_contract_json', JSON.stringify(contract, null, 2))
    } catch (error) {
      message.error(error instanceof Error ? error.message : '标准输出契约无法刷新')
      return
    }
    message.success('标准输出契约已按当前字段选择刷新')
  }

  const save = async () => {
    try {
      const values = await form.validateFields()
      if (values.result_mode === 'structured' && !(values.canonical_fields || []).length) {
        form.setFields([{
          name: 'canonical_fields',
          errors: ['结构化检查项至少需要一个标准字段'],
        }])
        message.error('结构化检查项至少需要一个标准字段')
        return
      }
      const fieldNames = (values.canonical_fields || []).map((item) => item.name.trim())
      if (new Set(fieldNames).size !== fieldNames.length) {
        form.setFields([{
          name: 'canonical_fields',
          errors: ['标准列名不能重复'],
        }])
        return
      }
      if (values.result_mode === 'structured' && canonicalError) {
        message.error('标准观测目录加载失败，暂时不能创建结构化检查项')
        return
      }
      const canonicalContract = buildCanonicalContract(values)
      const applicability = jsonObject(values.applicability_json, '适用条件')
      const coveragePolicy: Record<string, unknown> = {
        ...(checkItem?.coverage_policy || {}),
        model: values.coverage_model,
      }
      if (values.coverage_model === 'fixed') {
        coveragePolicy.expected_count = Number(values.expected_count)
      } else {
        delete coveragePolicy.expected_count
      }
      if (values.coverage_source?.trim()) coveragePolicy.source = values.coverage_source.trim()
      else delete coveragePolicy.source
      if (values.first_observation?.trim()) {
        coveragePolicy.first_observation = values.first_observation.trim()
      } else delete coveragePolicy.first_observation
      onSubmit({
        id: values.id.trim(),
        name: values.name.trim(),
        category: values.category.trim(),
        description: values.description?.trim(),
        result_mode: values.result_mode,
        assessment_mode: values.assessment_mode,
        canonical_contract: canonicalContract,
        report_projection: values.report_projection.trim(),
        applicability,
        freshness_policy: {
          ...(checkItem?.freshness_policy || {}),
          max_observation_age: values.max_observation_age?.trim(),
          max_collection_span: values.max_collection_span?.trim(),
        },
        empty_result_policy: {
          ...(checkItem?.empty_result_policy || {}),
          classification: values.empty_classification,
        },
        coverage_policy: coveragePolicy,
        reason: values.reason?.trim() || '',
      })
    } catch (error) {
      if (error instanceof Error) message.error(error.message)
    }
  }

  return (
    <Drawer
      open={open}
      width={mobile ? '100%' : 780}
      rootClassName="parser-control-root check-item-editor-drawer"
      title={checkItem ? '编辑巡检检查项' : '新增巡检检查项'}
      onClose={onClose}
      extra={(
        <Button
          type="primary"
          className="rule-asset-import-button"
          loading={loading}
          disabled={resultMode === 'structured' && Boolean(canonicalError)}
          onClick={() => void save()}
        >
          保存草稿并预览影响
        </Button>
      )}
    >
      <Alert
        type="info"
        showIcon
        message="先定义业务目标与标准输出，再配置厂商采集实现"
        description="保存会创建不可变候选版本，不会直接覆盖生产目录。发布新检查项后，还需关联采集实现并加入巡检 Profile。"
        style={{ marginBottom: 16 }}
      />
      <Form form={form} layout="vertical" requiredMark="optional">
        <div className="check-item-catalog-form-grid">
          <Form.Item
            label="稳定检查项 ID"
            name="id"
            rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_.-]+$/, message: '使用小写字母、数字、点、横线或下划线' }]}
          >
            <Input disabled={Boolean(checkItem)} placeholder="例如 routing.bgp_neighbor" />
          </Form.Item>
          <Form.Item label="检查项名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="例如 BGP 邻居状态" />
          </Form.Item>
          <Form.Item label="类别" name="category" rules={[{ required: true }]}>
            <Select
              showSearch
              options={['device', 'system', 'interface', 'routing', 'security', 'environment', 'other'].map((value) => ({ value, label: value }))}
            />
          </Form.Item>
          <Form.Item
            label="报告表格"
            name="report_projection"
            rules={[{ required: true }]}
            extra="自动投影会按当前版本的标准字段生成稳定列，并随运行证据冻结。"
          >
            <Select options={[
              { value: 'auto', label: '按标准字段自动生成' },
              ...(
                checkItem?.report_projection && checkItem.report_projection !== 'auto'
                  ? [{
                    value: checkItem.report_projection,
                    label: `沿用已发布投影（${checkItem.report_projection}）`,
                  }]
                  : []
              ),
            ]} />
          </Form.Item>
          <Form.Item label="结果模式" name="result_mode" rules={[{ required: true }]}>
            <Select options={[
              { value: 'structured', label: '结构化状态' },
              { value: 'normalized_diff', label: '规范化文本变化' },
              { value: 'hash_only', label: '原文哈希变化' },
              { value: 'evidence_only', label: '仅保留证据' },
            ]} />
          </Form.Item>
          <Form.Item label="评估模式" name="assessment_mode" rules={[{ required: true }]}>
            <Select options={[
              { value: 'health', label: '健康与风险' },
              { value: 'change', label: '变化跟踪' },
              { value: 'evidence', label: '审计证据' },
            ]} />
          </Form.Item>
          <Form.Item className="check-item-catalog-form-wide" label="说明" name="description">
            <Input.TextArea rows={2} placeholder="说明该检查项回答什么运维问题，以及报告如何使用结果" />
          </Form.Item>
        </div>

        <Typography.Title level={5}>标准输出</Typography.Title>
        {canonicalError && (
          <Alert
            type="error"
            showIcon
            message="标准观测目录加载失败"
            description={canonicalError}
            style={{ marginBottom: 16 }}
          />
        )}
        <div className="check-item-catalog-form-grid">
          <Form.Item label="实体类型" name="entity_type" rules={[{ required: true }]}>
            <Input placeholder="例如 bgp_neighbor" />
          </Form.Item>
          <Form.Item label="实体主键" name="identity_dimensions" rules={[{ required: true }]}>
            <Select mode="tags" tokenSeparators={[',']} placeholder="例如 device_id, vrf, peer_address" />
          </Form.Item>
          <div className="check-item-catalog-form-wide">
            <Form.List name="canonical_fields">
              {(fields, { add, remove }) => (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  {fields.map(({ key, name, ...restField }) => (
                    <div className="check-item-adapter-output" key={key}>
                      <div className="check-item-catalog-form-grid">
                        <Form.Item
                          {...restField}
                          label="标准列名"
                          name={[name, 'name']}
                          rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_]*$/, message: '使用稳定的小写 snake_case 名称' }]}
                        >
                          <Input placeholder="例如 cpu_usage_percent" />
                        </Form.Item>
                        <Form.Item
                          {...restField}
                          label="观测语义"
                          name={[name, 'observation_type']}
                          rules={[{ required: true }]}
                        >
                          <Select
                            showSearch
                            optionFilterProp="label"
                            loading={canonicalLoading}
                            options={(canonicalCatalog?.items || []).map((item) => ({
                              value: item.id,
                              label: `${item.id} · ${item.description || item.semantic_type}`,
                            }))}
                          />
                        </Form.Item>
                        <Form.Item
                          {...restField}
                          label="值类型"
                          name={[name, 'value_type']}
                          rules={[{ required: true }]}
                        >
                          <Select options={[
                            'text', 'number', 'percent', 'enum', 'boolean',
                            'interface_name', 'ip_or_unassigned',
                          ].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                        <Form.Item {...restField} label="单位" name={[name, 'unit']}>
                          <Input placeholder="例如 percent、dbm、bytes" />
                        </Form.Item>
                        <Form.Item
                          {...restField}
                          label="必填"
                          name={[name, 'required']}
                          valuePropName="checked"
                        >
                          <Switch />
                        </Form.Item>
                      </div>
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => remove(name)}
                      >
                        删除字段
                      </Button>
                    </div>
                  ))}
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    onClick={() => add({ value_type: 'text', required: true })}
                  >
                    添加标准字段
                  </Button>
                </Space>
              )}
            </Form.List>
          </div>
          <Form.Item className="check-item-catalog-form-wide">
            <Button onClick={synchronizeContract}>刷新标准输出契约</Button>
          </Form.Item>
        </div>

        {resultMode === 'structured' && (
          <>
            <Typography.Title level={5}>变化、当前值与事件</Typography.Title>
            <Alert
              type="info"
              showIcon
              message="这些规则属于检查项，所有厂商命令适配器共同复用"
              description="没有历史基线时仍执行当前值判断；事件只消费已发布的确定性规则。空数组表示该检查项暂不产生对应结论。"
              style={{ marginBottom: 16 }}
            />
            <Form.Item
              label="变化跟踪字段"
              name="tracked_fields"
              extra="只比较这些标准列。CPU 等瞬时指标可以不加入，避免产生无意义变化。"
            >
              <Select mode="tags" tokenSeparators={[',']} placeholder="例如 oper_status, description" />
            </Form.Item>
            <Form.Item
              label="当前值判断规则"
              name="evaluation_json"
              rules={[{ required: true }]}
              extra={'示例：[ {"id":"cpu_high","field":"cpu_usage_percent","operator":">=","value":80,"severity":"warning"} ]'}
            >
              <Input.TextArea rows={6} />
            </Form.Item>
            <Form.Item
              label="事件规则"
              name="events_json"
              rules={[{ required: true }]}
              extra={'示例：[ {"id":"InterfaceDown","when":{"from":"up","to":"down"}}, {"id":"InterfaceUp","recovery_of":"InterfaceDown","when":{"to":"up"}} ]'}
            >
              <Input.TextArea rows={6} />
            </Form.Item>
          </>
        )}

        <Typography.Title level={5}>证据完整性策略</Typography.Title>
        <Typography.Paragraph type="secondary">
          明确结果时效、空输出解释与实体覆盖范围。健康检查项必须完整配置，避免运行时把未知或不完整数据误判为正常。
        </Typography.Paragraph>
        <div className="check-item-catalog-form-grid">
          <Form.Item
            label="最大观测年龄"
            name="max_observation_age"
            rules={assessmentMode === 'health' ? [{ required: true }] : undefined}
          >
            <Input placeholder="例如 24h" />
          </Form.Item>
          <Form.Item
            label="最大采集跨度"
            name="max_collection_span"
            rules={assessmentMode === 'health' ? [{ required: true }] : undefined}
          >
            <Input placeholder="例如 8h" />
          </Form.Item>
          <Form.Item
            label="空结果分类"
            name="empty_classification"
            rules={assessmentMode === 'health' ? [{ required: true }] : undefined}
          >
            <Select options={[
              { value: 'invalid', label: '无效证据' },
              { value: 'expected_empty', label: '预期为空' },
              { value: 'not_applicable', label: '不适用' },
            ]} />
          </Form.Item>
          <Form.Item
            label="覆盖模型"
            name="coverage_model"
            rules={assessmentMode === 'health' ? [{ required: true }] : undefined}
          >
            <Select options={[
              { value: 'fixed', label: '固定实体数量' },
              { value: 'derived_entity_set', label: '基于历史合格观测' },
              { value: 'inventory_entity_set', label: '基于设备清单' },
              { value: 'explicit_entity_set', label: '显式实体集合' },
              { value: 'non_exhaustive', label: '非穷举观测' },
            ]} />
          </Form.Item>
          {coverageModel === 'fixed' && (
            <Form.Item
              label="预期实体数量"
              name="expected_count"
              rules={[{ required: true }]}
            >
              <Input type="number" min={1} placeholder="例如 1" />
            </Form.Item>
          )}
          <Form.Item label="覆盖基准来源" name="coverage_source">
            <Input placeholder="例如 previous_qualified_observation" />
          </Form.Item>
          <Form.Item label="首次观测处理" name="first_observation">
            <Input placeholder="例如 unable_to_determine" />
          </Form.Item>
        </div>

        <Collapse
          ghost
          items={[{
            key: 'advanced',
            forceRender: true,
            label: '高级配置',
            children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Form.Item label="标准输出契约 JSON" name="canonical_contract_json" rules={[{ required: true }]}>
                  <Input.TextArea rows={12} />
                </Form.Item>
                <Form.Item label="适用条件 JSON" name="applicability_json" rules={[{ required: true }]}>
                  <Input.TextArea rows={5} />
                </Form.Item>
              </Space>
            ),
          }]}
        />
        <Form.Item label="修订备注" name="reason">
          <Input.TextArea rows={3} placeholder="可选；版本差异和操作审计会自动留痕" />
        </Form.Item>
      </Form>
    </Drawer>
  )
}

export default CheckItemEditorDrawer
