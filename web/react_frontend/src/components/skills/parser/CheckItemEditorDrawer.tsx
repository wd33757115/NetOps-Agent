import React, { useEffect } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Typography,
  message,
} from 'antd'

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
  observation_types: string[]
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
    observation_types: observations
      .map((item) => String(item.observation_type || ''))
      .filter(Boolean),
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
  const coverageModel = Form.useWatch('coverage_model', form)
  const resultMode = Form.useWatch('result_mode', form)

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
    const observations = (values.observation_types || []).map((observationType) => {
      const existing = existingRows.get(observationType)
      if (existing) return existing
      const definition = definitions.get(observationType)
      return {
        observation_type: observationType,
        semantic_type: definition?.semantic_type,
        ...(definition?.canonical_unit ? { unit: definition.canonical_unit } : {}),
        ...(definition?.dimensions?.length ? { dimensions: definition.dimensions } : {}),
        required: true,
      }
    })
    return {
      ...contract,
      entity_type: values.entity_type,
      identity_dimensions: values.identity_dimensions || [],
      observations,
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
      if (values.result_mode === 'structured' && !(values.observation_types || []).length) {
        form.setFields([{
          name: 'observation_types',
          errors: ['结构化检查项至少需要一个标准观测字段'],
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
          <Form.Item className="check-item-catalog-form-wide" label="标准观测字段" name="observation_types">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              loading={canonicalLoading}
              placeholder="从平台已批准的厂商无关字段中选择"
              options={(canonicalCatalog?.items || []).map((item) => ({
                value: item.id,
                label: `${item.id} · ${item.description || item.semantic_type}`,
              }))}
            />
          </Form.Item>
          <Form.Item className="check-item-catalog-form-wide">
            <Button onClick={synchronizeContract}>刷新标准输出契约</Button>
          </Form.Item>
        </div>

        <Typography.Title level={5}>证据与完整性</Typography.Title>
        <div className="check-item-catalog-form-grid">
          <Form.Item label="观测最大年龄" name="max_observation_age" rules={[{ required: true }]}>
            <Input placeholder="24h" />
          </Form.Item>
          <Form.Item label="单次采集最大跨度" name="max_collection_span" rules={[{ required: true }]}>
            <Input placeholder="8h" />
          </Form.Item>
          <Form.Item label="空结果含义" name="empty_classification" rules={[{ required: true }]}>
            <Select options={[
              { value: 'invalid', label: '无效，不能判断' },
              { value: 'expected_empty', label: '预期为空' },
              { value: 'not_applicable', label: '不适用' },
              { value: 'unsupported', label: '设备不支持' },
            ]} />
          </Form.Item>
          <Form.Item label="覆盖模型" name="coverage_model" rules={[{ required: true }]}>
            <Select options={[
              { value: 'fixed', label: '固定实体数' },
              { value: 'inventory_entity_set', label: '资产清单实体集' },
              { value: 'derived_entity_set', label: '历史/关联结果推导' },
              { value: 'command_reported_total', label: '命令声明总数' },
              { value: 'non_exhaustive', label: '非穷举证据' },
            ]} />
          </Form.Item>
          {coverageModel === 'fixed' && (
            <Form.Item label="预期记录数" name="expected_count" rules={[{ required: true }]}>
              <InputNumber min={0} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          )}
          <Form.Item label="覆盖基准来源" name="coverage_source">
            <Input placeholder="例如 previous_qualified_observation" />
          </Form.Item>
          <Form.Item label="首次巡检处理" name="first_observation">
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
