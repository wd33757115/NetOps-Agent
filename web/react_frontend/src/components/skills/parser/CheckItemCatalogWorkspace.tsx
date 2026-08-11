import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Grid,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  MinusCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useAuth } from '../../../context/AuthContext'
import {
  parserApi,
  type CheckItemCatalogChange,
  type CheckItemCatalogCheckItemCandidate,
  type CheckItemCatalogCheckItemDraftInput,
  type CheckItemCatalogImplementationCandidate,
  type CheckItemCatalogImpact,
  type CheckItemCatalogImplementationDraftInput,
  type ParserCheckItem,
} from '../../../services/api'
import { queryErrorLabel, shortDigest } from './parserUi'
import CheckItemEditorDrawer from './CheckItemEditorDrawer'

type CatalogView = 'catalog' | 'changes'

interface CheckItemCatalogWorkspaceProps {
  focusChangeId?: string
  mode?: 'check_items' | 'command_adapters'
}

interface ImplementationDraftForm {
  id: string
  name: string
  description?: string
  check_item_ids: string[]
  vendors: string[]
  families: string[]
  platforms: string[]
  model_patterns: string[]
  os_version_patterns: string[]
  command: string
  parser_requirement: CheckItemCatalogImplementationDraftInput['parser_requirement']
  template_scope?: string
  selection_priority: number
  output_quality_policy_json?: string
  configure_adapter: boolean
  parser_template_id?: string
  adapter_outputs: Array<{
    check_item_id: string
    source_record_set: string
    field_mappings: Array<{
      target_field: string
      mode: 'source' | 'expression' | 'literal'
      value: string
    }>
    empty_status: 'valid_empty' | 'unavailable' | 'not_applicable'
  }>
  reason?: string
}

const fieldMappingRows = (value: unknown): ImplementationDraftForm['adapter_outputs'][number]['field_mappings'] => {
  if (!value || Array.isArray(value) || typeof value !== 'object') return []
  return Object.entries(value as Record<string, unknown>).map(([targetField, binding]) => {
    if (typeof binding === 'string') {
      return { target_field: targetField, mode: 'source' as const, value: binding }
    }
    const item = binding && !Array.isArray(binding) && typeof binding === 'object'
      ? binding as Record<string, unknown>
      : {}
    if ('expression' in item) {
      return { target_field: targetField, mode: 'expression' as const, value: String(item.expression || '') }
    }
    if ('literal' in item) {
      return { target_field: targetField, mode: 'literal' as const, value: String(item.literal ?? '') }
    }
    return { target_field: targetField, mode: 'source' as const, value: String(item.source || '') }
  })
}

const canonicalFields = (checkItem?: ParserCheckItem): string[] => {
  const contract = checkItem?.canonical_contract || {}
  const identities = Array.isArray(contract.identity_dimensions)
    ? contract.identity_dimensions.map(String).filter((item) => item && item !== 'device_id')
    : []
  const observations = Array.isArray(contract.observations)
    ? (contract.observations as Array<Record<string, unknown>>)
      .map((item) => String(item.canonical_field || '').trim())
      .filter(Boolean)
    : []
  return Array.from(new Set([...identities, ...observations]))
}

const statusLabels: Record<CheckItemCatalogChange['status'], string> = {
  draft: '草稿待提交',
  pending_approval: '待发布审批',
  approved: '已发布',
  rejected: '已拒绝',
  cancelled: '已取消',
}

const statusColors: Record<CheckItemCatalogChange['status'], string> = {
  draft: 'default',
  pending_approval: 'gold',
  approved: 'green',
  rejected: 'red',
  cancelled: 'default',
}

const nextActionLabels: Record<string, string> = {
  narrow_compatibility_selector: '收窄厂商、设备族或型号范围，并调整优先级以消除匹配歧义',
  prepare_dual_implementation_rollout: '先补齐旧、新采集实现双路由的渐进发布能力；当前修订可保存和审批，但禁止替换生产版本',
  import_inventory_or_review_manually: '补充设备资产数据，或由工程师逐台复核预期影响',
  confirm_future_compatibility_scope: '确认该兼容范围用于后续新增设备，当前没有可验证的现网设备',
  prepare_runtime_bundle: '补齐 Parser、字段映射和检查项判断，并生成可发布运行版本',
  create_collection_implementation: '为检查项新增厂商、设备族、型号和命令适配器',
  add_check_item_to_patrol_profile: '把已发布检查项加入需要执行它的巡检 Profile',
  retire_or_rebuild_runtime_bundles: '先停用旧运行包，或按新检查项版本重新组装并发布运行包',
}

const nextActionLabel = (value: string) => nextActionLabels[value] || value

const parserRequirementLabels: Record<string, string> = {
  structured: '结构化解析',
  normalized_diff: '规范化文本比较',
  hash_only: '原文哈希比较',
  evidence_only: '证据留存',
}

const isCheckItemCandidate = (
  candidate: CheckItemCatalogChange['candidate'],
): candidate is CheckItemCatalogCheckItemCandidate => 'check_item_id' in candidate

const uniqueStrings = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map(String).map((item) => item.trim()).filter(Boolean)))
  }
  const single = String(value || '').trim()
  return single ? [single] : []
}

const makeIdempotencyKey = (scope: string) =>
  `${scope}-${typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`

const implementationFormValues = (
  implementation: ParserCheckItem['implementations'][number],
  checkItemId: string,
): ImplementationDraftForm => {
  const match = implementation.match || {}
  const primaryCommand = [...implementation.commands]
    .sort((left, right) => right.priority - left.priority)[0]
  const adapterSpec = implementation.adapter_spec || {}
  const parser = adapterSpec.parser && typeof adapterSpec.parser === 'object'
    ? adapterSpec.parser as Record<string, unknown>
    : {}
  const legacyTemplates = Array.isArray(parser.templates)
    ? parser.templates as Array<Record<string, unknown>>
    : []
  const parserTemplateId = String(
    parser.template_id
    || legacyTemplates.find((item) => item.template_id)?.template_id
    || '',
  )
  const outputs = Array.isArray(adapterSpec.outputs)
    ? adapterSpec.outputs as Array<Record<string, unknown>>
    : []
  return {
    id: implementation.implementation_id,
    name: implementation.name,
    description: implementation.description,
    check_item_ids: [checkItemId],
    vendors: uniqueStrings(match.vendors || match.vendor),
    families: uniqueStrings(match.families || match.family),
    platforms: uniqueStrings(match.platforms || match.platform),
    model_patterns: uniqueStrings(match.model_patterns),
    os_version_patterns: uniqueStrings(match.os_version_patterns),
    command: primaryCommand?.command || '',
    parser_requirement: implementation.parser_requirement as ImplementationDraftForm['parser_requirement'],
    template_scope: implementation.template_scope || 'family',
    selection_priority: implementation.selection_priority,
    output_quality_policy_json: Object.keys(implementation.output_quality_policy || {}).length
      ? JSON.stringify(implementation.output_quality_policy, null, 2)
      : '',
    configure_adapter: outputs.length > 0,
    parser_template_id: parserTemplateId,
    adapter_outputs: outputs.map((item) => ({
      check_item_id: String(item.check_item_id || item.check_item || ''),
      source_record_set: String(item.source_record_set || ''),
      field_mappings: fieldMappingRows(item.fields),
      empty_status: String(item.empty_status || 'unavailable') as ImplementationDraftForm['adapter_outputs'][number]['empty_status'],
    })),
    reason: '',
  }
}

const decisionLabel = (value: unknown) => {
  const status = String(value || '')
  return ({
    resolved: '已匹配',
    unsupported: '不支持',
    ambiguous: '歧义',
  } as Record<string, string>)[status] || status || '-'
}

const CheckItemCatalogWorkspace: React.FC<CheckItemCatalogWorkspaceProps> = ({
  focusChangeId,
  mode = 'check_items',
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [view, setView] = useState<CatalogView>('catalog')
  const [catalogPage, setCatalogPage] = useState(1)
  const [catalogPageSize, setCatalogPageSize] = useState(20)
  const [catalogSearchDraft, setCatalogSearchDraft] = useState('')
  const [catalogSearch, setCatalogSearch] = useState('')
  const [changePage, setChangePage] = useState(1)
  const [changePageSize, setChangePageSize] = useState(20)
  const [changeSearchDraft, setChangeSearchDraft] = useState('')
  const [changeSearch, setChangeSearch] = useState('')
  const [changeStatus, setChangeStatus] = useState<string>()
  const [draftOpen, setDraftOpen] = useState(false)
  const [editingImplementationId, setEditingImplementationId] = useState<string>()
  const [draftIdempotencyKey, setDraftIdempotencyKey] = useState('')
  const [selectedChangeId, setSelectedChangeId] = useState<string>()
  const [detailOpen, setDetailOpen] = useState(false)
  const [lifecycleReason, setLifecycleReason] = useState('')
  const [selectedRolloutCampaignId, setSelectedRolloutCampaignId] = useState<string>()
  const [deletingImplementation, setDeletingImplementation] = useState<
    ParserCheckItem['implementations'][number]
  >()
  const [deleteReason, setDeleteReason] = useState('')
  const [checkItemEditorOpen, setCheckItemEditorOpen] = useState(false)
  const [editingCheckItem, setEditingCheckItem] = useState<ParserCheckItem>()
  const [checkItemIdempotencyKey, setCheckItemIdempotencyKey] = useState('')
  const [deletingCheckItem, setDeletingCheckItem] = useState<ParserCheckItem>()
  const [checkItemDeleteReason, setCheckItemDeleteReason] = useState('')
  const [form] = Form.useForm<ImplementationDraftForm>()
  const configureAdapter = Form.useWatch('configure_adapter', form)
  const parserRequirement = Form.useWatch('parser_requirement', form)
  const handledFocusChangeId = useRef('')

  useEffect(() => {
    const changeId = String(focusChangeId || '').trim()
    if (!changeId || handledFocusChangeId.current === changeId) return
    handledFocusChangeId.current = changeId
    setView('changes')
    setSelectedChangeId(changeId)
    setDetailOpen(true)
  }, [focusChangeId])

  const catalogQuery = useQuery(
    ['check-item-catalog', catalogPage, catalogPageSize, catalogSearch],
    () => parserApi.checkItems({
      limit: catalogPageSize,
      offset: (catalogPage - 1) * catalogPageSize,
      search: catalogSearch || undefined,
    }),
    { keepPreviousData: true },
  )
  const allCheckItemsQuery = useQuery(
    ['check-item-catalog-options'],
    () => parserApi.checkItems({ limit: 200, offset: 0 }),
    { staleTime: 60_000 },
  )
  const changesQuery = useQuery(
    ['check-item-catalog-changes', changePage, changePageSize, changeSearch, changeStatus],
    () => parserApi.catalogChanges({
      limit: changePageSize,
      offset: (changePage - 1) * changePageSize,
      search: changeSearch || undefined,
      status: changeStatus,
    }),
    { keepPreviousData: true },
  )
  const canonicalObservationsQuery = useQuery(
    ['canonical-observation-catalog'],
    () => parserApi.canonicalObservations(),
    { staleTime: 5 * 60_000 },
  )
  const activeTemplatesQuery = useQuery(
    ['active-parser-template-options'],
    () => parserApi.templates({ status: 'active', limit: 200, offset: 0 }),
    { staleTime: 60_000 },
  )
  const changeDetailQuery = useQuery(
    ['check-item-catalog-change', selectedChangeId],
    () => parserApi.getCatalogChange(selectedChangeId || ''),
    { enabled: Boolean(selectedChangeId) },
  )
  const impactQuery = useQuery(
    ['check-item-catalog-impact', selectedChangeId],
    () => parserApi.previewCatalogChange(selectedChangeId || ''),
    { enabled: Boolean(selectedChangeId && detailOpen) },
  )
  const publicationOptionsQuery = useQuery(
    ['check-item-catalog-publication-options', selectedChangeId],
    () => parserApi.catalogPublicationOptions(selectedChangeId || ''),
    { enabled: Boolean(selectedChangeId && detailOpen) },
  )

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries('check-item-catalog'),
      queryClient.invalidateQueries('check-item-catalog-options'),
      queryClient.invalidateQueries('check-item-catalog-changes'),
      queryClient.invalidateQueries('check-item-catalog-change'),
      queryClient.invalidateQueries('check-item-catalog-impact'),
      queryClient.invalidateQueries('check-item-catalog-publication-options'),
      queryClient.invalidateQueries('rule-asset-production-units'),
      queryClient.invalidateQueries('product-check-item-runtime-bundles'),
    ])
  }

  const createMutation = useMutation(
    (data: CheckItemCatalogImplementationDraftInput) =>
      parserApi.createCollectionImplementationDraft(data, draftIdempotencyKey),
    {
      onSuccess: async (change) => {
        message.success('命令适配器草稿已创建')
        setDraftOpen(false)
        setSelectedChangeId(change.id)
        setDetailOpen(true)
        setLifecycleReason('')
        form.resetFields()
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const createCheckItemMutation = useMutation(
    (data: CheckItemCatalogCheckItemDraftInput) =>
      parserApi.createCheckItemDraft(data, checkItemIdempotencyKey),
    {
      onSuccess: async (change) => {
        message.success('检查项草稿已创建')
        setCheckItemEditorOpen(false)
        setEditingCheckItem(undefined)
        setSelectedChangeId(change.id)
        setDetailOpen(true)
        setLifecycleReason('')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const submitMutation = useMutation(
    ({ changeId, impactDigest }: { changeId: string; impactDigest: string }) =>
      parserApi.submitCatalogChange(
        changeId,
        { reason: lifecycleReason.trim(), expected_impact_digest: impactDigest },
        makeIdempotencyKey('catalog-submit'),
      ),
    {
      onSuccess: async () => {
        message.success('已提交独立发布审批')
        setLifecycleReason('')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const decisionMutation = useMutation(
    ({
      changeId,
      impactDigest,
      decision,
      rolloutCampaignId,
    }: {
      changeId: string
      impactDigest: string
      decision: 'approve' | 'reject'
      rolloutCampaignId?: string
    }) => parserApi.decideCatalogChange(
      changeId,
      decision,
      {
        reason: lifecycleReason.trim(),
        expected_impact_digest: impactDigest,
        rollout_campaign_id: decision === 'approve' ? rolloutCampaignId : undefined,
      },
      makeIdempotencyKey(`catalog-${decision}`),
    ),
    {
      onSuccess: async (_data, variables) => {
        message.success(variables.decision === 'approve' ? '目录资产已发布' : '候选已拒绝')
        setLifecycleReason('')
        setSelectedRolloutCampaignId(undefined)
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const deleteMutation = useMutation(
    ({ implementationId, reason }: { implementationId: string; reason?: string }) =>
      parserApi.deleteCollectionImplementation(
        implementationId,
        reason,
        makeIdempotencyKey('catalog-delete'),
      ),
    {
      onSuccess: async () => {
        message.success('命令适配器已删除，历史版本与审计记录已保留')
        setDeletingImplementation(undefined)
        setDeleteReason('')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const deleteCheckItemMutation = useMutation(
    ({ checkItemId, reason }: { checkItemId: string; reason?: string }) =>
      parserApi.deleteCheckItem(
        checkItemId,
        reason,
        makeIdempotencyKey('catalog-check-item-delete'),
      ),
    {
      onSuccess: async () => {
        message.success('检查项已删除，历史版本与审计记录已保留')
        setDeletingCheckItem(undefined)
        setCheckItemDeleteReason('')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const openNewDraft = (checkItemId?: string) => {
    setEditingImplementationId(undefined)
    setDraftIdempotencyKey(makeIdempotencyKey('catalog-create'))
    form.setFieldsValue({
      id: '',
      name: '',
      description: '',
      check_item_ids: checkItemId ? [checkItemId] : [],
      vendors: [],
      families: [],
      platforms: [],
      model_patterns: [],
      os_version_patterns: [],
      command: '',
      parser_requirement: 'structured',
      template_scope: 'family',
      selection_priority: 100,
      output_quality_policy_json: '',
      configure_adapter: false,
      parser_template_id: '',
      adapter_outputs: [],
      reason: '',
    })
    setDraftOpen(true)
  }

  const openNewCheckItem = () => {
    setEditingCheckItem(undefined)
    setCheckItemIdempotencyKey(makeIdempotencyKey('catalog-check-item-create'))
    setCheckItemEditorOpen(true)
  }

  const canonicalFieldOptions = useMemo(() => Array.from(new Set(
    (allCheckItemsQuery.data?.items || []).flatMap(canonicalFields),
  )).sort().map((value) => ({ value, label: value })), [allCheckItemsQuery.data?.items])

  const prefillOutputMappings = (outputIndex: number, checkItemId?: string) => {
    const outputs = form.getFieldValue('adapter_outputs') || []
    const selectedId = checkItemId || outputs[outputIndex]?.check_item_id
    const checkItem = (allCheckItemsQuery.data?.items || []).find(
      (item) => item.check_item_id === selectedId,
    )
    const fields = canonicalFields(checkItem)
    if (!fields.length) {
      message.warning('该检查项尚未定义标准字段')
      return
    }
    const existing = new Map(
      (outputs[outputIndex]?.field_mappings || []).map((item) => [item.target_field, item]),
    )
    outputs[outputIndex] = {
      ...outputs[outputIndex],
      ...(checkItemId ? { check_item_id: checkItemId } : {}),
      field_mappings: fields.map((field) => existing.get(field) || {
        target_field: field,
        mode: 'source',
        value: field,
      }),
    }
    form.setFieldValue('adapter_outputs', outputs)
  }

  const openCheckItemRevision = (checkItem: ParserCheckItem) => {
    setEditingCheckItem(checkItem)
    setCheckItemIdempotencyKey(makeIdempotencyKey('catalog-check-item-revise'))
    setCheckItemEditorOpen(true)
  }

  const openRevision = (
    implementation: ParserCheckItem['implementations'][number],
    checkItemId: string,
  ) => {
    setEditingImplementationId(implementation.implementation_id)
    setDraftIdempotencyKey(makeIdempotencyKey('catalog-revise'))
    const allAssociations = (allCheckItemsQuery.data?.items || [])
      .filter((item) => item.implementations.some(
        (candidate) => candidate.implementation_id === implementation.implementation_id,
      ))
      .map((item) => item.check_item_id)
    form.setFieldsValue({
      ...implementationFormValues(implementation, checkItemId),
      check_item_ids: allAssociations.length ? allAssociations : [checkItemId],
      reason: '',
    })
    setDraftOpen(true)
  }

  const createDraft = async () => {
    try {
      const values = await form.validateFields()
      const match: Record<string, unknown> = {}
      if (values.vendors?.length) match.vendors = uniqueStrings(values.vendors)
      if (values.families?.length) match.families = uniqueStrings(values.families)
      if (values.platforms?.length) match.platforms = uniqueStrings(values.platforms)
      if (values.model_patterns?.length) match.model_patterns = uniqueStrings(values.model_patterns)
      if (values.os_version_patterns?.length) {
        match.os_version_patterns = uniqueStrings(values.os_version_patterns)
      }
      if (!Object.keys(match).length) {
        message.error('至少配置厂商、设备族、平台、型号模式或系统版本模式之一')
        return
      }
      let outputQualityPolicy: Record<string, unknown> | undefined
      if (values.output_quality_policy_json?.trim()) {
        try {
          outputQualityPolicy = JSON.parse(values.output_quality_policy_json)
        } catch {
          message.error('输出质量策略不是有效 JSON')
          return
        }
      }
      let adapterSpec: Record<string, unknown> | undefined
      if (values.configure_adapter && values.parser_requirement === 'structured') {
        const outputs: Array<Record<string, unknown>> = []
        for (const output of values.adapter_outputs || []) {
          const fields: Record<string, unknown> = {}
          for (const mapping of output.field_mappings || []) {
            const target = mapping.target_field?.trim()
            const value = mapping.value?.trim()
            if (!target || !value) {
              message.error(`检查项 ${output.check_item_id || '-'} 存在未完成的字段映射`)
              return
            }
            if (target in fields) {
              message.error(`检查项 ${output.check_item_id || '-'} 的标准字段 ${target} 被重复映射`)
              return
            }
            fields[target] = mapping.mode === 'source'
              ? value
              : { [mapping.mode]: value }
          }
          if (!Object.keys(fields).length) {
            message.error(`检查项 ${output.check_item_id || '-'} 至少需要一个字段映射`)
            return
          }
          outputs.push({
            check_item_id: output.check_item_id,
            source_record_set: output.source_record_set.trim(),
            fields,
            empty_status: output.empty_status,
          })
        }
        adapterSpec = {
          schema_version: 'command-adapter/v1',
          parser: { template_id: values.parser_template_id?.trim() },
          outputs,
        }
      }
      createMutation.mutate({
        id: values.id.trim(),
        name: values.name.trim(),
        description: values.description?.trim(),
        check_item_ids: values.check_item_ids,
        match,
        commands: [{ command: values.command.trim(), priority: 100 }],
        collector: 'cli',
        parser_requirement: values.parser_requirement,
        template_scope: values.template_scope || null,
        adapter_spec: adapterSpec,
        output_quality_policy: outputQualityPolicy,
        selection_priority: Number(values.selection_priority),
        reason: values.reason?.trim() || '',
      })
    } catch {
      // Ant Design keeps field-level validation messages in the form.
    }
  }

  const selectedChange = changeDetailQuery.data
  const impact = impactQuery.data
  const publicationOptions = publicationOptionsQuery.data
  const selectedCheckItemCandidate = selectedChange && isCheckItemCandidate(selectedChange.candidate)
    ? selectedChange.candidate
    : undefined
  const selectedImplementationCandidate = selectedChange && !isCheckItemCandidate(selectedChange.candidate)
    ? selectedChange.candidate as CheckItemCatalogImplementationCandidate
    : undefined
  const impactDrifted = Boolean(
    selectedChange?.impact_digest
    && impact?.impact_digest
    && selectedChange.impact_digest !== impact.impact_digest,
  )
  const selectedRolloutIsEligible = Boolean(
    selectedRolloutCampaignId
    && publicationOptions?.eligible_campaigns.some(
      (item) => item.campaign_id === selectedRolloutCampaignId,
    ),
  )
  const unresolvedBlockers = (impact?.blockers || []).filter(
    (item) => !(
      item.code === 'active_runtime_bundle_requires_rollout'
      && selectedRolloutIsEligible
    ),
  )
  const canApprove = Boolean(
    user?.role === 'admin'
    && selectedChange?.status === 'pending_approval'
    && impact
    && !unresolvedBlockers.length
    && !impactDrifted,
  )
  const catalogColumns: ColumnsType<ParserCheckItem> = [
    {
      title: '检查项',
      key: 'check_item',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.display_name || row.name}</Typography.Text>
          <Typography.Text type="secondary" copyable>{row.check_item_id}</Typography.Text>
        </Space>
      ),
    },
    { title: '类别', dataIndex: 'category', width: 120 },
    {
      title: '结果模式',
      dataIndex: 'result_mode',
      width: 150,
      render: (value: string) => parserRequirementLabels[value] || value,
    },
    {
      title: '命令适配器',
      dataIndex: 'implementation_count',
      width: 110,
      render: (value: number) => `${value} 个`,
    },
    {
      title: '版本',
      key: 'version',
      width: 100,
      render: (_, row) => `v${row.version}`,
    },
    {
      title: '操作',
      key: 'action',
      width: 128,
      render: (_, row) => (
        <Space size={0}>
          <Button
            type="text"
            icon={<PlusOutlined />}
            title="新增命令适配器"
            aria-label={`为 ${row.check_item_id} 新增命令适配器`}
            onClick={() => openNewDraft(row.check_item_id)}
          />
          <Button
            type="text"
            icon={<EditOutlined />}
            title="编辑检查项"
            aria-label={`编辑检查项 ${row.check_item_id}`}
            onClick={() => openCheckItemRevision(row)}
          />
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            title="删除检查项"
            aria-label={`删除检查项 ${row.check_item_id}`}
            disabled={user?.role !== 'admin'}
            onClick={() => {
              setDeletingCheckItem(row)
              setCheckItemDeleteReason('')
            }}
          />
        </Space>
      ),
    },
  ]
  const implementationColumns = (
    checkItemId: string,
  ): ColumnsType<ParserCheckItem['implementations'][number]> => [
    {
      title: '兼容范围',
      key: 'scope',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.name}</Typography.Text>
          <Typography.Text type="secondary">
            {[...(uniqueStrings(row.match.vendors || row.match.vendor)),
              ...uniqueStrings(row.match.families),
              ...uniqueStrings(row.match.platforms),
              ...uniqueStrings(row.match.model_patterns),
              ...uniqueStrings(row.match.os_version_patterns)].join(' / ') || '未配置'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '采集命令',
      dataIndex: 'commands',
      render: (commands: Array<{ command: string; priority: number }>) => (
        <Typography.Text code>{commands[0]?.command || '-'}</Typography.Text>
      ),
    },
    {
      title: '解析方式',
      dataIndex: 'parser_requirement',
      width: 150,
      render: (value: string) => parserRequirementLabels[value] || value,
    },
    {
      title: '操作',
      key: 'action',
      width: 112,
      render: (_, row) => (
        <Space size={0}>
          <Button
            type="text"
            icon={<EditOutlined />}
            title="编辑命令适配器"
            aria-label="编辑命令适配器"
            onClick={() => openRevision(row, checkItemId)}
          />
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            title="删除命令适配器"
            aria-label="删除命令适配器"
            disabled={user?.role !== 'admin'}
            onClick={() => {
              setDeletingImplementation(row)
              setDeleteReason('')
            }}
          />
        </Space>
      ),
    },
  ]
  const changeColumns: ColumnsType<CheckItemCatalogChange> = [
    {
      title: '目录资产',
      key: 'asset',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.candidate.name}</Typography.Text>
          <Typography.Text type="secondary">
            {row.asset_kind === 'check_item' ? '检查项' : '命令适配器'} · {row.asset_id} · v{row.version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '检查项',
      key: 'check_items',
      render: (_, row) => {
        const values = isCheckItemCandidate(row.candidate)
          ? [row.candidate.check_item_id]
          : row.candidate.check_item_ids
        return <Space wrap size={[4, 4]}>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space>
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (value: CheckItemCatalogChange['status']) => (
        <Tag color={statusColors[value]}>{statusLabels[value]}</Tag>
      ),
    },
    {
      title: '发起人',
      dataIndex: 'created_by',
      width: 130,
    },
    {
      title: '操作',
      key: 'action',
      width: 86,
      render: (_, row) => (
        <Button
          type="text"
          icon={<EyeOutlined />}
          title="查看影响与审批"
          onClick={() => {
            setSelectedChangeId(row.id)
            setLifecycleReason('')
            setDetailOpen(true)
          }}
        />
      ),
    },
  ]

  return (
    <div className="check-item-catalog-workspace">
      <div className="check-item-catalog-toolbar">
        <div>
          <Typography.Title level={2}>
            {mode === 'command_adapters' ? '命令适配器' : '检查项目录'}
          </Typography.Title>
          <Typography.Text type="secondary">
            {mode === 'command_adapters'
              ? '按检查项分组维护设备范围、单条命令、Parser 记录集与标准字段映射。'
              : '定义跨厂商一致的标准字段、主键、变化与风险判断。'}
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} title="刷新" onClick={() => void refreshAll()} />
          {mode === 'command_adapters' && (
            <Button
              type="primary"
              className="rule-asset-import-button"
              icon={<PlusOutlined />}
              onClick={() => openNewDraft()}
            >
              新增命令适配器
            </Button>
          )}
          {mode === 'check_items' && (
            <Button
              type="primary"
              className="rule-asset-import-button"
              icon={<PlusOutlined />}
              onClick={openNewCheckItem}
            >
              新增检查项
            </Button>
          )}
        </Space>
      </div>

      <div
        className="rule-asset-view-switch"
        role="tablist"
        aria-label={mode === 'command_adapters' ? '命令适配器视图' : '检查项目录视图'}
      >
        <button
          type="button"
          role="tab"
          className={view === 'catalog' ? 'is-active' : ''}
          aria-selected={view === 'catalog'}
          onClick={() => setView('catalog')}
        >
          {mode === 'command_adapters' ? '已发布适配器' : '已发布检查项'}
        </button>
        <button
          type="button"
          role="tab"
          className={view === 'changes' ? 'is-active' : ''}
          aria-selected={view === 'changes'}
          onClick={() => setView('changes')}
        >
          变更与审批
        </button>
      </div>

      {view === 'catalog' ? (
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 14 }}>
          <Input.Search
            allowClear
            placeholder="搜索检查项、厂商、型号或命令"
            value={catalogSearchDraft}
            onChange={(event) => setCatalogSearchDraft(event.target.value)}
            onSearch={(value) => {
              setCatalogPage(1)
              setCatalogSearch(value.trim())
            }}
          />
          {catalogQuery.isError && (
            <Alert type="error" showIcon message="目录加载失败" description={queryErrorLabel(catalogQuery.error)} />
          )}
          <Table<ParserCheckItem>
            rowKey="check_item_version_id"
            loading={catalogQuery.isLoading || catalogQuery.isFetching}
            dataSource={catalogQuery.data?.items || []}
            columns={catalogColumns}
            expandable={{
              columnTitle: <span className="visually-hidden">展开命令适配器</span>,
              expandedRowRender: (row) => row.implementations.length ? (
                <Table
                  rowKey="implementation_version_id"
                  size="small"
                  dataSource={row.implementations}
                  columns={implementationColumns(row.check_item_id)}
                  pagination={false}
                  scroll={{ x: 760 }}
                />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未配置命令适配器" />,
            }}
            scroll={{ x: 760 }}
            pagination={{
              current: catalogPage,
              pageSize: catalogPageSize,
              total: catalogQuery.data?.total || 0,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 个检查项`,
              onChange: (page, pageSize) => {
                setCatalogPage(pageSize === catalogPageSize ? page : 1)
                setCatalogPageSize(pageSize)
              },
            }}
          />
        </Space>
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 14 }}>
          <div className="check-item-catalog-filters">
            <Input.Search
              allowClear
              placeholder="搜索采集实现或变更号"
              value={changeSearchDraft}
              onChange={(event) => setChangeSearchDraft(event.target.value)}
              onSearch={(value) => {
                setChangePage(1)
                setChangeSearch(value.trim())
              }}
            />
            <Select
              allowClear
              placeholder="治理状态"
              value={changeStatus}
              onChange={(value) => {
                setChangePage(1)
                setChangeStatus(value)
              }}
              options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
            />
          </div>
          {changesQuery.isError && (
            <Alert type="error" showIcon message="变更记录加载失败" description={queryErrorLabel(changesQuery.error)} />
          )}
          <Table<CheckItemCatalogChange>
            rowKey="id"
            loading={changesQuery.isLoading || changesQuery.isFetching}
            dataSource={changesQuery.data?.items || []}
            columns={changeColumns}
            scroll={{ x: 820 }}
            pagination={false}
            locale={{ emptyText: '尚无变更记录' }}
          />
          <Pagination
            current={changePage}
            pageSize={changePageSize}
            total={changesQuery.data?.total || 0}
            showSizeChanger
            showTotal={(total) => `共 ${total} 条变更`}
            onChange={(page, pageSize) => {
              setChangePage(pageSize === changePageSize ? page : 1)
              setChangePageSize(pageSize)
            }}
          />
        </Space>
      )}

      <CheckItemEditorDrawer
        open={checkItemEditorOpen}
        mobile={isMobile}
        checkItem={editingCheckItem}
        canonicalCatalog={canonicalObservationsQuery.data}
        canonicalLoading={canonicalObservationsQuery.isLoading}
        canonicalError={
          canonicalObservationsQuery.error instanceof Error
            ? canonicalObservationsQuery.error.message
            : canonicalObservationsQuery.isError
              ? '无法读取已批准的标准观测字段，请刷新后重试。'
              : undefined
        }
        loading={createCheckItemMutation.isLoading}
        onClose={() => {
          setCheckItemEditorOpen(false)
          setEditingCheckItem(undefined)
        }}
        onSubmit={(payload) => createCheckItemMutation.mutate(payload)}
      />

      <Modal
        open={Boolean(deletingCheckItem)}
        title="删除检查项"
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: deleteCheckItemMutation.isLoading }}
        onCancel={() => {
          setDeletingCheckItem(undefined)
          setCheckItemDeleteReason('')
        }}
        onOk={() => {
          if (!deletingCheckItem) return
          deleteCheckItemMutation.mutate({
            checkItemId: deletingCheckItem.check_item_id,
            reason: checkItemDeleteReason.trim(),
          })
        }}
      >
        <Alert
          type="warning"
          showIcon
          message={`将删除 ${deletingCheckItem?.display_name || deletingCheckItem?.name || ''}`}
          description="这是可追溯的逻辑删除。存在已发布采集实现或 Active RuntimeBundle 时，系统会阻止删除；请先退出相关生产资产。"
          style={{ marginBottom: 16 }}
        />
        <Input.TextArea
          rows={3}
          value={checkItemDeleteReason}
          onChange={(event) => setCheckItemDeleteReason(event.target.value)}
          placeholder="删除备注（可选）"
        />
      </Modal>

      <Drawer
        open={draftOpen}
        width={isMobile ? '100%' : 760}
        title={editingImplementationId ? '编辑命令适配器' : '新增命令适配器'}
        onClose={() => setDraftOpen(false)}
        extra={(
          <Button
            type="primary"
            className="rule-asset-import-button"
            loading={createMutation.isLoading}
            onClick={() => void createDraft()}
          >
            保存草稿并预览影响
          </Button>
        )}
      >
        <Alert
          type="info"
          showIcon
          message="保存会创建不可变新版本"
          description={user?.role === 'admin'
            ? 'Active 版本不会被覆盖；发布前必须检查设备命中、歧义和运行包影响。管理员可审批本人提交的变更，操作会写入审计记录。'
            : 'Active 版本不会被覆盖；发布前必须检查设备命中、歧义和运行包影响，并由另一账号审批。'}
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical" requiredMark="optional">
          <div className="check-item-catalog-form-grid">
            <Form.Item
              label="稳定实现 ID"
              name="id"
              rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_.-]+$/, message: '使用小写字母、数字、点、横线或下划线' }]}
            >
              <Input disabled={Boolean(editingImplementationId)} placeholder="例如 huawei-vrp-cpu-usage" />
            </Form.Item>
            <Form.Item label="实现名称" name="name" rules={[{ required: true }]}>
              <Input placeholder="例如 Huawei VRP CPU utilization" />
            </Form.Item>
            <Form.Item className="check-item-catalog-form-wide" label="关联检查项" name="check_item_ids" rules={[{ required: true }]}>
              <Select
                mode="multiple"
                showSearch
                optionFilterProp="label"
                loading={allCheckItemsQuery.isLoading}
                options={(allCheckItemsQuery.data?.items || []).map((item) => ({
                  value: item.check_item_id,
                  label: `${item.display_name || item.name} · ${item.check_item_id}`,
                }))}
              />
            </Form.Item>
            <Form.Item className="check-item-catalog-form-wide" label="说明" name="description">
              <Input.TextArea rows={2} />
            </Form.Item>
          </div>

          <Typography.Title level={5}>设备兼容范围</Typography.Title>
          <div className="check-item-catalog-form-grid">
            <Form.Item label="厂商" name="vendors">
              <Select aria-label="厂商" mode="tags" tokenSeparators={[',']} placeholder="Cisco、H3C、Huawei" />
            </Form.Item>
            <Form.Item label="设备族" name="families">
              <Select aria-label="设备族" mode="tags" tokenSeparators={[',']} placeholder="NXOS、Comware7、VRP" />
            </Form.Item>
            <Form.Item label="平台" name="platforms">
              <Select aria-label="平台" mode="tags" tokenSeparators={[',']} placeholder="可选平台标识" />
            </Form.Item>
            <Form.Item label="型号正则" name="model_patterns">
              <Select aria-label="型号正则" mode="tags" tokenSeparators={[',']} placeholder="例如 ^N9K- 或 ^S6850" />
            </Form.Item>
            <Form.Item className="check-item-catalog-form-wide" label="系统版本正则" name="os_version_patterns">
              <Select aria-label="系统版本正则" mode="tags" tokenSeparators={[',']} placeholder="可选，例如 ^7\\." />
            </Form.Item>
          </div>

          <Typography.Title level={5}>采集命令</Typography.Title>
          <Form.Item
            label="巡检只读命令"
            name="command"
            rules={[{ required: true, message: '请输入命令' }]}
          >
            <Input placeholder="例如 display cpu-usage" />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="每个命令适配器只执行一条明确命令"
            description="平台不再尝试备用命令；设备不支持该命令时会记录原因并提示治理。"
            style={{ marginBottom: 16 }}
          />

          <Typography.Title level={5} style={{ marginTop: 20 }}>解析与选择</Typography.Title>
          <div className="check-item-catalog-form-grid">
            <Form.Item label="结果处理方式" name="parser_requirement" rules={[{ required: true }]}>
              <Select aria-label="结果处理方式" options={Object.entries(parserRequirementLabels).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
            <Form.Item label="模板兼容粒度" name="template_scope">
              <Select aria-label="模板兼容粒度" allowClear options={[
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
              label="配置结构化结果映射"
              name="configure_adapter"
              valuePropName="checked"
              extra="样本生成流程会自动填写；工程师可在发布前随时修正。文本比较模式无需配置。"
            >
              <Switch disabled={parserRequirement !== 'structured'} />
            </Form.Item>
            {configureAdapter && (
              <div className="check-item-catalog-form-wide">
                <Typography.Title level={5}>Parser 资产</Typography.Title>
                <Form.Item
                  name="parser_template_id"
                  label="Parser 版本 ID"
                  rules={[{ required: true, message: '请选择已审批的 Parser 资产版本' }]}
                  extra="一个命令适配器只引用一个 Parser；ParserBundle 可以包含多个命名记录集。"
                >
                  <Select
                    showSearch
                    optionFilterProp="label"
                    loading={activeTemplatesQuery.isLoading}
                    placeholder="选择已发布 Parser"
                    options={(activeTemplatesQuery.data?.items || []).map((item) => ({
                      value: item.id,
                      label: `${item.vendor} / ${item.profile} / ${item.canonical_command} · ${item.version}`,
                    }))}
                  />
                </Form.Item>

                <Typography.Title level={5} style={{ marginTop: 20 }}>标准结果映射</Typography.Title>
                <Form.List name="adapter_outputs">
                  {(fields, { add, remove }) => (
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      {fields.map(({ key, name, ...restField }) => (
                        <div key={key} className="check-item-adapter-output">
                          <div className="check-item-catalog-form-grid">
                            <Form.Item
                              {...restField}
                              label="检查项"
                              name={[name, 'check_item_id']}
                              rules={[{ required: true }]}
                            >
                              <Select
                                options={(allCheckItemsQuery.data?.items || [])
                                  .filter((item) => form.getFieldValue('check_item_ids')?.includes(item.check_item_id))
                                  .map((item) => ({
                                    value: item.check_item_id,
                                    label: `${item.display_name || item.name} · ${item.check_item_id}`,
                                  }))}
                                onChange={(value) => prefillOutputMappings(name, value)}
                              />
                            </Form.Item>
                            <Form.Item
                              {...restField}
                              label="来源记录集"
                              name={[name, 'source_record_set']}
                              rules={[{ required: true }]}
                            >
                              <Input placeholder="例如 cpu_cores" />
                            </Form.Item>
                            <Form.Item
                              {...restField}
                              label="空输出含义"
                              name={[name, 'empty_status']}
                              initialValue="unavailable"
                            >
                              <Select options={[
                                { value: 'unavailable', label: '无法判断' },
                                { value: 'valid_empty', label: '确认无记录' },
                                { value: 'not_applicable', label: '设备不适用' },
                              ]} />
                            </Form.Item>
                          </div>
                          <div className="check-item-catalog-form-wide">
                            <Space align="center" style={{ marginBottom: 8 }}>
                              <Typography.Text strong>标准字段映射</Typography.Text>
                              <Button size="small" onClick={() => prefillOutputMappings(name)}>
                                按检查项字段预填
                              </Button>
                            </Space>
                            <Form.List name={[name, 'field_mappings']}>
                              {(mappingFields, mappingActions) => (
                                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                                  {mappingFields.map(({ key: mappingKey, name: mappingName, ...mappingRest }) => (
                                    <div key={mappingKey} className="check-item-field-mapping-row">
                                      <Form.Item
                                        {...mappingRest}
                                        name={[mappingName, 'target_field']}
                                        rules={[{ required: true, message: '请选择标准字段' }]}
                                      >
                                        <Select
                                          showSearch
                                          optionFilterProp="label"
                                          placeholder="标准字段"
                                          options={canonicalFieldOptions}
                                        />
                                      </Form.Item>
                                      <Form.Item
                                        {...mappingRest}
                                        name={[mappingName, 'mode']}
                                        rules={[{ required: true }]}
                                      >
                                        <Select options={[
                                          { value: 'source', label: '直接取源字段' },
                                          { value: 'expression', label: '简单换算' },
                                          { value: 'literal', label: '固定值' },
                                        ]} />
                                      </Form.Item>
                                      <Form.Item
                                        {...mappingRest}
                                        name={[mappingName, 'value']}
                                        rules={[{ required: true, message: '请输入来源字段、表达式或固定值' }]}
                                      >
                                        <Input placeholder="例如 cpu_usage_percent 或 100 - cpu_idle_percent" />
                                      </Form.Item>
                                      <Button
                                        type="text"
                                        danger
                                        icon={<MinusCircleOutlined />}
                                        title="删除字段映射"
                                        onClick={() => mappingActions.remove(mappingName)}
                                      />
                                    </div>
                                  ))}
                                  <Button
                                    type="dashed"
                                    icon={<PlusOutlined />}
                                    onClick={() => mappingActions.add({ mode: 'source' })}
                                  >
                                    添加字段映射
                                  </Button>
                                </Space>
                              )}
                            </Form.List>
                          </div>
                          <Button
                            type="text"
                            danger
                            icon={<MinusCircleOutlined />}
                            onClick={() => remove(name)}
                          >
                            删除此输出
                          </Button>
                        </div>
                      ))}
                      <Button
                        type="dashed"
                        icon={<PlusOutlined />}
                        onClick={() => add({ empty_status: 'unavailable', field_mappings: [] })}
                      >
                        添加检查项输出
                      </Button>
                    </Space>
                  )}
                </Form.List>
              </div>
            )}
            <Form.Item className="check-item-catalog-form-wide" label="输出质量策略（高级 JSON）" name="output_quality_policy_json">
              <Input.TextArea rows={5} placeholder="留空继承平台默认策略" />
            </Form.Item>
            <Form.Item className="check-item-catalog-form-wide" label="修订备注" name="reason">
              <Input.TextArea rows={3} placeholder="可补充新增或变更范围、命令及预期影响" />
            </Form.Item>
          </div>
        </Form>
      </Drawer>

      <Modal
        open={Boolean(deletingImplementation)}
        title="删除命令适配器"
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          loading: deleteMutation.isLoading,
        }}
        onCancel={() => {
          setDeletingImplementation(undefined)
          setDeleteReason('')
        }}
        onOk={() => {
          if (!deletingImplementation) return
          deleteMutation.mutate({
            implementationId: deletingImplementation.implementation_id,
            reason: deleteReason.trim(),
          })
        }}
      >
        <Alert
          type="warning"
          showIcon
          message={`将删除 ${deletingImplementation?.name || ''}`}
          description="这是可追溯的逻辑删除：已发布版本会被停用并退出生产匹配，历史版本和审计记录仍会保留。仍被 Active RuntimeBundle 引用时，系统会阻止删除。"
          style={{ marginBottom: 16 }}
        />
        <Input.TextArea
          rows={3}
          value={deleteReason}
          onChange={(event) => setDeleteReason(event.target.value)}
          placeholder="删除备注（可选）"
        />
      </Modal>

      <Drawer
        open={detailOpen}
        width={isMobile ? '100%' : 860}
        title={selectedChange?.asset_kind === 'check_item' ? '检查项变更' : '采集实现变更'}
        className="rule-asset-detail-drawer"
        onClose={() => {
          setDetailOpen(false)
          setSelectedChangeId(undefined)
          setLifecycleReason('')
          setSelectedRolloutCampaignId(undefined)
        }}
      >
        {changeDetailQuery.isLoading || impactQuery.isLoading ? (
          <Typography.Text type="secondary">正在读取不可变版本与影响快照...</Typography.Text>
        ) : selectedChange && impact ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions size="small" column={isMobile ? 1 : 2} bordered>
              <Descriptions.Item label={selectedChange.asset_kind === 'check_item' ? '检查项' : '实现'}>
                {selectedChange.candidate.name}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColors[selectedChange.status]}>{statusLabels[selectedChange.status]}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="稳定 ID">{selectedChange.asset_id}</Descriptions.Item>
              <Descriptions.Item label="版本">v{selectedChange.version}</Descriptions.Item>
              <Descriptions.Item label="发起人">{selectedChange.created_by}</Descriptions.Item>
              <Descriptions.Item label="发布审批人">{selectedChange.decided_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="候选摘要">{shortDigest(selectedChange.payload_digest)}</Descriptions.Item>
              <Descriptions.Item label="影响摘要">{shortDigest(impact.impact_digest)}</Descriptions.Item>
              {selectedChange.cutover_digest && (
                <Descriptions.Item label="切换证据">{shortDigest(selectedChange.cutover_digest)}</Descriptions.Item>
              )}
            </Descriptions>

            {impactDrifted && (
              <Alert
                type="warning"
                showIcon
                message="提交后的设备或资产范围已变化"
                description="当前影响摘要与提交快照不一致，请创建新修订或重新提交，不能沿用旧审批证据。"
              />
            )}
            {impact.blockers.map((item) => (
              <Alert
                key={item.code}
                type={item.code === 'active_runtime_bundle_requires_rollout' && selectedRolloutIsEligible ? 'success' : 'error'}
                showIcon
                message={item.code === 'active_runtime_bundle_requires_rollout' && selectedRolloutIsEligible ? '灰度切换证据已就绪' : item.message}
                description={item.code === 'active_runtime_bundle_requires_rollout' && selectedRolloutIsEligible
                  ? '批准后将原子切换采集实现与候选 RuntimeBundle。'
                  : `下一步：${nextActionLabel(item.next_action)}`}
              />
            ))}
            {impact.warnings.map((item) => (
              <Alert key={item.code} type="warning" showIcon message={item.message} description={`下一步：${nextActionLabel(item.next_action)}`} />
            ))}

            {selectedCheckItemCandidate ? (
              <div className="check-item-catalog-impact-metrics">
                <div><span>活动设备</span><strong>{impact.inventory_device_count}</strong></div>
                <div><span>变更字段</span><strong>{impact.semantic_diff?.changed_fields.length || 0}</strong></div>
                <div><span>新增标准字段</span><strong>{impact.semantic_diff?.added_observation_types.length || 0}</strong></div>
                <div><span>移除标准字段</span><strong>{impact.semantic_diff?.removed_observation_types.length || 0}</strong></div>
                <div><span>采集实现</span><strong>{impact.semantic_diff?.collection_implementation_ids.length || 0}</strong></div>
              </div>
            ) : (
              <div className="check-item-catalog-impact-metrics">
                <div><span>活动设备</span><strong>{impact.inventory_device_count}</strong></div>
                <div><span>匹配变化</span><strong>{impact.summary.changed}</strong></div>
                <div><span>新增支持</span><strong>{impact.summary.newly_supported}</strong></div>
                <div><span>失去支持</span><strong>{impact.summary.newly_unsupported}</strong></div>
                <div><span>匹配歧义</span><strong>{impact.summary.ambiguous}</strong></div>
              </div>
            )}

            <section className="check-item-catalog-section">
              <Typography.Title level={5}>候选配置</Typography.Title>
              {selectedCheckItemCandidate ? (
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="检查项 ID">{selectedCheckItemCandidate.check_item_id}</Descriptions.Item>
                  <Descriptions.Item label="类别">{selectedCheckItemCandidate.category}</Descriptions.Item>
                  <Descriptions.Item label="结果 / 评估模式">
                    {parserRequirementLabels[selectedCheckItemCandidate.result_mode]} / {selectedCheckItemCandidate.assessment_mode}
                  </Descriptions.Item>
                  <Descriptions.Item label="报告投影">{selectedCheckItemCandidate.report_projection}</Descriptions.Item>
                  <Descriptions.Item label="标准输出契约">
                    <pre>{JSON.stringify(selectedCheckItemCandidate.canonical_contract, null, 2)}</pre>
                  </Descriptions.Item>
                  <Descriptions.Item label="证据完整性策略">
                    <pre>{JSON.stringify({
                      freshness_policy: selectedCheckItemCandidate.freshness_policy,
                      empty_result_policy: selectedCheckItemCandidate.empty_result_policy,
                      coverage_policy: selectedCheckItemCandidate.coverage_policy,
                    }, null, 2)}</pre>
                  </Descriptions.Item>
                </Descriptions>
              ) : selectedImplementationCandidate ? (
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="检查项">{selectedImplementationCandidate.check_item_ids.join('、')}</Descriptions.Item>
                  <Descriptions.Item label="兼容范围"><pre>{JSON.stringify(selectedImplementationCandidate.match, null, 2)}</pre></Descriptions.Item>
                  <Descriptions.Item label="命令">
                    {selectedImplementationCandidate.commands.map((item) => `${item.command} (${item.priority})`).join(' → ')}
                  </Descriptions.Item>
                  <Descriptions.Item label="结果处理">{parserRequirementLabels[selectedImplementationCandidate.parser_requirement]}</Descriptions.Item>
                </Descriptions>
              ) : null}
            </section>

            {!selectedCheckItemCandidate && <section className="check-item-catalog-section">
              <Typography.Title level={5}>设备影响明细</Typography.Title>
              <Table<CheckItemCatalogImpact['rows'][number]>
                rowKey={(row) => `${row.device_id}:${row.check_item_id}`}
                size="small"
                dataSource={impact.rows}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                scroll={{ x: 760 }}
                columns={[
                  { title: '设备', key: 'device', render: (_, row) => row.device_name || row.device_id },
                  { title: '厂商/型号', key: 'scope', render: (_, row) => [row.vendor, row.family, row.model].filter(Boolean).join(' / ') || '-' },
                  { title: '检查项', dataIndex: 'check_item_id' },
                  { title: '变化', dataIndex: 'change' },
                  { title: '原匹配', key: 'before', render: (_, row) => decisionLabel(row.before.status) },
                  { title: '新匹配', key: 'after', render: (_, row) => decisionLabel(row.after.status) },
                ]}
                locale={{ emptyText: '当前设备匹配结果没有变化' }}
              />
            </section>}

            {['draft', 'pending_approval'].includes(selectedChange.status) && (
              <section className="check-item-catalog-section">
                <Typography.Title level={5}>
                  {selectedChange.status === 'draft' ? '提交发布审批' : '独立发布决定'}
                </Typography.Title>
                {selectedChange.status === 'pending_approval' && publicationOptions?.requires_rollout && (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Typography.Text strong>灰度验证依据</Typography.Text>
                    <Select
                      value={selectedRolloutCampaignId}
                      onChange={setSelectedRolloutCampaignId}
                      loading={publicationOptionsQuery.isLoading}
                      placeholder="选择已完成且覆盖本次影响范围的灰度活动"
                      options={publicationOptions.eligible_campaigns.map((item) => ({
                        value: item.campaign_id,
                        label: `${item.name} · ${item.target_count} 个设备检查项`,
                      }))}
                      notFoundContent="没有可用于本次切换的已完成灰度活动"
                      style={{ width: '100%' }}
                    />
                    {!publicationOptions.eligible_campaigns.length && (
                      <Alert
                        type="warning"
                        showIcon
                        message="需要先完成旧、新采集实现双路由灰度"
                        description="灰度目标必须覆盖本次受影响设备并冻结候选 RuntimeBundle；完成观察阶段后才能批准切换。"
                      />
                    )}
                  </Space>
                )}
                <Input.TextArea
                  rows={3}
                  value={lifecycleReason}
                  onChange={(event) => setLifecycleReason(event.target.value)}
                  placeholder="审批备注（可选）"
                />
                <div className="check-item-catalog-actions">
                  <Button icon={<ReloadOutlined />} onClick={() => void impactQuery.refetch()}>
                    重新计算影响
                  </Button>
                  {selectedChange.status === 'draft' ? (
                    <Button
                      type="primary"
                      className="rule-asset-import-button"
                      loading={submitMutation.isLoading}
                      onClick={() => submitMutation.mutate({
                        changeId: selectedChange.id,
                        impactDigest: impact.impact_digest,
                      })}
                    >
                      提交审批
                    </Button>
                  ) : (
                    <>
                      <Button
                        danger
                        icon={<CloseOutlined />}
                        disabled={user?.role !== 'admin'}
                        loading={decisionMutation.isLoading}
                        onClick={() => decisionMutation.mutate({
                          changeId: selectedChange.id,
                          impactDigest: impact.impact_digest,
                          decision: 'reject',
                        })}
                      >
                        拒绝
                      </Button>
                      <Button
                        type="primary"
                        className="rule-asset-import-button"
                        icon={<CheckOutlined />}
                        disabled={!canApprove}
                        loading={decisionMutation.isLoading}
                        onClick={() => decisionMutation.mutate({
                          changeId: selectedChange.id,
                          impactDigest: impact.impact_digest,
                          decision: 'approve',
                          rolloutCampaignId: selectedRolloutCampaignId,
                        })}
                      >
                        {publicationOptions?.requires_rollout ? '批准并切换' : '批准发布'}
                      </Button>
                    </>
                  )}
                </div>
                {selectedChange.status === 'pending_approval' && user?.role !== 'admin' && (
                  <Typography.Text type="secondary">当前账号无发布审批权限，请由独立管理员处理。</Typography.Text>
                )}
              </section>
            )}
          </Space>
        ) : (
          <Alert
            type="error"
            showIcon
            message="变更详情加载失败"
            description={queryErrorLabel(changeDetailQuery.error || impactQuery.error)}
          />
        )}
      </Drawer>
    </div>
  )
}

export default CheckItemCatalogWorkspace
