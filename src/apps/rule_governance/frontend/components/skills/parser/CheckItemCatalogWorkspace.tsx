import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Grid,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Popconfirm,
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
import { Link, useLocation } from 'react-router-dom'
import { transferHref } from '../../../services/rulePackages'
import { useAuth } from '@netops/context/AuthContext'
import {
  parserApi,
  ruleAssetSourceApi,
  type AdapterProjectionCutoverReadiness,
  type AdapterProjectionPreviewResult,
  type AdapterProjectionReplayAttestation,
  type CheckItemCatalogChange,
  type CheckItemCatalogCheckItemCandidate,
  type CheckItemCatalogCheckItemDraftInput,
  type CheckItemCatalogImplementationCandidate,
  type CheckItemCatalogImpact,
  type CheckItemCatalogImplementationDraftInput,
  type CheckItemProductionGroup,
  type ParserCheckItem,
} from '@netops/services/api'
import { queryErrorLabel, shortDigest } from './parserUi'
import CheckItemDeleteDialog from './CheckItemDeleteDialog'
import CheckItemEditorDrawer from './CheckItemEditorDrawer'
import CheckItemOverviewDrawer, { CheckItemPatrolStatusLabel } from './CheckItemOverviewDrawer'
import CheckItemPicker, { type CheckItemPickerHandle } from './CheckItemPicker'
import CheckItemReleasePreview from './CheckItemReleasePreview'
import CheckItemRollbackDialog from './CheckItemRollbackDialog'

type CatalogView = 'catalog' | 'changes' | 'cutover'

interface CheckItemCatalogWorkspaceProps {
  focusChangeId?: string
  initialOfflineJobId?: string
}

interface CommandAdapterDraftContext {
  sourceJobId?: string
  command?: string
  vendor?: string
  family?: string
  model?: string
  profile?: string
  parserTemplateId?: string
  sourceRecordSet?: string
  adapterSpec?: Record<string, unknown>
}

type CatalogDisplayRow = ParserCheckItem & {
  pendingCommandGroups?: CheckItemProductionGroup[]
}

const PENDING_CHECK_ITEM_ID = '__pending_classification__'

const isPendingCheckItemRow = (row: CatalogDisplayRow) => (
  row.check_item_id === PENDING_CHECK_ITEM_ID
)

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
      projection_json?: string
    }>
    empty_status: 'valid_empty' | 'unavailable' | 'not_applicable'
  }>
  preview_record_sets_json?: string
  reason?: string
}

const emptyReplayAttestation = (): AdapterProjectionReplayAttestation => ({
  success: false,
  status: 'pending',
  collected_sample_count: 0,
  sample_count: 0,
  standard_records_verified: false,
  primary_keys_verified: false,
  quality_status_verified: false,
  rule_results_verified: false,
  evidence_summary_verified: false,
})

const toReplayAttestation = (
  value: Partial<AdapterProjectionReplayAttestation> | undefined,
): AdapterProjectionReplayAttestation => ({
  ...emptyReplayAttestation(),
  ...value,
  evidence_digest: value?.evidence_digest || undefined,
})

const buildDirectAdapterSpec = (
  values: ImplementationDraftForm,
  checkItemVersions: Map<string, string>,
): Record<string, unknown> | undefined => {
  if (!values.configure_adapter || values.parser_requirement !== 'structured') return undefined
  const parserTemplateId = values.parser_template_id?.trim()
  if (!parserTemplateId) throw new Error('请选择已审批的 Parser 资产版本')
  const outputs = (values.adapter_outputs || []).map((output) => {
    const checkItemId = output.check_item_id?.trim()
    const sourceRecordSet = output.source_record_set?.trim()
    if (!checkItemId || !sourceRecordSet) {
      throw new Error('每个 Projection 输出都必须绑定检查项和来源记录集')
    }
    const checkItemVersionId = checkItemVersions.get(checkItemId)
    if (!checkItemVersionId) {
      throw new Error(`检查项 ${checkItemId} 没有可用的已发布版本`)
    }
    const fields: Record<string, unknown> = {}
    for (const mapping of output.field_mappings || []) {
      const target = mapping.target_field?.trim()
      const value = mapping.value?.trim()
      if (!target || !value) {
        throw new Error(`检查项 ${checkItemId} 存在未完成的字段映射`)
      }
      if (target in fields) {
        throw new Error(`检查项 ${checkItemId} 的标准字段 ${target} 被重复映射`)
      }
      let projection: Record<string, unknown> = {}
      if (mapping.projection_json?.trim()) {
        try {
          const parsed = JSON.parse(mapping.projection_json)
          if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
            throw new Error('高级投影规则必须是 JSON 对象')
          }
          projection = parsed as Record<string, unknown>
        } catch (error) {
          throw new Error(
            `标准字段 ${target} 的投影规则无效：${error instanceof Error ? error.message : 'JSON 格式错误'}`,
          )
        }
      }
      const baseBinding = mapping.mode === 'source'
        ? value
        : { [mapping.mode]: value }
      fields[target] = typeof baseBinding === 'string'
        ? (Object.keys(projection).length ? { source: baseBinding, ...projection } : baseBinding)
        : { ...baseBinding, ...projection }
    }
    if (!Object.keys(fields).length) {
      throw new Error(`检查项 ${checkItemId} 至少需要一个字段映射`)
    }
    return {
      check_item_id: checkItemId,
      check_item_version_id: checkItemVersionId,
      source_record_set: sourceRecordSet,
      projection: { fields },
      empty_status: output.empty_status,
    }
  })
  if (!outputs.length) throw new Error('至少需要一个 Projection 输出')
  return {
    schema_version: 'command-adapter/v2',
    parser: { template_id: parserTemplateId },
    outputs,
  }
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
    const projection = Object.fromEntries(
      Object.entries(item).filter(([key]) => !['source', 'expression', 'literal'].includes(key)),
    )
    const projection_json = Object.keys(projection).length
      ? JSON.stringify(projection)
      : ''
    if ('expression' in item) {
      return { target_field: targetField, mode: 'expression' as const, value: String(item.expression || ''), projection_json }
    }
    if ('literal' in item) {
      return { target_field: targetField, mode: 'literal' as const, value: String(item.literal ?? ''), projection_json }
    }
    return { target_field: targetField, mode: 'source' as const, value: String(item.source || ''), projection_json }
  })
}

const canonicalFields = (checkItem?: ParserCheckItem): string[] => {
  const contract = checkItem?.canonical_contract || {}
  const primaryKeys = Array.isArray(contract.primary_keys)
    ? contract.primary_keys.map(String).filter((item) => item && item !== 'device_id')
    : []
  const identities = Array.isArray(contract.identity_dimensions)
    ? contract.identity_dimensions.map(String).filter((item) => item && item !== 'device_id')
    : []
  const observations = Array.isArray(contract.observations)
    ? (contract.observations as Array<Record<string, unknown>>)
      .map((item) => String(item.canonical_field || '').trim())
      .filter(Boolean)
    : []
  return Array.from(new Set([...primaryKeys, ...identities, ...observations]))
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

const adapterDraftId = (context: CommandAdapterDraftContext) => {
  const source = [context.vendor, context.family || context.profile, context.command]
    .filter(Boolean)
    .join('-')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return source ? `adapter-${source}`.slice(0, 120) : ''
}

const exactModelPattern = (model?: string) => {
  const value = model?.trim()
  return value ? `^${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$` : ''
}

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
      field_mappings: fieldMappingRows(
        item.projection && typeof item.projection === 'object'
          ? (item.projection as Record<string, unknown>).fields
          : item.fields,
      ),
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
  initialOfflineJobId,
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const { user } = useAuth()
  const canUseRules = Boolean(user?.app_ids?.includes('rule-governance'))
  const queryClient = useQueryClient()
  const [view, setView] = useState<CatalogView>('catalog')
  const [exportSelection, setExportSelection] = useState<React.Key[]>([])
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
  const [rollingBack, setRollingBack] = useState<{ checkItem: ParserCheckItem; version: number }>()
  const [projectionFeedback, setProjectionFeedback] = useState<Record<number, {
    success: boolean
    errors: string[]
    provider?: string
    attemptCount?: number
    readyForApproval?: boolean
  }>>({})
  const [adapterPreview, setAdapterPreview] = useState<AdapterProjectionPreviewResult>()
  const [cutoverAttestations, setCutoverAttestations] = useState<
    Record<string, AdapterProjectionReplayAttestation>
  >({})
  const [cutoverReason, setCutoverReason] = useState('')
  const [form] = Form.useForm<ImplementationDraftForm>()
  const navigate = useRuleTaskNavigate()
  const location = useLocation()
  const sourceJobScope = initialOfflineJobId?.trim() || undefined
  const [draftSourceContext, setDraftSourceContext] = useState<CommandAdapterDraftContext>()
  const [associationTarget, setAssociationTarget] = useState<CheckItemProductionGroup>()
  const [associationCheckItemId, setAssociationCheckItemId] = useState<string>()
  const [overviewCheckItem, setOverviewCheckItem] = useState<ParserCheckItem>()
  const associationPickerRef = useRef<CheckItemPickerHandle>(null)
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
    { keepPreviousData: true, enabled: view === 'changes' },
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
  const unclassifiedCommandsQuery = useQuery(
    ['check-item-catalog-unclassified-sample-commands', sourceJobScope],
    () => ruleAssetSourceApi.checkItemProductionGroups({
      page: 1,
      page_size: 100,
      offline_job_id: sourceJobScope,
    }),
    { keepPreviousData: true },
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
  // The trial compiles rule drafts on the server, so it runs once per opened change, not on every refocus.
  const releasePreviewQuery = useQuery(
    ['check-item-release-preview', selectedChangeId],
    () => parserApi.previewCheckItemRelease(selectedChangeId || ''),
    { enabled: Boolean(selectedChangeId && detailOpen), refetchOnWindowFocus: false, retry: false },
  )
  const publicationOptionsQuery = useQuery(
    ['check-item-catalog-publication-options', selectedChangeId],
    () => parserApi.catalogPublicationOptions(selectedChangeId || ''),
    { enabled: Boolean(selectedChangeId && detailOpen) },
  )
  const cutoverReadinessQuery = useQuery(
    ['adapter-projection-cutover-readiness'],
    () => parserApi.adapterProjectionCutoverReadiness(),
    { enabled: view === 'cutover' },
  )

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries('check-item-catalog'),
      queryClient.invalidateQueries('check-item-overview'),
      queryClient.invalidateQueries('check-item-catalog-options'),
      queryClient.invalidateQueries('check-item-catalog-changes'),
      queryClient.invalidateQueries('check-item-catalog-change'),
      queryClient.invalidateQueries('check-item-catalog-impact'),
      queryClient.invalidateQueries('check-item-release-preview'),
      queryClient.invalidateQueries('check-item-catalog-publication-options'),
      queryClient.invalidateQueries('check-item-catalog-unclassified-sample-commands'),
      queryClient.invalidateQueries('adapter-projection-cutover-readiness'),
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
        setDraftSourceContext(undefined)
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
  // A check item is approved once: submitting and approving are one action for whoever may do both.
  const publishMutation = useMutation(
    async ({ changeId, impactDigest }: { changeId: string; impactDigest: string }) => {
      const body = { reason: lifecycleReason.trim(), expected_impact_digest: impactDigest }
      await parserApi.submitCatalogChange(changeId, body, makeIdempotencyKey('catalog-submit'))
      return parserApi.decideCatalogChange(changeId, 'approve', body, makeIdempotencyKey('catalog-approve'))
    },
    {
      onSuccess: async () => {
        message.success('检查项已发布，规则和运行包已切换到新版本')
        setLifecycleReason('')
        await refreshAll()
      },
      onError: async (error) => {
        message.error(queryErrorLabel(error))
        await refreshAll()
      },
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
  const discardMutation = useMutation(
    (changeId: string) => parserApi.discardCatalogDraft(changeId),
    {
      onSuccess: async () => {
        message.success('草稿已撤销，已发布的版本不受影响')
        setDetailOpen(false)
        setSelectedChangeId(undefined)
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
        message.success('命令适配器已删除，关联运行包已停用')
        setDeletingImplementation(undefined)
        setDeleteReason('')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const deleteUnclassifiedCommandMutation = useMutation(
    ({ jobId, unitId }: { jobId: string; unitId: string }) =>
      ruleAssetSourceApi.deleteUnclassifiedCommandAsset(jobId, unitId),
    {
      onSuccess: async () => {
        message.success('待归类命令已删除，原始样本证据仍保留在导入任务中')
        await refreshAll()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const associateUnclassifiedCommandMutation = useMutation(
    ({ jobId, unitId, checkItemId }: {
      jobId: string
      unitId: string
      checkItemId: string
      command: string
    }) => ruleAssetSourceApi.associateUnclassifiedCommandAsset(jobId, unitId, {
      check_item_id: checkItemId,
      auto_continue: true,
    }),
    {
      onSuccess: async (_result, variables) => {
        if (_result.autopilot_error) message.warning(`关联已保存：${_result.autopilot_error}`)
        else message.success('关联已确认，后台自动制作已启动')
        setAssociationTarget(undefined)
        setAssociationCheckItemId(undefined)
        await refreshAll()
        // The scoped inbox intentionally hides commands once B is selected.
        // Continue directly to the only next step, rather than leaving the
        // operator on an empty source-scoped catalog with no way forward.
        const params = new URLSearchParams({
          workspace: 'production',
          subtab: 'asset-production-units',
          source_job_id: variables.jobId,
          offline_job_id: variables.jobId,
          command: variables.command,
          unit_id: variables.unitId,
          action: 'parser',
        })
        navigate(businessHref(`/skills/parser?${params.toString()}`))
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const projectionCandidateMutation = useMutation(
    ({
      data,
    }: {
      outputIndex: number
      provider: 'deterministic' | 'llm'
      data: {
        check_item_id: string
        parser_template_id: string
        source_record_set: string
        generation_provider: 'deterministic' | 'llm'
        max_agent_attempts: number
      }
    }) => parserApi.generateAdapterProjectionCandidate(data),
    {
      onSuccess: (result, variables) => {
        const proposal = result.proposal || result.validation.adapter_spec
        const outputs = Array.isArray(proposal?.outputs)
          ? proposal.outputs as Array<Record<string, unknown>>
          : []
        const candidateOutput = outputs[0]
        const projection = candidateOutput?.projection
        const fields = projection && typeof projection === 'object'
          ? (projection as Record<string, unknown>).fields
          : undefined
        const errors = result.validation.errors || []
        const agentLoop = result.agent_loop || {}
        const attemptCount = typeof agentLoop.attempt_count === 'number'
          ? agentLoop.attempt_count
          : undefined
        setProjectionFeedback((current) => ({
          ...current,
          [variables.outputIndex]: {
            success: result.validation.success,
            errors,
            provider: result.generation_provider || variables.provider,
            attemptCount,
            readyForApproval: agentLoop.ready_for_approval === true,
          },
        }))
        if (!candidateOutput || !fields || typeof fields !== 'object') {
          message.error(errors.join('；') || '候选未返回可编辑的 Projection 字段映射')
          return
        }
        const outputsInForm = form.getFieldValue('adapter_outputs') || []
        outputsInForm[variables.outputIndex] = {
          ...outputsInForm[variables.outputIndex],
          check_item_id: String(candidateOutput.check_item_id || ''),
          source_record_set: String(candidateOutput.source_record_set || ''),
          field_mappings: fieldMappingRows(fields),
        }
        form.setFieldValue('adapter_outputs', outputsInForm)
        if (result.validation.success) {
          message.success('Projection 候选已填入；请执行本地回放并提交人工审批')
        } else {
          message.warning('候选已填入，但校验未通过；请按错误提示修正')
        }
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const projectionValidationMutation = useMutation(
    ({
      data,
    }: {
      outputIndex: number
      data: {
        check_item_id: string
        parser_template_id: string
        source_record_set: string
        proposal: Record<string, unknown>
      }
    }) => parserApi.validateAdapterProjection(data),
    {
      onSuccess: (result, variables) => {
        const errors = result.validation.errors || []
        setProjectionFeedback((current) => ({
          ...current,
          [variables.outputIndex]: {
            success: result.validation.success,
            errors,
            provider: 'local validation',
          },
        }))
        if (result.validation.success) {
          message.success('Projection 字段契约校验通过')
        } else {
          message.error(errors.join('；') || 'Projection 字段契约校验失败')
        }
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const adapterPreviewMutation = useMutation(
    (data: {
      adapter_spec: Record<string, unknown>
      record_sets: Record<string, Array<Record<string, unknown>>>
      device_id?: string
      adapter_version_id?: string
    }) => parserApi.previewCommandAdapter(data),
    {
      onSuccess: (result) => {
        setAdapterPreview(result)
        if (result.success) message.success('本地 Projection 回放已完成')
        else message.warning('Projection 回放完成，但存在质量或映射错误')
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const cutoverVerificationMutation = useMutation(
    (replayByImplementationVersion: Record<string, AdapterProjectionReplayAttestation>) =>
      parserApi.verifyAdapterProjectionCutoverReadiness(replayByImplementationVersion),
    { onError: (error) => message.error(queryErrorLabel(error)) },
  )
  const cutoverRecordMutation = useMutation(
    (replayByImplementationVersion: Record<string, AdapterProjectionReplayAttestation>) =>
      parserApi.recordAdapterProjectionReplayAttestations(
        replayByImplementationVersion,
        makeIdempotencyKey('adapter-projection-replay'),
      ),
    {
      onSuccess: async () => {
        message.success('已将脱敏回放证明写入审计链')
        await queryClient.invalidateQueries('adapter-projection-cutover-readiness')
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )
  const cutoverAuthorizationMutation = useMutation(
    ({ digest, reason }: { digest: string; reason: string }) =>
      parserApi.authorizeAdapterProjectionCutover(
        { expected_readiness_digest: digest, reason },
        makeIdempotencyKey('adapter-projection-authorize'),
      ),
    {
      onSuccess: async () => {
        message.success('全量切换授权已记录；请按部署流程在所有生产 Worker 同步启用退休开关')
        await queryClient.invalidateQueries('adapter-projection-cutover-readiness')
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const openNewDraft = useCallback((
    checkItemId?: string,
    sourceContext?: CommandAdapterDraftContext,
  ) => {
    const selectedCheckItem = (allCheckItemsQuery.data?.items || []).find(
      (item) => item.check_item_id === checkItemId,
    )
    const parserRequirement = selectedCheckItem?.result_mode || 'structured'
    const command = sourceContext?.command?.trim() || ''
    const vendor = sourceContext?.vendor?.trim() || ''
    const family = sourceContext?.family?.trim() || ''
    const profile = sourceContext?.profile?.trim() || ''
    const modelPattern = exactModelPattern(sourceContext?.model)
    const candidateOutputs = Array.isArray(sourceContext?.adapterSpec?.outputs)
      ? sourceContext?.adapterSpec?.outputs as Array<Record<string, unknown>>
      : []
    const selectedOutput = candidateOutputs.find(
      (output) => String(output.check_item_id || '') === String(checkItemId || ''),
    ) || candidateOutputs[0]
    const selectedProjection = selectedOutput?.projection
      && typeof selectedOutput.projection === 'object'
      ? selectedOutput.projection as Record<string, unknown>
      : {}
    const prefilledMappings = fieldMappingRows(selectedProjection.fields)
    const hasProjection = Boolean(
      sourceContext?.parserTemplateId
      && selectedOutput?.source_record_set
      && prefilledMappings.length,
    )
    setDraftSourceContext(sourceContext)
    setEditingImplementationId(undefined)
    setDraftIdempotencyKey(makeIdempotencyKey('catalog-create'))
    setProjectionFeedback({})
    setAdapterPreview(undefined)
    form.setFieldsValue({
      id: adapterDraftId(sourceContext || {}),
      name: [vendor, family || profile, command].filter(Boolean).join(' · '),
      description: sourceContext?.sourceJobId
        ? `根据样本导入 ${sourceContext.sourceJobId} 的命令证据创建；请在此完成 Parser、Projection 的本地验证后，再提交命令适配器审批。`
        : '',
      check_item_ids: checkItemId ? [checkItemId] : [],
      vendors: vendor ? [vendor] : [],
      families: family ? [family] : [],
      platforms: !family && profile ? [profile] : [],
      model_patterns: modelPattern ? [modelPattern] : [],
      os_version_patterns: [],
      command,
      parser_requirement: parserRequirement as ImplementationDraftForm['parser_requirement'],
      template_scope: parserRequirement === 'structured' ? 'family' : undefined,
      selection_priority: 100,
      output_quality_policy_json: '',
      configure_adapter: hasProjection,
      parser_template_id: sourceContext?.parserTemplateId || '',
      adapter_outputs: hasProjection ? [{
        check_item_id: String(selectedOutput?.check_item_id || checkItemId || ''),
        source_record_set: String(selectedOutput?.source_record_set || sourceContext?.sourceRecordSet || ''),
        field_mappings: prefilledMappings,
        empty_status: String(selectedOutput?.empty_status || 'unavailable') as ImplementationDraftForm['adapter_outputs'][number]['empty_status'],
      }] : [],
      preview_record_sets_json: '',
      reason: '',
    })
    setDraftOpen(true)
  }, [allCheckItemsQuery.data, form])

  useEffect(() => {
    const state = location.state as { commandAdapterDraft?: CommandAdapterDraftContext & {
      checkItemId?: string
    } } | null
    const incoming = state?.commandAdapterDraft
    if (!incoming?.checkItemId || !incoming.adapterSpec) return
    openNewDraft(incoming.checkItemId, incoming)
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
  }, [location.pathname, location.search, location.state, navigate, openNewDraft])

  const openNewCheckItem = () => {
    setEditingCheckItem(undefined)
    setCheckItemIdempotencyKey(makeIdempotencyKey('catalog-check-item-create'))
    setCheckItemEditorOpen(true)
  }

  const canonicalFieldOptionsForOutput = (outputIndex: number) => {
    const checkItemId = form.getFieldValue(['adapter_outputs', outputIndex, 'check_item_id'])
    const checkItem = (allCheckItemsQuery.data?.items || []).find(
      (item) => item.check_item_id === checkItemId,
    )
    return canonicalFields(checkItem)
      .sort()
      .map((value) => ({ value, label: value }))
  }

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
    setDraftSourceContext(undefined)
    setEditingImplementationId(implementation.implementation_id)
    setDraftIdempotencyKey(makeIdempotencyKey('catalog-revise'))
    setProjectionFeedback({})
    setAdapterPreview(undefined)
    const allAssociations = (allCheckItemsQuery.data?.items || [])
      .filter((item) => item.implementations.some(
        (candidate) => candidate.implementation_id === implementation.implementation_id,
      ))
      .map((item) => item.check_item_id)
    form.setFieldsValue({
      ...implementationFormValues(implementation, checkItemId),
      check_item_ids: allAssociations.length ? allAssociations : [checkItemId],
      preview_record_sets_json: '',
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
      const checkItemVersions = new Map(
        (allCheckItemsQuery.data?.items || []).map((item) => [
          item.check_item_id,
          item.check_item_version_id,
        ]),
      )
      let adapterSpec: Record<string, unknown> | undefined
      try {
        adapterSpec = buildDirectAdapterSpec(values, checkItemVersions)
      } catch (error) {
        message.error(error instanceof Error ? error.message : 'Projection 配置无效')
        return
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

  const projectionSpecForOutput = (outputIndex: number): {
    values: ImplementationDraftForm
    output: ImplementationDraftForm['adapter_outputs'][number]
    adapterSpec: Record<string, unknown>
  } => {
    const values = form.getFieldsValue(true) as ImplementationDraftForm
    const output = values.adapter_outputs?.[outputIndex]
    if (!output) throw new Error('Projection 输出不存在')
    const checkItemVersions = new Map(
      (allCheckItemsQuery.data?.items || []).map((item) => [
        item.check_item_id,
        item.check_item_version_id,
      ]),
    )
    const adapterSpec = buildDirectAdapterSpec(
      { ...values, adapter_outputs: [output] },
      checkItemVersions,
    )
    if (!adapterSpec) throw new Error('当前采集实现不是结构化 Projection')
    return { values, output, adapterSpec }
  }

  const generateProjectionForOutput = (
    outputIndex: number,
    provider: 'deterministic' | 'llm',
  ) => {
    try {
      const values = form.getFieldsValue(true) as ImplementationDraftForm
      const output = values.adapter_outputs?.[outputIndex]
      if (!output) throw new Error('Projection 输出不存在')
      const checkItemId = output.check_item_id?.trim()
      const parserTemplateId = values.parser_template_id?.trim()
      const sourceRecordSet = output.source_record_set?.trim()
      if (!checkItemId || !parserTemplateId || !sourceRecordSet) {
        throw new Error('请先选择检查项、Parser 和来源记录集')
      }
      projectionCandidateMutation.mutate({
        outputIndex,
        provider,
        data: {
          check_item_id: checkItemId,
          parser_template_id: parserTemplateId,
          source_record_set: sourceRecordSet,
          generation_provider: provider,
          max_agent_attempts: 3,
        },
      })
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法生成 Projection 候选')
    }
  }

  const validateProjectionOutput = (outputIndex: number) => {
    try {
      const { values, output, adapterSpec } = projectionSpecForOutput(outputIndex)
      projectionValidationMutation.mutate({
        outputIndex,
        data: {
          check_item_id: output.check_item_id.trim(),
          parser_template_id: values.parser_template_id?.trim() || '',
          source_record_set: output.source_record_set.trim(),
          proposal: adapterSpec,
        },
      })
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法校验 Projection')
    }
  }

  const previewAdapterProjection = () => {
    try {
      const values = form.getFieldsValue(true) as ImplementationDraftForm
      const checkItemVersions = new Map(
        (allCheckItemsQuery.data?.items || []).map((item) => [
          item.check_item_id,
          item.check_item_version_id,
        ]),
      )
      const adapterSpec = buildDirectAdapterSpec(values, checkItemVersions)
      if (!adapterSpec) throw new Error('请先启用结构化 Projection')
      const rawRecordSets = String(values.preview_record_sets_json || '').trim()
      if (!rawRecordSets) {
        throw new Error('请粘贴脱敏后的 Parser record sets JSON 后再执行回放')
      }
      const parsed = JSON.parse(rawRecordSets)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error('Parser record sets 必须是 JSON 对象')
      }
      const recordSets = Object.fromEntries(
        Object.entries(parsed as Record<string, unknown>).map(([name, records]) => {
          if (!Array.isArray(records) || records.some(
            (record) => !record || Array.isArray(record) || typeof record !== 'object',
          )) {
            throw new Error(`记录集 ${name} 必须是对象数组`)
          }
          return [name, records as Array<Record<string, unknown>>]
        }),
      )
      adapterPreviewMutation.mutate({
        adapter_spec: adapterSpec,
        record_sets: recordSets,
        adapter_version_id: 'local-preview',
      })
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法执行 Projection 回放')
    }
  }

  const cutoverReport: AdapterProjectionCutoverReadiness | undefined = (
    cutoverAuthorizationMutation.data?.readiness
    || cutoverRecordMutation.data
    || cutoverVerificationMutation.data
    || cutoverReadinessQuery.data
  )

  useEffect(() => {
    if (!cutoverReport) return
    setCutoverAttestations((current) => {
      const next = { ...current }
      for (const route of cutoverReport.routes) {
        const implementationVersionId = route.implementation_version_id
        if (!next[implementationVersionId]) {
          next[implementationVersionId] = toReplayAttestation(route.replay_attestation)
        }
      }
      return next
    })
  }, [cutoverReport])

  const setCutoverAttestation = (
    implementationVersionId: string,
    patch: Partial<AdapterProjectionReplayAttestation>,
  ) => {
    setCutoverAttestations((current) => ({
      ...current,
      [implementationVersionId]: {
        ...toReplayAttestation(current[implementationVersionId]),
        ...patch,
      },
    }))
  }

  const allCutoverAttestations = (): Record<string, AdapterProjectionReplayAttestation> => (
    Object.fromEntries((cutoverReport?.routes || []).map((route) => [
      route.implementation_version_id,
      toReplayAttestation(cutoverAttestations[route.implementation_version_id]),
    ]))
  )

  const persistableCutoverAttestations = (): Record<string, AdapterProjectionReplayAttestation> => (
    Object.fromEntries(Object.entries(allCutoverAttestations()).filter(([, value]) => (
      value.success || Boolean(value.evidence_digest)
    )))
  )

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
    selectedChange?.status === 'pending_approval'
    && selectedChange.impact_digest
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
  const releasePreview = releasePreviewQuery.data
  // A check item that cannot switch as a whole must not be published at all.
  const releaseBlocked = Boolean(releasePreview?.applicable && !releasePreview.releasable)
  const canApprove = Boolean(
    canUseRules
    && selectedChange?.status === 'pending_approval'
    && impact
    && !unresolvedBlockers.length
    && !impactDrifted
    && !releaseBlocked,
  )
  const canPublish = Boolean(
    canUseRules
    && selectedChange?.status === 'draft'
    && selectedChange.asset_kind === 'check_item'
    && impact
    && !unresolvedBlockers.length
    && releasePreview?.releasable,
  )
  const catalogColumns: ColumnsType<CatalogDisplayRow> = [
    {
      title: '检查项',
      key: 'check_item',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            {isPendingCheckItemRow(row)
              ? <Typography.Text strong>{row.display_name || row.name}</Typography.Text>
              : (
                <button
                  type="button"
                  className="check-item-name-button"
                  title="查看详情"
                  onClick={() => setOverviewCheckItem(row)}
                >
                  {row.display_name || row.name}
                </button>
              )}
            {isPendingCheckItemRow(row) && <Tag color="orange">样本自动生成</Tag>}
          </Space>
          <Typography.Text type="secondary">
            {isPendingCheckItemRow(row)
              ? '样本导入命令：结构化命令关联后继续 Parser / Projection；文本命令创建文本命令适配器'
              : row.check_item_id}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '类别',
      dataIndex: 'category',
      width: 100,
      render: (value: string, row) => isPendingCheckItemRow(row) ? '待归类' : value,
    },
    {
      title: '结果模式',
      dataIndex: 'result_mode',
      width: 138,
      render: (value: string, row) => isPendingCheckItemRow(row)
        ? '样本待处理'
        : parserRequirementLabels[value] || value,
    },
    {
      title: '命令适配器',
      dataIndex: 'implementation_count',
      width: 96,
      render: (value: number, row) => isPendingCheckItemRow(row)
        ? `${row.pendingCommandGroups?.length || 0} 条`
        : `${value} 个`,
    },
    {
      title: '版本',
      key: 'version',
      width: 64,
      render: (_, row) => isPendingCheckItemRow(row) ? '-' : `v${row.version}`,
    },
    {
      title: '巡检状态',
      key: 'patrol_status',
      width: 222,
      render: (_, row) => isPendingCheckItemRow(row) ? '-' : <CheckItemPatrolStatusLabel status={row.patrol_status} />,
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, row) => isPendingCheckItemRow(row) ? null : (
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
            disabled={!canUseRules}
            onClick={() => {
              setDeletingCheckItem(row)
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
            disabled={!canUseRules}
            onClick={() => {
              setDeletingImplementation(row)
              setDeleteReason('')
            }}
          />
        </Space>
      ),
    },
  ]
  const unclassifiedCommandGroups = (unclassifiedCommandsQuery.data?.items || []).filter(
    (group) => group.classification_required,
  )
  const canDeleteUnclassifiedCommand = canUseRules
  const catalogRows = useMemo<CatalogDisplayRow[]>(() => {
    const checkItems = catalogQuery.data?.items || []
    if (!unclassifiedCommandGroups.length) return sourceJobScope ? [] : checkItems
    const pendingRow: CatalogDisplayRow = {
      check_item_id: PENDING_CHECK_ITEM_ID,
      check_item_version_id: PENDING_CHECK_ITEM_ID,
      version: 0,
      name: '待归类检查项',
      display_name: '待归类检查项',
      category: 'unclassified',
      description: '样本导入后自动生成；每条命令只需一次关联 CheckItem，随后完成 Parser、Projection 与适配器审批。',
      display_description: '样本导入后自动生成；每条命令只需一次关联 CheckItem，随后完成 Parser、Projection 与适配器审批。',
      result_mode: 'pending_classification',
      assessment_mode: 'evidence',
      canonical_contract: {},
      report_projection: '',
      applicability: {},
      freshness_policy: {},
      empty_result_policy: {},
      coverage_policy: {},
      status: 'active',
      digest: '',
      implementation_count: unclassifiedCommandGroups.length,
      implementations: [],
      pendingCommandGroups: unclassifiedCommandGroups,
    }
    return sourceJobScope ? [pendingRow] : [pendingRow, ...checkItems]
  }, [catalogQuery.data?.items, sourceJobScope, unclassifiedCommandGroups])
  const sourceContextForGroup = (group: CheckItemProductionGroup): CommandAdapterDraftContext | undefined => {
    const unit = group.implementations[0]
    if (!unit) return undefined
    return {
      sourceJobId: unit.offline_job_id,
      command: unit.canonical_command,
      vendor: unit.vendor,
      family: unit.family || undefined,
      model: unit.model || undefined,
      profile: unit.profile,
    }
  }
  const openSourceEvidence = (group: CheckItemProductionGroup) => {
    const source = sourceContextForGroup(group)
    if (!source?.sourceJobId || !source.command) return
    navigate(
      businessHref(`/skills/parser/source-jobs/${encodeURIComponent(source.sourceJobId)}`)
      + `?tab=assets&command=${encodeURIComponent(source.command)}`,
    )
  }
  const openAssociationDialog = (group: CheckItemProductionGroup) => {
    setAssociationTarget(group)
    setAssociationCheckItemId(group.implementations[0]?.candidate_check_item_id || undefined)
  }
  const confirmAssociation = () => {
    const source = associationTarget ? sourceContextForGroup(associationTarget) : undefined
    const unitId = associationTarget?.implementations[0]?.id
    if (!associationCheckItemId || !source?.sourceJobId || !source.command || !unitId) return
    associateUnclassifiedCommandMutation.mutate({
      jobId: source.sourceJobId,
      unitId,
      checkItemId: associationCheckItemId,
      command: source.command,
    })
  }
  const openAssociatedParser = (group: CheckItemProductionGroup) => {
    const source = sourceContextForGroup(group)
    if (!source?.sourceJobId || !source.command) return
    const params = new URLSearchParams({
      workspace: 'production',
      subtab: 'asset-production-units',
      source_job_id: source.sourceJobId,
      offline_job_id: source.sourceJobId,
      command: source.command,
      action: 'parser',
    })
    navigate(businessHref(`/skills/parser?${params.toString()}`))
  }
  const openTextAdapter = (group: CheckItemProductionGroup) => {
    const checkItem = associatedCheckItem(group)
    if (!checkItem) {
      message.error('请先关联有效的 CheckItem')
      return
    }
    openNewDraft(checkItem.check_item_id, sourceContextForGroup(group))
  }
  const associatedCheckItem = (group: CheckItemProductionGroup) => {
    const unit = group.implementations[0]
    return (allCheckItemsQuery.data?.items || []).find(
      (item) => item.check_item_id === unit?.candidate_check_item_id,
    )
  }
  const associationNeedsReconfirm = (group: CheckItemProductionGroup) => {
    const unit = group.implementations[0]
    const current = associatedCheckItem(group)
    return Boolean(
      unit?.candidate_check_item_id
      && current?.check_item_version_id
      && unit.candidate_check_item_version_id
      && current.check_item_version_id !== unit.candidate_check_item_version_id,
    )
  }
  const unclassifiedCommandColumns: ColumnsType<CheckItemProductionGroup> = [
    {
      title: '样本命令',
      key: 'command',
      render: (_, group) => {
        const source = sourceContextForGroup(group)
        return source?.command ? (
          <Typography.Text
            code
            style={{ cursor: 'context-menu' }}
            title="右键关联检查项"
            onContextMenu={(event) => {
              event.preventDefault()
              openAssociationDialog(group)
            }}
          >
            {source.command}
          </Typography.Text>
        ) : '-'
      },
    },
    {
      title: '样本来源',
      key: 'source',
      render: (_, group) => {
        const source = sourceContextForGroup(group)
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text>{group.sample_count} 份输出 · {group.evidence_device_count} 台设备</Typography.Text>
            <Typography.Text type="secondary">任务 {source?.sourceJobId || '-'}</Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '样本范围',
      key: 'scope',
      render: (_, group) => {
        const source = sourceContextForGroup(group)
        return [source?.vendor, source?.family || source?.profile, source?.model]
          .filter(Boolean)
          .join(' / ') || '-'
      },
    },
    {
      title: '业务目标',
      key: 'check_item',
      render: (_, group) => {
        const unit = group.implementations[0]
        const current = associatedCheckItem(group)
        const needsReconfirm = associationNeedsReconfirm(group)
        return unit?.candidate_check_item_id ? (
          <Space direction="vertical" size={0}>
            <Tag color={needsReconfirm ? 'orange' : 'blue'}>
              {needsReconfirm ? '检查项已更新，待重新确认' : '已关联，未发布'}
            </Tag>
            <Typography.Text>{unit.candidate_check_item_name || unit.candidate_check_item_id}</Typography.Text>
            {current?.result_mode !== 'structured' && <Typography.Text type="secondary">文本/证据路径，不生成 TextFSM</Typography.Text>}
          </Space>
        ) : <Typography.Text type="secondary">待关联 CheckItem</Typography.Text>
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 360,
      render: (_, group) => {
        const associated = Boolean(group.implementations[0]?.candidate_check_item_id)
        const current = associatedCheckItem(group)
        const needsReconfirm = associationNeedsReconfirm(group)
        const structured = current?.result_mode === 'structured'
        return (
        <Space wrap>
          <Button
            type={associated ? 'default' : 'primary'}
            loading={associateUnclassifiedCommandMutation.isLoading}
            onClick={() => openAssociationDialog(group)}
          >
            {needsReconfirm ? '重新确认检查项' : associated ? '更换检查项' : '关联检查项'}
          </Button>
          {associated && structured && !needsReconfirm && (
            <Button type="primary" onClick={() => openAssociatedParser(group)}>
              生成 Parser / Projection
            </Button>
          )}
          {associated && !structured && !needsReconfirm && (
            <Button type="primary" onClick={() => openTextAdapter(group)}>
              创建文本命令适配器
            </Button>
          )}
          <Button onClick={() => openSourceEvidence(group)}>查看证据</Button>
          <Popconfirm
            title="删除待归类命令？"
            description="将从待归类列表移除该命令，原始样本证据会继续保留。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{
              danger: true,
              loading: deleteUnclassifiedCommandMutation.isLoading,
            }}
            onConfirm={() => {
              const source = sourceContextForGroup(group)
              const unitId = group.implementations[0]?.id
              if (!source?.sourceJobId || !unitId) return
              deleteUnclassifiedCommandMutation.mutate({
                jobId: source.sourceJobId,
                unitId,
              })
            }}
          >
            <Button
              danger
              disabled={!canDeleteUnclassifiedCommand}
              loading={deleteUnclassifiedCommandMutation.isLoading}
            >
              删除命令
            </Button>
          </Popconfirm>
        </Space>
        )
      },
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
            检查项目录
          </Typography.Title>
          <Typography.Text type="secondary">
            维护检查标准和设备适配范围；生成任务在制作工作台中继续。
          </Typography.Text>
        </div>
        <Space>
          <Link to={transferHref(exportSelection.map(id => `check_version:${id}`))}><Button>规则包{exportSelection.length ? `导出 (${exportSelection.length})` : '导入导出'}</Button></Link>
          <Button icon={<ReloadOutlined />} title="刷新" onClick={() => void refreshAll()} />
          <Button
            type="primary"
            className="rule-asset-import-button"
            icon={<PlusOutlined />}
            onClick={openNewCheckItem}
          >
            新增检查项
          </Button>
        </Space>
      </div>

      <div
        className="rule-asset-view-switch"
        role="tablist"
        aria-label="检查项目录视图"
      >
        <button
          type="button"
          role="tab"
          className={view === 'catalog' ? 'is-active' : ''}
          aria-selected={view === 'catalog'}
          onClick={() => setView('catalog')}
        >
          检查项与命令适配器
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
        {canUseRules && <details className="rule-workspace-tools"><summary>平台管理</summary>        <button
          type="button"
          role="tab"
          className={view === 'cutover' ? 'is-active' : ''}
          aria-selected={view === 'cutover'}
          onClick={() => setView('cutover')}
        >
          回放与切换
        </button>
</details>}
      </div>

      {view === 'cutover' ? (
        <Space direction="vertical" size={16} style={{ width: '100%', marginTop: 14 }}>
          <Alert
            type="info"
            showIcon
            message="全量一次切换门禁"
            description="此处只接收脱敏的回放证明摘要。原始 CLI 继续保存在证据链；所有活动结构化路由均完成 V2 Projection、Parser 和全量回放后，才能签发部署授权。"
          />
          {cutoverReadinessQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="切换就绪度加载失败"
              description={queryErrorLabel(cutoverReadinessQuery.error)}
            />
          )}
          {cutoverReadinessQuery.isLoading && !cutoverReport ? (
            <Typography.Text type="secondary">正在清点活动结构化路由...</Typography.Text>
          ) : cutoverReport ? (
            <Form component={false} layout="vertical">
              <Descriptions size="small" column={isMobile ? 1 : 4} bordered>
                <Descriptions.Item label="活动路由">{cutoverReport.route_count}</Descriptions.Item>
                <Descriptions.Item label="已就绪">{cutoverReport.ready_route_count}</Descriptions.Item>
                <Descriptions.Item label="被阻断">{cutoverReport.blocked_route_count}</Descriptions.Item>
                <Descriptions.Item label="当前摘要">{shortDigest(cutoverReport.digest)}</Descriptions.Item>
              </Descriptions>
              <Alert
                type={cutoverReport.production_cutover_permitted ? 'success' : 'warning'}
                showIcon
                message={cutoverReport.production_cutover_permitted
                  ? '所有活动结构化路由均满足切换硬门槛'
                  : '尚未满足全量切换硬门槛'}
                description={cutoverReport.production_cutover_permitted
                  ? '可由有应用使用权的用户确认部署授权；授权本身不会在单个 API 进程内切换生产运行时。'
                  : '补齐 V2 Projection、活动 Parser 或完整原始 CLI 回放证明后重新验证。'}
              />
              {cutoverReport.cutover_authorization && (
                <Alert
                  type={cutoverReport.cutover_authorization.matches_current_readiness ? 'success' : 'warning'}
                  showIcon
                  message={cutoverReport.cutover_authorization.matches_current_readiness
                    ? '当前就绪度已有有效切换授权'
                    : '历史切换授权已不匹配当前路由清单'}
                  description={`授权人：${cutoverReport.cutover_authorization.actor || '-'}；摘要：${shortDigest(cutoverReport.cutover_authorization.readiness_digest)}`}
                />
              )}
              {cutoverReport.routes.map((route) => {
                const implementationVersionId = route.implementation_version_id
                const attestation = toReplayAttestation(cutoverAttestations[implementationVersionId])
                return (
                  <section key={implementationVersionId} className="check-item-catalog-section">
                    <Space direction="vertical" size={10} style={{ width: '100%' }}>
                      <Space wrap>
                        <Typography.Text strong>{implementationVersionId}</Typography.Text>
                        <Tag color={route.status === 'ready' ? 'success' : 'error'}>
                          {route.status === 'ready' ? '已就绪' : '被阻断'}
                        </Tag>
                        {(cutoverReport.replay_attestation_sources || {})[implementationVersionId] === 'audit' && (
                          <Tag color="blue">已审计</Tag>
                        )}
                      </Space>
                      <Typography.Text type="secondary">
                        CheckItem 版本：{route.check_item_version_ids.join('、') || '-'}
                      </Typography.Text>
                      <div className="check-item-catalog-form-grid">
                        <Form.Item label="已采集样本数">
                          <InputNumber
                            min={0}
                            value={attestation.collected_sample_count}
                            onChange={(value) => setCutoverAttestation(implementationVersionId, {
                              collected_sample_count: Number(value || 0),
                            })}
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                        <Form.Item label="已回放样本数">
                          <InputNumber
                            min={0}
                            value={attestation.sample_count}
                            onChange={(value) => setCutoverAttestation(implementationVersionId, {
                              sample_count: Number(value || 0),
                            })}
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                        <Form.Item label="回放状态">
                          <Select
                            value={attestation.status}
                            onChange={(value) => setCutoverAttestation(implementationVersionId, {
                              status: value as AdapterProjectionReplayAttestation['status'],
                            })}
                            options={[
                              { value: 'passed', label: '通过' },
                              { value: 'complete', label: '完成' },
                              { value: 'ready', label: '就绪' },
                              { value: 'failed', label: '失败' },
                              { value: 'blocked', label: '阻断' },
                              { value: 'pending', label: '待处理' },
                            ]}
                          />
                        </Form.Item>
                        <Form.Item label="证据摘要 SHA-256">
                          <Input
                            value={attestation.evidence_digest || ''}
                            onChange={(event) => setCutoverAttestation(implementationVersionId, {
                              evidence_digest: event.target.value.trim() || undefined,
                            })}
                            placeholder="64 位摘要；不粘贴原始 CLI"
                          />
                        </Form.Item>
                      </div>
                      <Space wrap>
                        <Checkbox
                          checked={attestation.success}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            success: event.target.checked,
                            status: event.target.checked ? 'passed' : 'pending',
                          })}
                        >
                          回放通过
                        </Checkbox>
                        <Checkbox
                          checked={attestation.standard_records_verified}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            standard_records_verified: event.target.checked,
                          })}
                        >
                          标准记录
                        </Checkbox>
                        <Checkbox
                          checked={attestation.primary_keys_verified}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            primary_keys_verified: event.target.checked,
                          })}
                        >
                          主键
                        </Checkbox>
                        <Checkbox
                          checked={attestation.quality_status_verified}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            quality_status_verified: event.target.checked,
                          })}
                        >
                          质量状态
                        </Checkbox>
                        <Checkbox
                          checked={attestation.rule_results_verified}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            rule_results_verified: event.target.checked,
                          })}
                        >
                          规则结果
                        </Checkbox>
                        <Checkbox
                          checked={attestation.evidence_summary_verified}
                          onChange={(event) => setCutoverAttestation(implementationVersionId, {
                            evidence_summary_verified: event.target.checked,
                          })}
                        >
                          证据摘要
                        </Checkbox>
                      </Space>
                      {route.blockers.map((blocker) => (
                        <Alert
                          key={`${implementationVersionId}:${blocker.code}:${blocker.message}`}
                          type="error"
                          showIcon
                          message={blocker.message}
                        />
                      ))}
                    </Space>
                  </section>
                )
              })}
              <Input.TextArea
                rows={3}
                value={cutoverReason}
                onChange={(event) => setCutoverReason(event.target.value)}
                placeholder="全量切换授权备注（签发授权时必填）"
              />
              <div className="check-item-catalog-actions">
                <Button
                  aria-label="验证全量回放证明"
                  icon={<ReloadOutlined />}
                  loading={cutoverVerificationMutation.isLoading}
                  onClick={() => cutoverVerificationMutation.mutate(allCutoverAttestations())}
                >
                  验证全量回放证明
                </Button>
                <Button
                  aria-label="写入审计链"
                  loading={cutoverRecordMutation.isLoading}
                  onClick={() => {
                    const attestations = persistableCutoverAttestations()
                    if (!Object.keys(attestations).length) {
                      message.error('至少填写一条已完成的回放证明后才能写入审计链')
                      return
                    }
                    cutoverRecordMutation.mutate(attestations)
                  }}
                >
                  写入审计链
                </Button>
                <Button
                  aria-label="签发部署授权"
                  type="primary"
                  className="rule-asset-import-button"
                  icon={<CheckOutlined />}
                  disabled={!canUseRules || !cutoverReport.production_cutover_permitted}
                  loading={cutoverAuthorizationMutation.isLoading}
                  onClick={() => {
                    if (!cutoverReason.trim()) {
                      message.error('请填写全量切换授权备注')
                      return
                    }
                    cutoverAuthorizationMutation.mutate({
                      digest: cutoverReport.digest,
                      reason: cutoverReason.trim(),
                    })
                  }}
                >
                  签发部署授权
                </Button>
              </div>
              {!canUseRules && (
                <Typography.Text type="secondary">回放证明可由工程师提交；最终部署授权需由有应用使用权的用户确认。</Typography.Text>
              )}
            </Form>
          ) : null}
        </Space>
      ) : view === 'catalog' ? (
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 14 }}>
          {sourceJobScope && (
            <Alert
              type="info"
              showIcon
              message="本次样本导入的待归类命令"
              description="这里显示该导入任务尚未完成命令适配器接入的命令。先关联 CheckItem；结构化命令继续生成 Parser 和 Projection，文本命令直接创建文本适配器。"
              action={(
                <Space size={4}>
                  <Button
                    size="small"
                    onClick={() => navigate(businessHref('/skills/parser?workspace=production&subtab=catalog-management'))}
                  >
                    查看全部目录
                  </Button>
                </Space>
              )}
            />
          )}
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
          {unclassifiedCommandsQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="待归类检查项加载失败"
              action={<Button onClick={() => unclassifiedCommandsQuery.refetch()}>重试</Button>}
            />
          )}
          <Table<CatalogDisplayRow>
            rowKey="check_item_version_id"
            rowSelection={{ selectedRowKeys: exportSelection, preserveSelectedRowKeys: true, onChange: setExportSelection,
              getCheckboxProps: row => ({ disabled: isPendingCheckItemRow(row), 'aria-label': `选择检查项 ${row.check_item_version_id}` }),
              getTitleCheckboxProps: () => ({ 'aria-label': '全选当前页检查项' }) }}
            loading={catalogQuery.isLoading || catalogQuery.isFetching
              || unclassifiedCommandsQuery.isLoading || unclassifiedCommandsQuery.isFetching}
            dataSource={catalogRows}
            columns={catalogColumns}
            expandable={{
              columnTitle: <span className="visually-hidden">展开命令适配器</span>,
              rowExpandable: (row) => isPendingCheckItemRow(row)
                ? Boolean(row.pendingCommandGroups?.length)
                : row.implementations.length > 0,
              expandedRowRender: (row) => isPendingCheckItemRow(row) ? (
                <Table<CheckItemProductionGroup>
                  rowKey="id"
                  size="small"
                  dataSource={row.pendingCommandGroups || []}
                  columns={unclassifiedCommandColumns}
                  pagination={false}
                  scroll={{ x: 980 }}
                  locale={{ emptyText: '暂无待归类命令' }}
                />
              ) : row.implementations.length ? (
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
            scroll={{ x: 1040 }}
            // The server already returns one page. The pinned 待归类 row makes the list one
            // longer than the page size, and a table that pages would slice it again.
            pagination={false}
          />
          {!sourceJobScope && (
            <Pagination
              align="end"
              current={catalogPage}
              pageSize={catalogPageSize}
              total={catalogQuery.data?.total || 0}
              showSizeChanger
              showTotal={(total) => `共 ${total} 个检查项${unclassifiedCommandGroups.length ? `，另有 1 个待归类分组` : ''}`}
              onChange={(page, pageSize) => {
                setCatalogPage(pageSize === catalogPageSize ? page : 1)
                setCatalogPageSize(pageSize)
              }}
            />
          )}
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
            <Alert type="error" showIcon message="变更记录加载失败" description={queryErrorLabel(changesQuery.error)} action={<Button onClick={() => void changesQuery.refetch()}>重试</Button>} />
          )}
          <Table<CheckItemCatalogChange>
            rowKey="id"
            loading={changesQuery.isLoading || changesQuery.isFetching}
            dataSource={changesQuery.data?.items || []}
            columns={changeColumns}
            scroll={{ x: 820 }}
            pagination={false}
            locale={{ emptyText: changesQuery.isError ? '变更记录暂不可用，请重试或检查候选资产完整性。' : '尚无变更记录' }}
          />
          <Pagination
            align="end"
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

      <CheckItemOverviewDrawer
        open={Boolean(overviewCheckItem)}
        mobile={isMobile}
        checkItem={overviewCheckItem}
        onClose={() => setOverviewCheckItem(undefined)}
        onDelete={(checkItem) => {
          setOverviewCheckItem(undefined)
          setDeletingCheckItem(checkItem)
        }}
        onRollback={(checkItem, version) => {
          setOverviewCheckItem(undefined)
          setRollingBack({ checkItem, version })
        }}
        onEdit={(checkItem) => {
          setOverviewCheckItem(undefined)
          openCheckItemRevision(checkItem)
        }}
      />

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
        onOpenRuleBundle={(bundleId, checkItemVersionId) => {
          const params = new URLSearchParams({
            workspace: 'production',
            subtab: 'asset-production-units',
            action: 'release',
            bundle_id: bundleId,
            check_item_version_id: checkItemVersionId,
          })
          setCheckItemEditorOpen(false)
          navigate(businessHref(`/skills/parser?${params.toString()}`))
        }}
      />

      <Modal
        open={Boolean(associationTarget)}
        title={associationTarget?.implementations[0]?.candidate_check_item_id ? '更换关联检查项' : '关联检查项'}
        width={720}
        centered
        afterOpenChange={(open) => {
          if (open) associationPickerRef.current?.focus()
        }}
        okText="确认关联"
        cancelText="取消"
        okButtonProps={{ disabled: !associationCheckItemId }}
        confirmLoading={associateUnclassifiedCommandMutation.isLoading}
        destroyOnHidden
        onOk={confirmAssociation}
        onCancel={() => {
          if (associateUnclassifiedCommandMutation.isLoading) return
          setAssociationTarget(undefined)
          setAssociationCheckItemId(undefined)
        }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text>
            这条命令的输出属于哪个检查项？<Typography.Text code>{associationTarget ? sourceContextForGroup(associationTarget)?.command || '-' : '-'}</Typography.Text>
          </Typography.Text>
          <CheckItemPicker
            ref={associationPickerRef}
            items={allCheckItemsQuery.data?.items || []}
            value={associationCheckItemId}
            loading={allCheckItemsQuery.isLoading}
            modeLabels={parserRequirementLabels}
            onChange={setAssociationCheckItemId}
          />
          <Typography.Text type="secondary">
            确认后，系统按此检查项自动完成解析、字段转换、规则生成和验证；可随时暂停接管。
          </Typography.Text>
        </Space>
      </Modal>

      <CheckItemDeleteDialog
        checkItem={deletingCheckItem}
        onClose={() => setDeletingCheckItem(undefined)}
        onDeleted={refreshAll}
      />
      <CheckItemRollbackDialog
        checkItem={rollingBack?.checkItem}
        restoresVersion={rollingBack?.version}
        onClose={() => setRollingBack(undefined)}
        onDone={refreshAll}
      />

      <Drawer
        open={draftOpen}
        width={isMobile ? '100%' : 760}
        title={editingImplementationId ? '编辑命令适配器' : '新增命令适配器'}
        onClose={() => {
          setDraftOpen(false)
          setDraftSourceContext(undefined)
        }}
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
          description={canUseRules
            ? 'Active 版本不会被覆盖；发布前必须检查设备命中、歧义和运行包影响。用户可审批本人提交的变更，操作会写入审计记录。'
            : 'Active 版本不会被覆盖；发布前必须检查设备命中、歧义和运行包影响，并由另一账号审批。'}
          style={{ marginBottom: 16 }}
        />
        {draftSourceContext?.command && !editingImplementationId && (
          <Alert
            type="warning"
            showIcon
            message="正在为样本命令归类并创建适配器"
            description={(
              <Space direction="vertical" size={4}>
                <Typography.Text>
                  <Typography.Text code>{draftSourceContext.command}</Typography.Text>
                  {' · '}
                  {[draftSourceContext.vendor, draftSourceContext.family || draftSourceContext.profile, draftSourceContext.model]
                    .filter(Boolean)
                    .join(' / ') || '样本设备范围'}
                </Typography.Text>
                <Typography.Text type="secondary">
                  先选择已有检查项；如果语义确实不同，再关闭此抽屉新建检查项后返回。
                </Typography.Text>
                {draftSourceContext.sourceJobId && (
                  <Button
                    size="small"
                    style={{ width: 'fit-content' }}
                    onClick={() => navigate(
                      `/skills/parser/source-jobs/${encodeURIComponent(draftSourceContext.sourceJobId || '')}`
                      + `?tab=assets&command=${encodeURIComponent(draftSourceContext.command || '')}`,
                    )}
                  >
                    返回命令证据
                  </Button>
                )}
              </Space>
            )}
            style={{ marginBottom: 16 }}
          />
        )}
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
                              <Button
                                size="small"
                                loading={projectionCandidateMutation.isLoading}
                                onClick={() => generateProjectionForOutput(name, 'deterministic')}
                              >
                                生成候选
                              </Button>
                              <Button
                                size="small"
                                loading={projectionCandidateMutation.isLoading}
                                onClick={() => generateProjectionForOutput(name, 'llm')}
                              >
                                AI 候选（最多三轮）
                              </Button>
                              <Button
                                size="small"
                                loading={projectionValidationMutation.isLoading}
                                onClick={() => validateProjectionOutput(name)}
                              >
                                校验 Projection
                              </Button>
                            </Space>
                            {projectionFeedback[name] && (
                              <Alert
                                type={projectionFeedback[name].success ? 'success' : 'warning'}
                                showIcon
                                message={projectionFeedback[name].success
                                  ? '当前 Projection 已通过字段契约校验'
                                  : '当前 Projection 仍需修正'}
                                description={[
                                  projectionFeedback[name].errors.length
                                    ? projectionFeedback[name].errors.join('；')
                                    : `来源：${projectionFeedback[name].provider || '本地编辑'}`,
                                  projectionFeedback[name].attemptCount
                                    ? `AI 修正轮次：${projectionFeedback[name].attemptCount}/3`
                                    : '',
                                  projectionFeedback[name].readyForApproval
                                    ? '候选仍须经人工确认与发布审批。'
                                    : '',
                                ].filter(Boolean).join('；')}
                                style={{ marginBottom: 8 }}
                              />
                            )}
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
                                          options={canonicalFieldOptionsForOutput(name)}
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
                                      <details><summary>单位与高级转换</summary>
                                      <Form.Item
                                        {...mappingRest}
                                        name={[mappingName, 'projection_json']}
                                        extra='可选：填写 value_mapping、transform、source_unit、canonical_unit、dimensions（如 {"window":"instant"}）等受限投影规则 JSON。'
                                      >
                                        <Input placeholder='高级投影 JSON，例如 {"value_mapping":{"UP":"up"}}' />
                                      </Form.Item>
                                      </details>
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
                <Alert
                  type="info"
                  showIcon
                  message="本地 Projection 回放"
                  description="粘贴脱敏后的 Parser record sets（不是原始 CLI）。回放会同时展示标准记录、质量状态和直接产出的 Observation；结果不会随草稿保存。"
                  style={{ marginTop: 16, marginBottom: 12 }}
                />
                <Form.Item
                  label="Parser record sets JSON"
                  name="preview_record_sets_json"
                  extra='例如 {"interfaces":[{"name":"GE1/0/1","link":"UP"}]}'
                >
                  <Input.TextArea rows={6} placeholder='{"record_set":[{"source_field":"value"}]}' />
                </Form.Item>
                <Button
                  onClick={previewAdapterProjection}
                  loading={adapterPreviewMutation.isLoading}
                >
                  执行本地 Projection 回放
                </Button>
              </div>
            )}
            <details className="check-item-catalog-form-wide"><summary>高级输出质量策略</summary>
            <Form.Item className="check-item-catalog-form-wide" label="输出质量策略（高级 JSON）" name="output_quality_policy_json">
              <Input.TextArea rows={5} placeholder="留空继承平台默认策略" />
            </Form.Item>
            </details>
            <Form.Item className="check-item-catalog-form-wide" label="修订备注" name="reason">
              <Input.TextArea rows={3} placeholder="可补充新增或变更范围、命令及预期影响" />
            </Form.Item>
          </div>
        </Form>
      </Drawer>

      <Modal
        open={Boolean(adapterPreview)}
        title="本地 Projection 回放结果"
        width={isMobile ? '100%' : 980}
        footer={<Button onClick={() => setAdapterPreview(undefined)}>关闭</Button>}
        onCancel={() => setAdapterPreview(undefined)}
      >
        {adapterPreview && (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Alert
              type={adapterPreview.success ? 'success' : 'warning'}
              showIcon
              message={adapterPreview.success ? 'Projection 回放通过' : 'Projection 回放发现质量或映射问题'}
              description={`输出批次：${adapterPreview.batches.length}；标准 Observation：${adapterPreview.observations.length}`}
            />
            <Table<Record<string, unknown>>
              rowKey={(row, index) => String(row.id || row.observation_id || index)}
              size="small"
              dataSource={adapterPreview.observations}
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              scroll={{ x: 760 }}
              columns={[
                { title: '检查项', key: 'check_item', render: (_, row) => String((row.metadata as Record<string, unknown> | undefined)?.check_item_id || '-') },
                { title: '实体', dataIndex: 'entity_key', key: 'entity_key' },
                { title: '标准字段', dataIndex: 'field_name', key: 'field_name' },
                { title: '标准观测', dataIndex: 'observation_type', key: 'observation_type' },
                { title: '值', dataIndex: 'value', key: 'value', render: (value) => typeof value === 'object' ? JSON.stringify(value) : String(value ?? '-') },
                { title: '维度', dataIndex: 'dimensions', key: 'dimensions', render: (value) => value ? JSON.stringify(value) : '-' },
              ]}
              locale={{ emptyText: '没有产生标准 Observation；请检查质量状态、主键和字段映射。' }}
            />
            <Typography.Text strong>批次质量与错误</Typography.Text>
            {adapterPreview.batches.map((batch, index) => (
              <Alert
                key={`${batch.check_item_id}:${index}`}
                type={['complete', 'valid_empty', 'not_applicable'].includes(batch.quality_status) ? 'success' : 'warning'}
                showIcon
                message={`${batch.check_item_id} · ${batch.quality_status} · ${batch.records.length} 条记录`}
                description={batch.errors.length ? batch.errors.join('；') : '无 Adapter 执行错误'}
              />
            ))}
          </Space>
        )}
      </Modal>

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
          description="删除后，该命令适配器及关联运行包将停止用于新巡检。检查项、TextFSM 模板和其他命令适配器不受影响，历史执行结果与审计记录保留。"
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

            {selectedChange.asset_kind === 'check_item' && ['draft', 'pending_approval'].includes(selectedChange.status) && (
              <section className="check-item-catalog-section">
                <Typography.Title level={5}>发布后巡检会变什么</Typography.Title>
                <CheckItemReleasePreview
                  preview={releasePreview}
                  loading={releasePreviewQuery.isLoading || releasePreviewQuery.isFetching}
                  error={releasePreviewQuery.isError ? queryErrorLabel(releasePreviewQuery.error) : undefined}
                />
              </section>
            )}

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
                  {selectedImplementationCandidate.adapter_spec?.schema_version === 'command-adapter/v2' && (
                    <Descriptions.Item label="Adapter Projection">
                      <pre>{JSON.stringify(selectedImplementationCandidate.adapter_spec.outputs || [], null, 2)}</pre>
                    </Descriptions.Item>
                  )}
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
                  {selectedChange.asset_kind === 'check_item'
                    ? '发布'
                    : selectedChange.status === 'draft' ? '提交发布审批' : '独立发布决定'}
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
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => {
                      void impactQuery.refetch()
                      void releasePreviewQuery.refetch()
                    }}
                  >
                    重新计算影响
                  </Button>
                  {selectedChange.status === 'draft' && (
                    <Popconfirm
                      title="撤销这份草稿？"
                      description="草稿不会发布，已发布的版本不受影响。"
                      okText="撤销草稿"
                      cancelText="保留"
                      onConfirm={() => discardMutation.mutate(selectedChange.id)}
                    >
                      <Button loading={discardMutation.isLoading}>撤销草稿</Button>
                    </Popconfirm>
                  )}
                  {selectedChange.status === 'draft' && selectedChange.asset_kind === 'check_item' ? (
                    <>
                      <Button
                        loading={submitMutation.isLoading}
                        disabled={publishMutation.isLoading}
                        onClick={() => submitMutation.mutate({
                          changeId: selectedChange.id,
                          impactDigest: impact.impact_digest,
                        })}
                      >
                        只提交，暂不发布
                      </Button>
                      <Button
                        type="primary"
                        className="rule-asset-import-button"
                        icon={<CheckOutlined />}
                        disabled={!canPublish}
                        loading={publishMutation.isLoading}
                        onClick={() => publishMutation.mutate({
                          changeId: selectedChange.id,
                          impactDigest: impact.impact_digest,
                        })}
                      >
                        发布并切换
                      </Button>
                    </>
                  ) : selectedChange.status === 'draft' ? (
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
                        disabled={!canUseRules}
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
                        {publicationOptions?.requires_rollout || selectedChange.asset_kind === 'check_item'
                          ? '批准并切换'
                          : '批准发布'}
                      </Button>
                    </>
                  )}
                </div>
                {selectedChange.status === 'pending_approval' && !canUseRules && (
                  <Typography.Text type="secondary">当前账号没有规则治理应用使用权。</Typography.Text>
                )}
                {selectedChange.status === 'pending_approval' && canUseRules && (
                  // Nothing asks for a second account; the wording used to suggest it did.
                  <Typography.Text type="secondary">
                    提交人自己也可以批准，不需要换账号；批准人会记入审计记录。
                  </Typography.Text>
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
