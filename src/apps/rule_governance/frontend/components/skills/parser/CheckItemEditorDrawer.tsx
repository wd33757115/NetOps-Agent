import React, { useEffect, useMemo, useRef, useState } from 'react'
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
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'

import type {
  CanonicalRuleCandidates,
  CanonicalObservationCatalog,
  CheckItemCatalogCheckItemDraftInput,
  CheckItemRelations,
  ParserCheckItem,
} from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import CheckItemRelationGraph from './CheckItemRelationGraph'
import FieldSemanticEditor, { pickUse, trendKindForUnit, type FieldRulePreview, type FieldRulesInForce } from './FieldSemanticEditor'
import RuleGuideDialog from './RuleGuideDialog'

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
  entity_type: string
  identity_dimensions: string[]
  canonical_fields: Array<{
    name: string
    observation_type: string
    value_type: string
    unit?: string
    required: boolean
    change_tracking: boolean
    semantic_definition?: any
    compare_mode?: string
    threshold_high?: string[]
    threshold_low?: string[]
    trend?: {
      kind: string
      ratio_percent?: number
      min_increase_points?: number
      identity_fields?: string[]
      require_identity?: boolean
    }
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

const modeLabels: Record<string, string> = { health: '健康与风险', change: '变化跟踪', evidence: '审计证据' }

/** The check item judges health if any Value does, else change, else nothing. */
const deriveMode = (fields: CheckItemFormValues['canonical_fields']): CheckItemFormValues['assessment_mode'] => {
  const uses = fields.map((field) => pickUse(field?.semantic_definition?.assessment_roles, field?.semantic_definition?.semantic_type))
  return uses.includes('current_state') ? 'health' : uses.includes('change') ? 'change' : 'evidence'
}

const referenceNames = (references: any, roles: string[]) => roles
  .map((role) => references?.[role]?.field_name).filter(Boolean).map(String)

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
    entity_type: String(contract.entity_type || 'device'),
    identity_dimensions: Array.isArray(contract.identity_dimensions)
      ? contract.identity_dimensions.map(String)
      : ['device_id'],
    canonical_fields: observations.map((item) => {
      const snapshot = (contract.rule_semantics_snapshot as any)?.observations || []
      const definition = item.semantic_definition || snapshot.find((row: any) => row.id === item.observation_type)
        || catalog?.items.find((row) => row.id === item.observation_type)
      const policy = item.health_policy as any
      // A Value the runtime judges from its health policy is a health Value, whatever its stored role says.
      const semantic = definition ? { ...definition as any,
        assessment_roles: [pickUse(policy ? ['current_state'] : item.assessment_roles as string[] || (definition as any).assessment_roles,
          String(item.semantic_type || (definition as any).semantic_type))] } : undefined
      return {
        name: String(item.canonical_field || item.observation_type || ''),
        observation_type: String(item.observation_type || ''),
        value_type: String(item.value_type || semantic?.value_types?.[0] || 'text'),
        unit: String(item.unit || semantic?.canonical_unit || ''),
        required: item.required !== false,
        change_tracking: item.change_tracking !== false,
        semantic_definition: semantic,
        compare_mode: (item.policy_constraints as any)?.allowed_compare_modes?.length === 1
          ? (item.policy_constraints as any).allowed_compare_modes[0]
          : (item.policy_constraints as any)?.allowed_compare_modes?.length ? '__existing__' : undefined,
        threshold_high: referenceNames(item.threshold_references, ['high', 'critical_high']),
        threshold_low: referenceNames(item.threshold_references, ['low', 'critical_low']),
        trend: policy?.trend ? {
          kind: policy.trend.kind,
          ratio_percent: Math.round(Number(policy.trend.threshold_ratio) * 100),
          min_increase_points: policy.trend.min_increase_points,
          identity_fields: policy.trend.identity_fields || [],
          require_identity: policy.trend.require_identity !== false,
        } : { kind: 'none' },
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
  const selectedMode = Form.useWatch('assessment_mode', form)
  const coverageModel = Form.useWatch('coverage_model', form)
  const canonicalFields = Form.useWatch('canonical_fields', form) || []
  const identityDimensions = Form.useWatch('identity_dimensions', form)
  const contractJson = Form.useWatch('canonical_contract_json', form)
  const structured = resultMode === 'structured'
  const assessmentMode = structured ? deriveMode(canonicalFields) : selectedMode
  const [ruleCandidates, setRuleCandidates] = useState<CanonicalRuleCandidates | null>(null)
  const [ruleDraftLoading, setRuleDraftLoading] = useState(false)
  const [preview, setPreview] = useState<{
    error?: string; fields: Record<string, FieldRulePreview>; graph?: CheckItemRelations
  }>({ fields: {} })
  const [guide, setGuide] = useState<{ open: boolean; row?: string; field?: string }>({ open: false })
  const previewRequest = useRef(0)
  // Every field offers the whole template catalog; build the list once, not per field per keystroke.
  const observationOptions = useMemo(() => (canonicalCatalog?.items || []).map((item) => ({
    value: item.id,
    label: `${item.id} · ${item.description || item.semantic_type}`,
  })), [canonicalCatalog])
  const [inForce, setInForce] = useState<{ leftover: number; fields: Record<string, FieldRulesInForce> }>({ leftover: 0, fields: {} })

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
      const state = ['state', 'enum', 'boolean'].includes(semantic.semantic_type)
      if (!state) { semantic.state_groups = {}; semantic.allowed_values = [] }
      // The form shows every group; one nobody filled in is no group at all.
      else semantic.state_groups = Object.fromEntries(
        Object.entries(semantic.state_groups || {}).filter(([, values]) => Array.isArray(values)))
      if (semantic.value_types[0] === 'boolean') {
        const booleanValue = (value: unknown) => value === 'true' ? true : value === 'false' ? false : value
        semantic.allowed_values = (semantic.allowed_values || []).map(booleanValue)
        semantic.state_groups = Object.fromEntries(Object.entries(semantic.state_groups || {}).map(([key, values]) => [key, (values as unknown[]).map(booleanValue)]))
      }
      const use = pickUse(semantic.assessment_roles, semantic.semantic_type)
      semantic.assessment_roles = [use]
      const numericHealth = semantic.semantic_type === 'gauge' && use === 'current_state'
      const constraints = { ...(existing?.policy_constraints as any || {}) }
      if (use !== 'change' || field.compare_mode === '__existing__') { /* Preserve advanced constraints. */ }
      else if (field.compare_mode) constraints.allowed_compare_modes = [field.compare_mode]
      else delete constraints.allowed_compare_modes
      // Limits come from fields the device reports; two on one side means the stricter one wins.
      const chosen = numericHealth ? [
        ...(field.threshold_high || []).slice(0, 2).map((value, index) => [index ? 'critical_high' : 'high', value]),
        ...(field.threshold_low || []).slice(0, 2).map((value, index) => [index ? 'critical_low' : 'low', value]),
      ] : []
      const references = Object.fromEntries(chosen.map(([role, value]) => [role, (existing?.threshold_references as any)?.[role]?.field_name === value ? (existing?.threshold_references as any)[role] : {
        field_name: value, source: 'structured_reference',
      }]))
      const trend = numericHealth && field.trend?.kind === trendKindForUnit(field.unit) ? field.trend : undefined
      if (trend && (!trend.ratio_percent || (trend.kind === 'utilization_increase' && !trend.min_increase_points))) {
        throw new Error(`${field.name}: 请展开“编辑语义与规则用途”填写趋势提醒的门槛`)
      }
      // The server compiles the health policy from the limits above; only the trend is ours to state.
      const { health_policy: _compiled, ...kept } = (existing || {}) as Record<string, unknown>
      return {
        ...kept,
        ...(trend ? { health_policy: { trend: trend.kind === 'utilization_increase'
          ? { kind: trend.kind, threshold_ratio: trend.ratio_percent! / 100, min_increase_points: trend.min_increase_points }
          : { kind: trend.kind, threshold_ratio: trend.ratio_percent! / 100,
            identity_fields: trend.identity_fields || [], require_identity: trend.require_identity !== false } } } : {}),
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
        // The form has no control for dimensions, and adapters output a field by the ones it was
        // published with: a field already there keeps exactly its own, never the definition's.
        dimensions: existing ? existing.dimensions || [] : definition?.dimensions || [],
        required: field.required,
        change_tracking: use === 'change' || field.change_tracking !== false,
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
      // Each Value states its own use, so no check-item switch overrides them.
      change_events: false,
      value_contract: 'same-name/v1',
      entity_type: values.entity_type,
      identity_dimensions: values.identity_dimensions || [],
      observations,
      parser_field_vocabulary: vocabulary,
    }
  }

  // What the published version does today, to set beside what the draft will do.
  useEffect(() => {
    setInForce({ leftover: 0, fields: {} })
    if (!open || !checkItem?.check_item_id || checkItem.status !== 'active') return
    let current = true
    parserApi.checkItemRulesInForce(checkItem.check_item_id).then((result) => {
      if (current) setInForce({ leftover: result.leftover_count,
        fields: Object.fromEntries(result.fields.map((row) => [row.canonical_field, row])) })
    }).catch(() => undefined)
    return () => { current = false }
  }, [open, checkItem?.check_item_id, checkItem?.check_item_version_id])

  // Show what the draft will do while it is edited, using the functions that save and compile it.
  useEffect(() => {
    if (!open || !structured) return
    const request = ++previewRequest.current
    const timer = window.setTimeout(async () => {
      let next: typeof preview
      try {
        const values = form.getFieldsValue(true) as CheckItemFormValues
        const result = await parserApi.previewCheckItemRules({
          id: values.id || '', result_mode: 'structured', canonical_contract: buildCanonicalContract(values),
        })
        next = result.valid
          ? { fields: Object.fromEntries(result.fields.map((row) => [row.canonical_field, row])), graph: result.graph }
          : { error: result.errors.join('；'), fields: {} }
      } catch (error: any) {
        next = { error: error?.response ? '规则预览暂不可用' : error?.message, fields: {} }
      }
      if (request === previewRequest.current) setPreview(next)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [open, structured, JSON.stringify(canonicalFields), JSON.stringify(identityDimensions), contractJson])

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
        assessment_mode: values.result_mode === 'structured' ? deriveMode(values.canonical_fields || []) : values.assessment_mode,
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
          {structured
            ? <Form.Item label="评估模式" extra="由下面各字段的用途决定：有健康判断即健康与风险，否则有变化检测即变化跟踪。">
              <Input aria-label="评估模式" readOnly value={modeLabels[assessmentMode]} />
            </Form.Item>
            : <Form.Item label="评估模式" name="assessment_mode" rules={[{ required: true }]}>
              <Select options={Object.entries(modeLabels).map(([value, label]) => ({ value, label }))} />
            </Form.Item>}
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
                              form.setFieldValue(['canonical_fields', name, 'threshold_high'], [])
                              form.setFieldValue(['canonical_fields', name, 'threshold_low'], [])
                              form.setFieldValue(['canonical_fields', name, 'trend'], { kind: 'none' })
                            }}
                            options={observationOptions}
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
                          tooltip="打开后，缺少这个值的记录会被整条丢弃，每个适配器也都必须提供它。只用于对象标识和这个检查项的核心值；其他字段保持关闭，各适配器能提供多少就提供多少。"
                          name={[name, 'required']}
                          valuePropName="checked"
                        >
                          <Switch />
                        </Form.Item>
                      </div>
                      <FieldSemanticEditor name={name} field={canonicalFields[name]} fields={canonicalFields}
                        preview={preview.fields[canonicalFields[name]?.name]}
                        inForce={inForce.fields[canonicalFields[name]?.name]}
                        onOpenGuide={(row, field) => setGuide({ open: true, row, field })} />
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
                  <Space wrap>
                    <Button
                      type="dashed"
                      icon={<PlusOutlined />}
                      onClick={() => add({ value_type: 'text', required: false, trend: { kind: 'none' }, semantic_definition: { description: '', semantic_type: 'text', assessment_roles: ['evidence'], dimensions: [], allowed_values: [], state_groups: {}, allowed_dimension_values: {} } })}
                    >
                      添加标准字段
                    </Button>
                    {canonicalFields.some((field) => pickUse(field?.semantic_definition?.assessment_roles, field?.semantic_definition?.semantic_type) === 'evidence' && field?.semantic_definition?.semantic_type !== 'threshold') && (
                      <Button onClick={() => form.setFieldValue('canonical_fields', canonicalFields.map((field) => (
                        pickUse(field?.semantic_definition?.assessment_roles, field?.semantic_definition?.semantic_type) === 'evidence' && field?.semantic_definition?.semantic_type !== 'threshold'
                          ? { ...field, change_tracking: true, semantic_definition: { ...field.semantic_definition, assessment_roles: ['change'] } }
                          : field)))}>
                        全部“仅展示”字段改为“变化检测”
                      </Button>
                    )}
                  </Space>
                </Space>
              )}
            </Form.List>
          </div>
          <Typography.Text type="secondary">已有语义用于预填，也可输入新标识并编辑含义。修改仅保存到当前检查项。</Typography.Text>
        </div>

        {structured && (
          <>
            <div className="check-item-editor-heading">
              <Typography.Title level={5}>巡检时会做什么</Typography.Title>
              <Button size="small" onClick={() => setGuide({ open: true })}>规则是怎么生成的</Button>
            </div>
            {preview.error
              ? <Alert type="warning" showIcon message="还不能生成规则" description={preview.error} style={{ marginBottom: 16 }} />
              : <Alert type="info" showIcon style={{ marginBottom: 16 }}
                message={`评估模式：${modeLabels[assessmentMode]}`}
                description={checkItem
                  ? '每个字段下方先写保存并发布后的判断，随表单即时更新；与现在生效的规则不同时，会把现在生效的内容一并列出。'
                  : '每个字段下方的说明就是巡检实际执行的判断，随表单即时更新；发布后生效。'} />}
            {preview.graph && !preview.error && (
              <div className="check-item-editor-graph">
                <Typography.Text type="secondary">
                  {preview.graph.mode === 'diff'
                    ? '下图是这份草稿会生成的规则和事件，和已发布的版本不同的地方标了出来。随表单更新，保存前不生效。'
                    : '下图是这份草稿会生成的规则和事件。随表单更新，发布后生效。'}
                </Typography.Text>
                <CheckItemRelationGraph checkItemId={checkItem?.check_item_id || ''} graph={preview.graph} />
              </div>
            )}
            {inForce.leftover > 0 && (
              <Alert type="warning" showIcon style={{ marginBottom: 16 }}
                message={`另有 ${inForce.leftover} 条规则绑定在旧版本已不存在的字段上，仍为生效状态`}
                description="它们不对应当前版本的任何字段，可在“已发布资产”中按检查项 ID 搜索并停用。" />
            )}
            {checkItem && (
              <Space direction="vertical" size={8} style={{ width: '100%', marginBottom: 16 }}>
                {!ruleCandidates?.drafts_created && (
                  <Button loading={ruleDraftLoading} onClick={() => void createRuleDrafts()}>
                    为已发布版本创建规则草稿
                  </Button>
                )}
                {ruleCandidates?.drafts_created && (
                  <Typography.Text type="success">
                    {ruleCandidates.bundle_id
                      ? '规则草稿已进入工程复核与审批发布链。'
                      : '该检查项没有需要发布的变化策略或事件规则。'}
                  </Typography.Text>
                )}
              </Space>
            )}
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
      <RuleGuideDialog
        open={guide.open}
        current={guide.row ? { row: guide.row, field: guide.field } : undefined}
        onClose={() => setGuide({ open: false })}
      />
    </Drawer>
  )
}

export default CheckItemEditorDrawer
