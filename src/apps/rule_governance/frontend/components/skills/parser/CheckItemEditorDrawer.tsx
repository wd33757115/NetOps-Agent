import React, { useEffect, useState } from 'react'
import {
  Alert,
  AutoComplete,
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'

import type {
  CanonicalRuleCandidates,
  CanonicalObservationCatalog,
  CheckItemCatalogCheckItemDraftInput,
  ParserCheckItem,
} from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import FieldSemanticEditor from './FieldSemanticEditor'

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
  onOpenRuleBundle?: (bundleId: string, checkItemVersionId: string) => void
}

interface CheckItemFormValues {
  id: string
  name: string
  category: string
  description?: string
  result_mode: CheckItemCatalogCheckItemDraftInput['result_mode']
  assessment_mode: CheckItemCatalogCheckItemDraftInput['assessment_mode']
  change_events?: boolean
  entity_type: string
  identity_dimensions: string[]
  canonical_fields: Array<{
    name: string
    observation_type: string
    value_type: string
    unit?: string
    required: boolean
    semantic_definition?: any
    compare_mode?: string
    threshold_fields?: Record<string, string>
  }>
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

const initialValues = (checkItem?: ParserCheckItem, catalog?: CanonicalObservationCatalog): CheckItemFormValues => {
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
    change_events: typeof contract.change_events === 'boolean' ? contract.change_events : checkItem ? undefined : false,
    entity_type: String(contract.entity_type || 'device'),
    identity_dimensions: Array.isArray(contract.identity_dimensions)
      ? contract.identity_dimensions.map(String)
      : ['device_id'],
    canonical_fields: observations.map((item) => {
      const snapshot = (contract.rule_semantics_snapshot as any)?.observations || []
      const definition = item.semantic_definition || snapshot.find((row: any) => row.id === item.observation_type)
        || catalog?.items.find((row) => row.id === item.observation_type)
      const semantic = definition ? { ...definition as any,
        assessment_roles: item.assessment_roles || (definition as any).assessment_roles } : undefined
      return {
        name: String(item.canonical_field || item.observation_type || ''),
        observation_type: String(item.observation_type || ''),
        value_type: String(item.value_type || semantic?.value_types?.[0] || 'text'),
        unit: String(item.unit || semantic?.canonical_unit || ''),
        required: item.required !== false,
        semantic_definition: semantic,
        compare_mode: (item.policy_constraints as any)?.allowed_compare_modes?.length === 1
          ? (item.policy_constraints as any).allowed_compare_modes[0]
          : (item.policy_constraints as any)?.allowed_compare_modes?.length ? '__existing__' : undefined,
        threshold_fields: Object.fromEntries(Object.entries(item.threshold_references || {}).map(([role, ref]) => [role, (ref as any).field_name])),
      }
    }),
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
  onOpenRuleBundle,
}) => {
  const [form] = Form.useForm<CheckItemFormValues>()
  const resultMode = Form.useWatch('result_mode', form)
  const assessmentMode = Form.useWatch('assessment_mode', form)
  const coverageModel = Form.useWatch('coverage_model', form)
  const canonicalFields = Form.useWatch('canonical_fields', form) || []
  const [ruleCandidates, setRuleCandidates] = useState<CanonicalRuleCandidates | null>(null)
  const [rulePreviewLoading, setRulePreviewLoading] = useState(false)
  const [ruleDraftLoading, setRuleDraftLoading] = useState(false)
  const publishedRows = Array.isArray(checkItem?.canonical_contract?.observations)
    ? checkItem.canonical_contract.observations as Array<Record<string, unknown>>
    : []

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initialValues(checkItem, canonicalCatalog))
      setRuleCandidates(null)
    }
  }, [checkItem, form, open])

  useEffect(() => {
    if (!open || !canonicalCatalog) return
    const fields = form.getFieldValue('canonical_fields') || []
    fields.forEach((field: any, index: number) => {
      if (field.semantic_definition) return
      const definition = canonicalCatalog.items.find((item) => item.id === field.observation_type)
      if (definition) form.setFieldValue(['canonical_fields', index, 'semantic_definition'], definition)
    })
  }, [canonicalCatalog, form, open])

  const buildCanonicalContract = (values: CheckItemFormValues) => {
    const contract = jsonObject(values.canonical_contract_json, '标准输出契约')
    const canonicalBase = { ...contract }
    delete canonicalBase.change
    delete canonicalBase.evaluation
    delete canonicalBase.events
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
      const definition = field.semantic_definition || definitions.get(observationType)
      if (!definition?.description?.trim()) throw new Error(`${field.name}: 请展开“编辑语义与规则用途”填写语义说明`)
      const semantic = { ...definition, id: observationType,
        canonical_unit: field.unit?.trim() || null, value_types: [field.value_type] }
      const numeric = ['gauge', 'counter', 'threshold'].includes(semantic.semantic_type)
      const state = ['state', 'enum', 'boolean'].includes(semantic.semantic_type)
      if (!state) { semantic.state_groups = {}; semantic.allowed_values = [] }
      if (semantic.value_types[0] === 'boolean') {
        const booleanValue = (value: unknown) => value === 'true' ? true : value === 'false' ? false : value
        semantic.allowed_values = (semantic.allowed_values || []).map(booleanValue)
        semantic.state_groups = Object.fromEntries(Object.entries(semantic.state_groups || {}).map(([key, values]) => [key, (values as unknown[]).map(booleanValue)]))
      }
      const constraints = { ...(existing?.policy_constraints as any || {}) }
      if (field.compare_mode === '__existing__') { /* Preserve advanced multi-mode constraints. */ }
      else if (field.compare_mode) constraints.allowed_compare_modes = [field.compare_mode]
      else delete constraints.allowed_compare_modes
      const references = numeric ? Object.fromEntries(Object.entries(field.threshold_fields || {}).filter(([, value]) => value).map(([role, value]) => [role, (existing?.threshold_references as any)?.[role]?.field_name === value ? (existing?.threshold_references as any)[role] : {
        ...((existing?.threshold_references as any)?.[role] || {}), field_name: value,
        context_key: `threshold.${role}`, source: 'structured_reference',
        level: role.startsWith('critical') ? 'alarm' : 'warning',
      }])) : {}
      return {
        ...(existing || {}),
        observation_type: observationType,
        canonical_field: field.name.trim(),
        semantic_definition: semantic,
        description: semantic.description,
        semantic_type: semantic.semantic_type,
        assessment_roles: semantic.assessment_roles,
        policy_constraints: constraints,
        threshold_references: references,
        context_fields: [...new Set([...(existing?.context_fields as string[] || []).filter((value) => !Object.values((existing?.threshold_references as any) || {}).some((ref: any) => ref.field_name === value)), ...Object.values(references).map((ref: any) => ref.field_name)])],
        value_type: field.value_type,
        unit: field.unit?.trim() || null,
        dimensions: existing?.dimensions || definition?.dimensions || [],
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
      ...canonicalBase,
      ...(typeof values.change_events === 'boolean' ? { change_events: values.change_events, value_contract: 'same-name/v1' } : {}),
      entity_type: values.entity_type,
      identity_dimensions: values.identity_dimensions || [],
      observations,
      parser_field_vocabulary: vocabulary,
    }
  }

  const previewPublishedRules = async () => {
    if (!checkItem?.check_item_version_id) return
    setRulePreviewLoading(true)
    try {
      const result = await parserApi.generateCheckItemRuleCandidates(
        checkItem.check_item_version_id,
      )
      setRuleCandidates(result)
      if (result.success) message.success('规则候选已按标准语义生成')
      else message.warning('标准语义仍有缺项，请按阻塞提示补充')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '规则候选生成失败')
    } finally {
      setRulePreviewLoading(false)
    }
  }

  const createRuleDrafts = async () => {
    if (!checkItem?.check_item_version_id) return
    setRuleDraftLoading(true)
    try {
      const result = await parserApi.createCheckItemRuleCandidateDrafts(
        checkItem.check_item_version_id,
      )
      setRuleCandidates(result)
      if (result.bundle_id) {
        message.success('规则草稿已创建，正在打开工程复核与审批')
        onOpenRuleBundle?.(result.bundle_id, checkItem.check_item_version_id)
      } else {
        message.success('该检查项不需要变化策略或事件规则草稿')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '规则草稿创建失败')
    } finally {
      setRuleDraftLoading(false)
    }
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
        assessment_mode: values.change_events === true && values.assessment_mode === 'evidence' ? 'change' : values.assessment_mode,
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
          <Form.Item label="启用全部字段变化事件" name="change_events" valuePropName="checked" extra="开启后每个检查字段的变化都会产生信息事件；对象标识用于匹配。TextFSM Value 必须使用这里定义的字段名。">
            <Switch />
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
              message="预填语义目录加载失败；仍可编辑当前语义或创建新语义"
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
                          rules={[{ required: true }, { pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: '使用字母开头的字母、数字或下划线；名称区分大小写' }]}
                        >
                          <Input placeholder="例如 cpu_usage_percent" />
                        </Form.Item>
                        <Form.Item
                          {...restField}
                          label="观测语义"
                          name={[name, 'observation_type']}
                          rules={[{ required: true }]}
                        >
                          <AutoComplete
                            placeholder="输入新语义标识，或搜索已有模板"
                            filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
                            onSelect={(id) => {
                              const definition = canonicalCatalog?.items.find((item) => item.id === id)
                              if (!definition) return
                              form.setFieldValue(['canonical_fields', name, 'semantic_definition'], { ...definition, state_groups: { ...definition.state_groups } })
                              form.setFieldValue(['canonical_fields', name, 'value_type'], definition.value_types[0] || 'text')
                              form.setFieldValue(['canonical_fields', name, 'unit'], definition.canonical_unit || '')
                              form.setFieldValue(['canonical_fields', name, 'compare_mode'], undefined)
                              form.setFieldValue(['canonical_fields', name, 'threshold_fields'], {})
                            }}
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
                            'text', 'number', 'integer', 'float', 'percent', 'enum', 'boolean',
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
                      <FieldSemanticEditor name={name} field={canonicalFields[name]} fields={canonicalFields} />
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
                    onClick={() => add({ value_type: 'text', required: true, semantic_definition: { description: '', semantic_type: 'text', assessment_roles: ['evidence'], dimensions: [], allowed_values: [], state_groups: {}, allowed_dimension_values: {} } })}
                  >
                    添加标准字段
                  </Button>
                </Space>
              )}
            </Form.List>
          </div>
          <Typography.Text type="secondary">已有语义用于预填，也可输入新标识并编辑含义。修改仅保存到当前检查项。</Typography.Text>
        </div>

        {resultMode === 'structured' && (
          <>
            <Typography.Title level={5}>规则生成依据</Typography.Title>
            <Alert
              type="info"
              showIcon
              message="变化策略和事件规则只读取这里的标准语义"
              description="检查项统一定义字段名称和语义。新契约要求 TextFSM 使用同名 Value；启用全部字段变化事件后，系统直接为每个检查字段生成变化规则与信息事件。"
              style={{ marginBottom: 16 }}
            />
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {canonicalFields.map((field) => {
                const definition = field.semantic_definition || canonicalCatalog?.items.find(
                  (item) => item.id === field.observation_type,
                )
                if (!definition) return null
                const published = publishedRows.find((row) => (
                  String(row.observation_type || '') === field.observation_type
                  && String(row.canonical_field || '') === field.name
                )) || {}
                const thresholdReferences = (
                  published.threshold_references
                  && typeof published.threshold_references === 'object'
                  && !Array.isArray(published.threshold_references)
                ) ? published.threshold_references as Record<string, Record<string, unknown>> : {}
                const baseline = (
                  published.baseline_comparison
                  && typeof published.baseline_comparison === 'object'
                  && !Array.isArray(published.baseline_comparison)
                ) ? published.baseline_comparison as Record<string, unknown> : {}
                const identityFields = Array.isArray(baseline.identity_fields)
                  ? baseline.identity_fields.map(String)
                  : []
                return (
                  <div className="check-item-adapter-output" key={`${field.name}-${field.observation_type}`}>
                    <Space wrap>
                      <Typography.Text strong>{field.name || field.observation_type}</Typography.Text>
                      <Tag>{definition.semantic_type}</Tag>
                      {definition.assessment_roles.map((role) => <Tag key={role} color="blue">{role}</Tag>)}
                      {Object.keys(thresholdReferences).length > 0 && (
                        <Tag color="cyan">动态阈值</Tag>
                      )}
                      {baseline.kind === 'linear_power_loss_ratio' && (
                        <Tag color="purple">线性功率基线</Tag>
                      )}
                    </Space>
                    {Object.keys(definition.state_groups || {}).length > 0 && (
                      <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
                        状态分组：{Object.entries(definition.state_groups).map(([group, values]) => (
                          `${group}=${(values as unknown[]).map(String).join('/')}`
                        )).join('；')}
                      </Typography.Paragraph>
                    )}
                    {Object.keys(thresholdReferences).length > 0 && (
                      <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
                        阈值来源：设备输出优先，其次为设备/型号策略；不使用全局固定值。字段关系：{
                          Object.entries(thresholdReferences).map(([role, reference]) => (
                            `${role}→${String(reference.field_name || '未配置')}`
                          )).join('；')
                        }
                      </Typography.Paragraph>
                    )}
                    {baseline.kind === 'linear_power_loss_ratio' && (
                      <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
                        基线：先将 dBm 转换为线性功率，允许衰减 {
                          Number(baseline.max_loss_ratio || 0) * 100
                        }%；模块身份字段：{identityFields.join(' + ') || '未配置'}。身份缺失或变化时结果为 Unknown。
                      </Typography.Paragraph>
                    )}
                  </div>
                )
              })}
              {checkItem && (
                <Button loading={rulePreviewLoading} onClick={() => void previewPublishedRules()}>
                  按当前已发布语义预览规则
                </Button>
              )}
              {ruleCandidates && (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Alert
                    type={ruleCandidates.success ? 'success' : 'warning'}
                    showIcon
                    message={ruleCandidates.success
                      ? `已生成 ${ruleCandidates.change_policies.length} 个变化策略、${ruleCandidates.event_rules.length} 条事件规则`
                      : '标准语义还不足以生成完整规则'}
                    description={ruleCandidates.success
                      ? '输入中包含 0 条样本、0 个 Parser 模板。'
                      : ruleCandidates.blockers.map((item) => (
                        `${item.observation_type}：${item.message}`
                      )).join('；')}
                  />
                  {ruleCandidates.success && !ruleCandidates.drafts_created && (
                    <Button
                      type="primary"
                      loading={ruleDraftLoading}
                      onClick={() => void createRuleDrafts()}
                    >
                      创建规则草稿
                    </Button>
                  )}
                  {ruleCandidates.success && ruleCandidates.change_policies.map((policy, index) => {
                    const references = policy.threshold_references as Record<string, Record<string, unknown>> | undefined
                    const baseline = policy.baseline_comparison as Record<string, unknown> | undefined
                    if (!references && !baseline) return null
                    return (
                      <div className="check-item-adapter-output" key={`${String(policy.observation_type)}-${index}`}>
                        <Typography.Text strong>{String(policy.observation_type)}</Typography.Text>
                        {references && (
                          <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                            动态阈值：{Object.entries(references).map(([role, value]) => `${role}→${String(value.field_name || '')}`).join('；')}
                          </Typography.Paragraph>
                        )}
                        {baseline && (
                          <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                            基线衰减上限：{Number(baseline.max_loss_ratio || 0) * 100}%（线性功率）
                          </Typography.Paragraph>
                        )}
                      </div>
                    )
                  })}
                  {ruleCandidates.drafts_created && (
                    <Typography.Text type="success">
                      {ruleCandidates.bundle_id
                        ? '规则草稿已进入工程复核与审批发布链。'
                        : '该检查项没有需要发布的变化策略或事件规则。'}
                    </Typography.Text>
                  )}
                </Space>
              )}
            </Space>
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
