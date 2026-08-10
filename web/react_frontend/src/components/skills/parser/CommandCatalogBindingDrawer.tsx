import React, { useDeferredValue, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Form,
  Grid,
  Input,
  InputNumber,
  Select,
  Space,
  Typography,
  message,
} from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  parserApi,
  type CheckItemCatalogChange,
  type CheckItemCatalogImplementationDraftInput,
  type RuleAssetCheckItemProductionUnit,
} from '../../../services/api'
import { queryErrorLabel } from './parserUi'

type CommandImplementation = RuleAssetCheckItemProductionUnit['implementations'][number]

interface CommandCatalogBindingDrawerProps {
  unit?: CommandImplementation
  open: boolean
  onClose: () => void
  onCreated: (change: CheckItemCatalogChange) => void
}

interface BindingFormValues {
  check_item_ids: string[]
  implementation_name: string
  vendors: string[]
  families: string[]
  platforms: string[]
  model_patterns: string[]
  os_version_patterns: string[]
  parser_requirement: CheckItemCatalogImplementationDraftInput['parser_requirement']
  template_scope?: string | null
  selection_priority: number
  reason?: string
}

const parserRequirementLabels: Record<
  CheckItemCatalogImplementationDraftInput['parser_requirement'],
  string
> = {
  structured: '结构化解析',
  normalized_diff: '规范化文本比较',
  hash_only: '原文哈希比较',
  evidence_only: '仅保留证据',
}

const compact = (values: Array<string | null | undefined>) => Array.from(new Set(
  values.map((item) => String(item || '').trim()).filter((item) => (
    item && !['unknown', 'auto-discovered'].includes(item.toLowerCase())
  )),
))

const escapePattern = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const slugPart = (value: string, limit: number) => {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return (normalized || 'asset').slice(0, limit).replace(/-+$/g, '')
}

const implementationIdFor = (unit: CommandImplementation, checkItemIds: string[]) => [
  'impl',
  slugPart(unit.vendor, 16),
  slugPart(unit.profile, 24),
  slugPart(unit.canonical_command, 32),
  slugPart([...checkItemIds].sort().join('-'), 36),
  slugPart(unit.asset_key, 12),
].join('-').slice(0, 128).replace(/-+$/g, '')

const CommandCatalogBindingDrawer: React.FC<CommandCatalogBindingDrawerProps> = ({
  unit,
  open,
  onClose,
  onCreated,
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const [form] = Form.useForm<BindingFormValues>()
  const [catalogSearch, setCatalogSearch] = useState('')
  const deferredCatalogSearch = useDeferredValue(catalogSearch)

  const catalogQuery = useQuery(
    ['command-catalog-binding-options', deferredCatalogSearch],
    () => parserApi.checkItems({
      limit: 100,
      offset: 0,
      search: deferredCatalogSearch || undefined,
    }),
    {
      enabled: open,
      keepPreviousData: true,
      staleTime: 30_000,
    },
  )

  useEffect(() => {
    if (!open || !unit) return
    form.setFieldsValue({
      check_item_ids: [],
      implementation_name: `${unit.vendor} ${unit.profile} · ${unit.canonical_command}`,
      vendors: compact([unit.vendor]),
      families: compact([unit.family]),
      platforms: compact([unit.profile]),
      model_patterns: compact(unit.models).map((model) => `^${escapePattern(model)}$`),
      os_version_patterns: [],
      parser_requirement: 'structured',
      template_scope: 'model',
      selection_priority: 100,
      reason: '',
    })
    setCatalogSearch('')
  }, [form, open, unit])

  const createMutation = useMutation(
    ({
      payload,
      idempotencyKey,
    }: {
      payload: CheckItemCatalogImplementationDraftInput
      idempotencyKey: string
    }) => parserApi.createCollectionImplementationDraft(payload, idempotencyKey),
    {
      onSuccess: (change) => {
        message.success('命令归类草稿已创建，请完成影响预览与发布审批')
        onCreated(change)
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const createBindingDraft = async () => {
    if (!unit) return
    try {
      const values = await form.validateFields()
      const match: Record<string, unknown> = {}
      const vendors = compact(values.vendors)
      const families = compact(values.families)
      const platforms = compact(values.platforms)
      const modelPatterns = compact(values.model_patterns)
      const osVersionPatterns = compact(values.os_version_patterns)
      if (vendors.length) match.vendors = vendors
      if (families.length) match.families = families
      if (platforms.length) match.platforms = platforms
      if (modelPatterns.length) match.model_patterns = modelPatterns
      if (osVersionPatterns.length) match.os_version_patterns = osVersionPatterns
      if (!Object.keys(match).length) {
        message.error('至少保留一种设备兼容条件')
        return
      }
      const idempotencyKey = `command-catalog-binding-${
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(16).slice(2)}`
      }`
      createMutation.mutate({
        idempotencyKey,
        payload: {
          id: implementationIdFor(unit, values.check_item_ids),
          name: values.implementation_name.trim(),
          description: `由规则资产生产单元 ${unit.id} 基于样本任务 ${unit.source_job.id} 创建。`,
          check_item_ids: values.check_item_ids,
          match,
          commands: [{ command: unit.canonical_command, priority: 100 }],
          collector: 'cli',
          parser_requirement: values.parser_requirement,
          template_scope: values.template_scope || null,
          selection_priority: Number(values.selection_priority),
          reason: values.reason?.trim() || '',
        },
      })
    } catch {
      // Ant Design renders field-level validation feedback.
    }
  }

  const options = (catalogQuery.data?.items || []).map((item) => ({
    value: item.check_item_id,
    label: `${item.display_name || item.name} · ${item.check_item_id}`,
  }))

  const selectCheckItems = (checkItemIds: string[]) => {
    const selected = (catalogQuery.data?.items || []).filter(
      (item) => checkItemIds.includes(item.check_item_id),
    )
    if (!selected.length) return
    const resultModes = Array.from(new Set(selected.map((item) => item.result_mode)))
    if (resultModes.length > 1) {
      message.warning('一次采集实现只能关联结果处理方式相同的检查项')
      return
    }
    form.setFieldValue(
      'parser_requirement',
      resultModes[0] as CheckItemCatalogImplementationDraftInput['parser_requirement'],
    )
  }

  return (
    <Drawer
      open={open}
      width={isMobile ? '100%' : 720}
      title="将命令绑定到检查项目录"
      onClose={onClose}
      extra={(
        <Button
          icon={<LinkOutlined />}
          loading={createMutation.isLoading}
          onClick={() => void createBindingDraft()}
        >
          创建绑定草稿
        </Button>
      )}
    >
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="绑定会进入目录审批，不会直接修改生产运行包"
          description="平台将创建一个版本化采集实现。影响预览和审批通过后，目录解析器会自动重新归类该命令。"
        />
        {unit && (
          <Descriptions size="small" bordered column={isMobile ? 1 : 2}>
            <Descriptions.Item label="规范命令" span={isMobile ? 1 : 2}>
              <Typography.Text code>{unit.canonical_command}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="厂商 / Profile">
              {unit.vendor} / {unit.profile}
            </Descriptions.Item>
            <Descriptions.Item label="证据规模">
              {unit.sample_count} 份输出 / {unit.device_count} 台设备
            </Descriptions.Item>
            <Descriptions.Item label="设备型号" span={isMobile ? 1 : 2}>
              {unit.models.join('、') || '-'}
            </Descriptions.Item>
          </Descriptions>
        )}

        <Form form={form} layout="vertical" requiredMark="optional">
          <Form.Item
            label="已审批检查项"
            name="check_item_ids"
            rules={[{ required: true, type: 'array', min: 1, message: '请选择检查项' }]}
          >
            <Select
              mode="multiple"
              showSearch
              filterOption={false}
              loading={catalogQuery.isFetching}
              placeholder="按名称或检查项 ID 搜索"
              options={options}
              onSearch={setCatalogSearch}
              onChange={selectCheckItems}
              notFoundContent={catalogQuery.isFetching ? '正在查询...' : '没有匹配的已发布检查项'}
            />
          </Form.Item>
          <Form.Item
            label="采集实现名称"
            name="implementation_name"
            rules={[{ required: true, message: '请输入采集实现名称' }]}
          >
            <Input />
          </Form.Item>

          <Typography.Title level={5}>设备兼容范围</Typography.Title>
          <div className="check-item-catalog-form-grid">
            <Form.Item label="厂商" name="vendors">
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>
            <Form.Item label="设备族" name="families">
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>
            <Form.Item label="平台" name="platforms">
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>
            <Form.Item label="型号正则" name="model_patterns">
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>
            <Form.Item className="check-item-catalog-form-wide" label="系统版本正则" name="os_version_patterns">
              <Select mode="tags" tokenSeparators={[',']} placeholder="可选，例如 ^10\\." />
            </Form.Item>
          </div>

          <Typography.Title level={5}>解析与选择</Typography.Title>
          <div className="check-item-catalog-form-grid">
            <Form.Item label="结果处理方式" name="parser_requirement" rules={[{ required: true }]}>
              <Select
                disabled
                options={Object.entries(parserRequirementLabels).map(([value, label]) => ({ value, label }))}
              />
            </Form.Item>
            <Form.Item label="模板兼容粒度" name="template_scope">
              <Select options={[
                { value: 'vendor', label: '厂商' },
                { value: 'family', label: '设备族' },
                { value: 'model', label: '型号' },
                { value: 'output_signature', label: '输出签名' },
              ]} />
            </Form.Item>
            <Form.Item label="实现选择优先级" name="selection_priority" rules={[{ required: true }]}>
              <InputNumber min={0} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              className="check-item-catalog-form-wide"
              label="归类备注"
              name="reason"
            >
              <Input.TextArea
                rows={3}
                maxLength={2000}
                placeholder="可补充该命令与检查项的关系或兼容范围依据"
              />
            </Form.Item>
          </div>
        </Form>
      </Space>
    </Drawer>
  )
}

export default CommandCatalogBindingDrawer
