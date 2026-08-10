import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Grid,
  Input,
  List,
  Pagination,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowRightOutlined,
  EyeOutlined,
  LinkOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useQuery } from 'react-query'
import { useNavigate } from 'react-router-dom'
import {
  parserApi,
  type RuleAssetCheckItemComponentStatuses,
  type RuleAssetCheckItemProductionUnit,
  type RuleAssetComponentStatus,
  type RuleAssetGovernanceBucket,
  type RuleAssetGovernanceStatus,
  type RuleAssetProductionUnit,
} from '../../../services/api'
import { CommandAssetDetail } from '../../devices/OfflineCommandAssetUnits'
import RuleAssetSourceJobsWorkspace from './RuleAssetSourceJobsWorkspace'
import RuleAssetGenerationStep from './RuleAssetGenerationStep'
import RuleAssetAutomationControl from './RuleAssetAutomationControl'
import AssessmentPolicyWorkspace from './AssessmentPolicyWorkspace'
import CheckItemCatalogWorkspace from './CheckItemCatalogWorkspace'
import CommandCatalogBindingDrawer from './CommandCatalogBindingDrawer'

type WorkbenchView = 'commands' | 'sources' | 'catalog' | 'policies'
type BucketFilter = RuleAssetGovernanceBucket | 'all'
type DetailTab = 'flow' | 'parser' | 'generation' | 'evidence' | 'attempts' | 'assets'

interface RuleAssetProductionWorkbenchProps {
  view: WorkbenchView
  onViewChange: (view: WorkbenchView) => void
  embedded?: boolean
  initialSearch?: string
  focusAction?: string
  focusOfflineJobId?: string
  focusRuleSeedJobId?: string
  focusBundleId?: string
  datasetIds?: string[]
}

const bucketOptions: Array<{ key: BucketFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'action_required', label: '待处理' },
  { key: 'in_progress', label: '进行中' },
  { key: 'awaiting_approval', label: '待审批' },
  { key: 'published', label: '已发布' },
  { key: 'not_applicable', label: '无需规则' },
  { key: 'exception', label: '异常' },
]

const governanceStatusLabel: Record<RuleAssetGovernanceStatus, string> = {
  preparing: '信息收集中',
  ready_to_generate: '待生成',
  generating: '生成验证中',
  engineering_review: '待工程复核',
  release_approval: '待发布审批',
  published: '已发布',
  not_applicable: '无需规则资产',
  blocked: '已阻断',
  failed: '生成失败',
}

const componentStatusLabel: Record<RuleAssetComponentStatus, string> = {
  not_started: '未开始',
  ready_to_generate: '待生成',
  generating: '生成中',
  engineering_review: '待复核',
  published: '已发布',
  not_applicable: '不适用',
  blocked: '阻断',
  failed: '失败',
}

const assetModeLabel: Record<string, string> = {
  full_chain: '完整规则链',
  observation_only: '仅状态观测',
  change_only: '仅变化检测',
  raw_hash: '原始哈希比较',
  ignored: '忽略',
}

const componentLabels = [
  ['identity', '身份策略'],
  ['parser', 'Parser'],
  ['contract', 'Contract'],
  ['current_state', '当前状态'],
  ['policy', '变化策略'],
  ['event', '事件规则'],
  ['comparison', '文本比较'],
  ['runtime_bundle', '运行包'],
] as const

const detailTabForAction = (
  unit: RuleAssetProductionUnit,
  focusAction?: string,
): DetailTab => {
  const code = unit.next_action?.code || ''
  if (focusAction === 'rule-generation' || code === 'generate_rule_asset_bundle') return 'generation'
  if (
    focusAction === 'release'
    || focusAction === 'bundle-review'
    || ['review_bundle', 'release_bundle'].includes(code)
  ) return 'generation'
  if (
    focusAction === 'parser'
    || ['open_parser_job', 'inspect_parser_validation'].includes(code)
  ) return 'parser'
  return 'flow'
}

const scopeLabel = (unit: RuleAssetProductionUnit) => {
  const models = unit.models.filter(Boolean)
  if (models.length === 1 && models[0].toLowerCase() === unit.profile.toLowerCase()) {
    return `${unit.vendor} · ${models[0]}`
  }
  if (models.length) {
    return `${unit.vendor} · ${unit.profile} · ${models.join(' / ')}`
  }
  return `${unit.vendor} · ${unit.profile}`
}

const formatUpdatedAt = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const nextActionLabel = (unit: RuleAssetProductionUnit) => {
  if (unit.next_action?.code === 'open_parser_job') {
    return unit.next_action.label || '配置并生成 Parser'
  }
  return ({
    generate_rule_asset_bundle: '生成规则',
    review_bundle: '工程复核',
    release_bundle: '审批发布',
    assemble_runtime_bundle: '装配运行包',
    inspect_runtime_bundle: '处理运行包缺口',
    repair_or_backfill: '修复回填',
    inspect_parser_validation: '查看验证',
    confirm_identity: '修正身份',
    inspect_import: '检查样本',
  }[unit.next_action?.code || ''] || unit.next_action?.label || '继续处理')
}

const ComponentChain: React.FC<{
  unit: { component_statuses: RuleAssetCheckItemComponentStatuses }
}> = ({ unit }) => (
  <div className="rule-asset-chain">
    {componentLabels.filter(
      ([key]) => unit.component_statuses[key] !== 'not_applicable',
    ).map(([key, label]) => {
      const status = unit.component_statuses[key]
      return (
        <div className="rule-asset-chain__item" key={key}>
          <span>{label}</span>
          <Tag className={`rule-asset-component-status is-${status}`}>
            {componentStatusLabel[status]}
          </Tag>
        </div>
      )
    })}
  </div>
)

const RuleAssetProductionWorkbench: React.FC<RuleAssetProductionWorkbenchProps> = ({
  view,
  onViewChange,
  embedded = false,
  initialSearch = '',
  focusAction,
  focusOfflineJobId,
  focusRuleSeedJobId,
  focusBundleId,
  datasetIds = [],
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState(initialSearch)
  const [search, setSearch] = useState(initialSearch)
  const [bucket, setBucket] = useState<BucketFilter>('all')
  const [governanceStatus, setGovernanceStatus] = useState<RuleAssetGovernanceStatus>()
  const [assetMode, setAssetMode] = useState<string>()
  const [selectedUnitId, setSelectedUnitId] = useState<string>()
  const [expandedRowKeys, setExpandedRowKeys] = useState<React.Key[]>([])
  const [detailInitialTab, setDetailInitialTab] = useState<DetailTab>('flow')
  const [evidencePage, setEvidencePage] = useState(1)
  const [evidencePageSize, setEvidencePageSize] = useState(25)
  const [bindingUnit, setBindingUnit] = useState<
    RuleAssetCheckItemProductionUnit['implementations'][number]
  >()
  const [catalogFocusChangeId, setCatalogFocusChangeId] = useState<string>()
  const handledFocusRef = useRef('')

  useEffect(() => {
    setSearchDraft(initialSearch)
    setSearch(initialSearch)
    setPage(1)
  }, [initialSearch])

  useEffect(() => {
    if (view !== 'commands') setSelectedUnitId(undefined)
  }, [view])

  const listQuery = useQuery(
    [
      'rule-asset-production-units',
      page,
      pageSize,
      search,
      bucket,
      governanceStatus,
      assetMode,
      focusOfflineJobId,
    ],
    () => parserApi.assetProductionCheckItems({
      page,
      page_size: pageSize,
      search: search || undefined,
      bucket: bucket === 'all' ? undefined : bucket,
      governance_status: governanceStatus,
      asset_mode: assetMode,
      offline_job_id: focusOfflineJobId,
    }),
    {
      enabled: view === 'commands',
      keepPreviousData: true,
      refetchInterval: (data) => (data?.summary.in_progress ? 5000 : false),
    },
  )
  const detailQuery = useQuery(
    ['rule-asset-production-unit', selectedUnitId, evidencePage, evidencePageSize],
    () => parserApi.assetProductionUnit(selectedUnitId || '', {
      evidence_page: evidencePage,
      evidence_page_size: evidencePageSize,
    }),
    { enabled: Boolean(selectedUnitId), keepPreviousData: true },
  )
  const detailDatasetIds = useMemo(() => {
    if (datasetIds.length) return datasetIds
    return Array.from(new Set(
      (detailQuery.data?.evidence || [])
        .map((item) => item.period?.dataset_id)
        .filter((item): item is string => Boolean(item)),
    ))
  }, [datasetIds, detailQuery.data?.evidence])

  const data = listQuery.data
  const summary = data?.summary
  const summaryMetrics = useMemo(() => ([
    { key: 'action_required' as const, label: '需要处理', value: summary?.action_required || 0 },
    { key: 'in_progress' as const, label: '生成验证中', value: summary?.in_progress || 0 },
    { key: 'awaiting_approval' as const, label: '待审批', value: summary?.awaiting_approval || 0 },
    { key: 'published' as const, label: '已发布', value: summary?.published || 0 },
    { key: 'not_applicable' as const, label: '无需规则', value: summary?.not_applicable || 0 },
    { key: 'exception' as const, label: '异常', value: summary?.exception || 0 },
  ]), [summary])

  const openDetail = (
    unitId: string,
    tab: DetailTab = 'flow',
  ) => {
    setEvidencePage(1)
    setDetailInitialTab(tab)
    setSelectedUnitId(unitId)
  }
  const openNextAction = (unit: RuleAssetProductionUnit) => {
    const tab = detailTabForAction(unit)
    if (unit.next_action?.target_url) {
      // The backend deep link carries the authoritative source job, datasets,
      // command and seed/bundle IDs. Keep that context when opening an inline
      // production step so the user never has to reselect its evidence.
      navigate(unit.next_action.target_url)
    }
    if (tab !== 'flow') {
      openDetail(unit.id, tab)
      return
    }
  }
  const openCatalogBinding = (
    unit: RuleAssetCheckItemProductionUnit['implementations'][number],
  ) => setBindingUnit(unit)
  const changeBucket = (nextBucket: BucketFilter) => {
    setPage(1)
    setBucket(nextBucket)
  }

  useEffect(() => {
    if (embedded || view !== 'commands' || !data?.items?.length) return
    const focusKey = [
      focusAction,
      focusOfflineJobId,
      focusRuleSeedJobId,
      focusBundleId,
      initialSearch,
    ].filter(Boolean).join(':')
    if (!focusKey || handledFocusRef.current === focusKey) return
    const matchesFocusAction = (unit: RuleAssetProductionUnit) => {
      const code = unit.next_action?.code || ''
      if (focusAction === 'parser') {
        return ['open_parser_job', 'inspect_parser_validation'].includes(code)
      }
      if (focusAction === 'rule-generation') {
        return code === 'generate_rule_asset_bundle'
      }
      if (focusAction === 'release' || focusAction === 'bundle-review') {
        return ['review_bundle', 'release_bundle'].includes(code)
      }
      return true
    }
    const focusedImplementation = (item: RuleAssetCheckItemProductionUnit) => (
      item.implementations.find((unit) => (
        (focusRuleSeedJobId && unit.rule_seed_job_id === focusRuleSeedJobId)
        || (focusBundleId && unit.bundle_id === focusBundleId)
        || (
          !focusRuleSeedJobId
          && !focusBundleId
          && focusOfflineJobId
          && unit.offline_job_id === focusOfflineJobId
          && (!initialSearch || unit.canonical_command === initialSearch)
          && matchesFocusAction(unit)
        )
      ))
    )
    const exactGroup = data.items.find(focusedImplementation)
    const hasScopedFocus = Boolean(
      focusOfflineJobId || focusRuleSeedJobId || focusBundleId,
    )
    const fallbackGroup = hasScopedFocus
      ? undefined
      : data.items.find((item) => (
        !initialSearch
        || item.check_item_id === initialSearch
        || item.implementations.some(
          (unit) => unit.canonical_command === initialSearch,
        )
      ))
    const matchedGroup = exactGroup || fallbackGroup
    const matched = matchedGroup
      ? focusedImplementation(matchedGroup) || matchedGroup.implementations[0]
      : undefined
    if (!matched) return
    handledFocusRef.current = focusKey
    setExpandedRowKeys([matchedGroup!.id])
    openDetail(matched.id, detailTabForAction(matched, focusAction))
  }, [
    data?.items,
    focusAction,
    focusBundleId,
    focusOfflineJobId,
    focusRuleSeedJobId,
    initialSearch,
    embedded,
    view,
  ])

  const openCheckItemNextAction = (item: RuleAssetCheckItemProductionUnit) => {
    if (
      item.next_action?.target_url
      && ['assemble_runtime_bundle', 'inspect_runtime_bundle'].includes(
        item.next_action.code || '',
      )
    ) {
      navigate(item.next_action.target_url)
      return
    }
    const implementation = item.implementations.find(
      (unit) => unit.next_action?.code === item.next_action?.code,
    ) || item.implementations[0]
    if (implementation) openNextAction(implementation)
  }

  const implementationColumns: ColumnsType<
    RuleAssetCheckItemProductionUnit['implementations'][number]
  > = [
    {
      title: '采集实现',
      key: 'implementation',
      width: 260,
      render: (_, unit) => (
        <Space direction="vertical" size={1}>
          <Typography.Text strong>
            {unit.implementation_name || unit.canonical_command}
          </Typography.Text>
          <Typography.Text code>{unit.canonical_command}</Typography.Text>
          <Typography.Text type="secondary">{scopeLabel(unit)}</Typography.Text>
          <Typography.Text type="secondary">
            来源：{unit.source_job.name}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '样本证据',
      key: 'evidence',
      width: 150,
      render: (_, unit) => (
        <Space direction="vertical" size={1}>
          <Typography.Text>{unit.sample_count} 份输出</Typography.Text>
          <Typography.Text type="secondary">
            {unit.device_count} 台设备 / {unit.output_signature_count} 种结构
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '资产链',
      key: 'components',
      width: 220,
      render: (_, unit) => <ComponentChain unit={unit} />,
    },
    {
      title: '治理状态',
      key: 'status',
      width: 150,
      render: (_, unit) => (
        <Space direction="vertical" size={2}>
          <Tag className={`rule-asset-governance-status is-${unit.governance_status}`}>
            {governanceStatusLabel[unit.governance_status]}
          </Tag>
          <Typography.Text type="secondary">{unit.current_stage_label}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_, unit) => (
        <Space size={2} wrap>
          <Button type="text" icon={<EyeOutlined />} onClick={() => openDetail(unit.id)}>
            审查证据
          </Button>
          {unit.mapping_status === 'unclassified' && (
            <Button
              type="text"
              icon={<LinkOutlined />}
              onClick={() => openCatalogBinding(unit)}
            >
              绑定检查项
            </Button>
          )}
          {unit.next_action?.target_url && (
            <Button
              type="text"
              className="rule-asset-next-action"
              icon={<ArrowRightOutlined />}
              onClick={() => openNextAction(unit)}
            >
              {nextActionLabel(unit)}
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const columns: ColumnsType<RuleAssetCheckItemProductionUnit> = [
    {
      title: '巡检检查项',
      key: 'identity',
      width: 280,
      fixed: 'left',
      render: (_, item) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{item.display_name || item.name}</Typography.Text>
          <Typography.Text code>
            {item.check_item_id || (item.category === 'not_applicable' ? 'no-rule-required' : 'unclassified')}
          </Typography.Text>
          <Typography.Text type="secondary">
            {item.display_description || item.description}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '兼容实现',
      key: 'implementations',
      width: 170,
      render: (_, item) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>{item.implementation_count} 个采集实现</Typography.Text>
          <Typography.Text type="secondary">
            {item.vendors.join(' / ') || '尚未归类'} / {item.command_count} 条命令
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '规则资产链',
      key: 'components',
      width: 220,
      render: (_, item) => <ComponentChain unit={item} />,
    },
    {
      title: '当前状态',
      key: 'status',
      width: 160,
      render: (_, item) => (
        <Space direction="vertical" size={3}>
          <Tag className={`rule-asset-governance-status is-${item.governance_status}`}>
            {governanceStatusLabel[item.governance_status]}
          </Tag>
          <Typography.Text type="secondary">
            已发布 {item.coverage.published}/{item.coverage.total}
          </Typography.Text>
          {item.blocker_count > 0 && (
            <Typography.Text type="danger">{item.blocker_count} 个阻断项</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 108,
      responsive: ['xxl'],
      render: (value) => <Typography.Text type="secondary">{formatUpdatedAt(value)}</Typography.Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      fixed: 'right',
      render: (_, item) => (
        <Space size={2} wrap>
          <Tooltip title="展开厂商、型号和命令实现">
            <Button
              type="text"
              icon={<EyeOutlined />}
              onClick={() => setExpandedRowKeys((current) => (
                current.includes(item.id)
                  ? current.filter((key) => key !== item.id)
                  : [...current, item.id]
              ))}
            >
              查看实现
            </Button>
          </Tooltip>
          {!item.check_item_id && (
            <Tooltip title="先展开命令，再逐条选择已审批检查项">
              <Button
                type="text"
                icon={<LinkOutlined />}
                onClick={() => {
                  if (item.implementations.length === 1) {
                    openCatalogBinding(item.implementations[0])
                    return
                  }
                  setExpandedRowKeys((current) => (
                    current.includes(item.id) ? current : [...current, item.id]
                  ))
                }}
              >
                {item.implementations.length === 1 ? '绑定检查项' : '逐条绑定'}
              </Button>
            </Tooltip>
          )}
          {item.next_action?.target_url && (
            <Tooltip title={item.next_action.label}>
              <Button
                type="text"
                className="rule-asset-next-action"
                icon={<ArrowRightOutlined />}
                onClick={() => openCheckItemNextAction(item)}
              >
                继续治理
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="rule-asset-workbench">
      {!embedded && <div className="rule-asset-workbench__toolbar">
        <div className="rule-asset-view-switch" role="tablist" aria-label="规则资产生产视图">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'commands'}
            className={view === 'commands' ? 'is-active' : ''}
            onClick={() => onViewChange('commands')}
          >
            检查项资产
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'sources'}
            className={view === 'sources' ? 'is-active' : ''}
            onClick={() => onViewChange('sources')}
          >
            样本批次
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'catalog'}
            className={view === 'catalog' ? 'is-active' : ''}
            onClick={() => onViewChange('catalog')}
          >
            检查项目录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'policies'}
            className={view === 'policies' ? 'is-active' : ''}
            onClick={() => onViewChange('policies')}
          >
            判断策略
          </button>
        </div>
        {(view === 'commands' || view === 'sources') && (
          <Button
            icon={<UploadOutlined />}
            className="rule-asset-import-button"
            onClick={() => onViewChange('sources')}
          >
            导入规则样本
          </Button>
        )}
      </div>}

      {view === 'policies' ? (
        <AssessmentPolicyWorkspace />
      ) : view === 'catalog' ? (
        <CheckItemCatalogWorkspace focusChangeId={catalogFocusChangeId} />
      ) : view === 'sources' ? (
        <RuleAssetSourceJobsWorkspace />
      ) : (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          {!embedded && (
            <RuleAssetAutomationControl onChanged={() => void listQuery.refetch()} />
          )}
          <div className="rule-asset-metrics" aria-label="规则资产治理摘要">
            {summaryMetrics.map((metric) => (
              <button
                type="button"
                key={metric.key}
                className={bucket === metric.key ? `is-active is-${metric.key}` : `is-${metric.key}`}
                onClick={() => changeBucket(metric.key)}
              >
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </button>
            ))}
          </div>

          <div className="rule-asset-bucket-tabs" role="tablist" aria-label="治理状态筛选">
            {bucketOptions.map((option) => (
              <button
                type="button"
                role="tab"
                aria-selected={bucket === option.key}
                key={option.key}
                className={bucket === option.key ? 'is-active' : ''}
                onClick={() => changeBucket(option.key)}
              >
                {option.label}
                {option.key !== 'all' && (
                  <span>{summary?.by_bucket[option.key] || 0}</span>
                )}
              </button>
            ))}
          </div>

          <div className="rule-asset-filters">
            <Input.Search
              allowClear
              value={searchDraft}
              placeholder="搜索检查项、命令、厂商、型号或样本批次"
              onChange={(event) => setSearchDraft(event.target.value)}
              onSearch={(value) => {
                setPage(1)
                setSearch(value.trim())
              }}
            />
            <Select
              aria-label="治理状态"
              allowClear
              value={governanceStatus}
              placeholder="治理状态"
              options={Object.entries(governanceStatusLabel).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={(value) => {
                setPage(1)
                setGovernanceStatus(value)
              }}
            />
            <Select
              aria-label="资产模式"
              allowClear
              value={assetMode}
              placeholder="资产模式"
              options={Object.entries(assetModeLabel).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={(value) => {
                setPage(1)
                setAssetMode(value)
              }}
            />
            <Button
              icon={<ReloadOutlined />}
              aria-label="刷新检查项资产"
              loading={listQuery.isFetching}
              onClick={() => listQuery.refetch()}
            />
          </div>

          {listQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="检查项资产加载失败"
              description={(listQuery.error as Error)?.message || '请检查服务状态后重试。'}
              action={<Button onClick={() => listQuery.refetch()}>重试</Button>}
            />
          )}

          {isMobile ? (
            <div className="rule-asset-mobile-list">
              <List
                loading={listQuery.isLoading || listQuery.isFetching}
                dataSource={data?.items || []}
                locale={{ emptyText: <Empty description="暂无符合条件的检查项资产" /> }}
                renderItem={(item) => (
                  <List.Item>
                    <div className="rule-asset-mobile-item">
                      <div className="rule-asset-mobile-item__header">
                        <Space direction="vertical" size={0}>
                          <Typography.Text strong>{item.display_name || item.name}</Typography.Text>
                          <Typography.Text code>
                            {item.check_item_id || (item.category === 'not_applicable' ? 'no-rule-required' : 'unclassified')}
                          </Typography.Text>
                        </Space>
                        <Tag className={`rule-asset-governance-status is-${item.governance_status}`}>
                          {governanceStatusLabel[item.governance_status]}
                        </Tag>
                      </div>
                      <Typography.Text type="secondary">
                        {item.display_description || item.description}
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        {item.implementation_count} 个实现 / {item.command_count} 条命令 / {item.vendors.join(' / ') || '待归类'}
                      </Typography.Text>
                      <ComponentChain unit={item} />
                      <div className="rule-asset-mobile-item__actions">
                        <Button
                          icon={<EyeOutlined />}
                          onClick={() => setExpandedRowKeys((current) => (
                            current.includes(item.id)
                              ? current.filter((key) => key !== item.id)
                              : [...current, item.id]
                          ))}
                        >
                          查看实现
                        </Button>
                        {!item.check_item_id && (
                          <Button
                            icon={<LinkOutlined />}
                            onClick={() => {
                              if (item.implementations.length === 1) {
                                openCatalogBinding(item.implementations[0])
                                return
                              }
                              setExpandedRowKeys((current) => (
                                current.includes(item.id) ? current : [...current, item.id]
                              ))
                            }}
                          >
                            {item.implementations.length === 1 ? '绑定检查项' : '逐条绑定'}
                          </Button>
                        )}
                        {item.next_action?.target_url && (
                          <Button
                            className="rule-asset-next-action"
                            icon={<ArrowRightOutlined />}
                            onClick={() => openCheckItemNextAction(item)}
                          >
                            继续治理
                          </Button>
                        )}
                      </div>
                      {expandedRowKeys.includes(item.id) && (
                        <List
                          size="small"
                          dataSource={item.implementations}
                          renderItem={(unit) => (
                            <List.Item
                              actions={[
                                <Button
                                  key="evidence"
                                  type="link"
                                  onClick={() => openDetail(unit.id)}
                                >
                                  审查证据
                                </Button>,
                                ...(unit.mapping_status === 'unclassified'
                                  ? [
                                    <Button
                                      key="catalog-binding"
                                      type="link"
                                      onClick={() => openCatalogBinding(unit)}
                                    >
                                      绑定检查项
                                    </Button>,
                                  ]
                                  : []),
                                ...(unit.next_action?.target_url
                                  ? [
                                    <Button
                                      key="next"
                                      type="link"
                                      onClick={() => openNextAction(unit)}
                                    >
                                      {nextActionLabel(unit)}
                                    </Button>,
                                  ]
                                  : []),
                              ]}
                            >
                              <List.Item.Meta
                                title={unit.canonical_command}
                                description={(
                                  <Space direction="vertical" size={0}>
                                    <Typography.Text type="secondary">
                                      {scopeLabel(unit)} / {governanceStatusLabel[unit.governance_status]}
                                    </Typography.Text>
                                    <Typography.Text type="secondary">
                                      来源：{unit.source_job.name}
                                    </Typography.Text>
                                  </Space>
                                )}
                              />
                            </List.Item>
                          )}
                        />
                      )}
                    </div>
                  </List.Item>
                )}
              />
              {(data?.total || 0) > 0 && (
                <Pagination
                  current={page}
                  pageSize={pageSize}
                  total={data?.total || 0}
                  showSizeChanger
                  onChange={(nextPage, nextSize) => {
                    setPage(nextSize === pageSize ? nextPage : 1)
                    setPageSize(nextSize)
                  }}
                />
              )}
            </div>
          ) : !listQuery.isLoading && !listQuery.isFetching && (data?.items.length || 0) === 0 ? (
            <Empty description="暂无符合条件的检查项资产" />
          ) : (
            <Table<RuleAssetCheckItemProductionUnit>
              rowKey="id"
              className="rule-asset-table"
              loading={listQuery.isLoading || listQuery.isFetching}
              dataSource={data?.items || []}
              columns={columns}
              expandable={{
                columnTitle: '实现',
                expandedRowKeys,
                onExpandedRowsChange: (keys) => setExpandedRowKeys([...keys]),
                expandedRowRender: (item) => (
                  <Table
                    rowKey={(unit) => `${unit.id}:${unit.implementation_version_id || 'unclassified'}`}
                    size="small"
                    columns={implementationColumns}
                    dataSource={item.implementations}
                    pagination={false}
                    scroll={{ x: 960 }}
                  />
                ),
              }}
              scroll={{ x: screens.xxl ? 1120 : 900 }}
              pagination={{
                current: page,
                pageSize,
                total: data?.total || 0,
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 个检查项`,
                onChange: (nextPage, nextSize) => {
                  setPage(nextSize === pageSize ? nextPage : 1)
                  setPageSize(nextSize)
                },
              }}
              locale={{ emptyText: <Empty description="暂无符合条件的检查项资产" /> }}
            />
          )}
        </Space>
      )}

      <CommandAssetDetail
        open={Boolean(selectedUnitId)}
        detail={detailQuery.data}
        evidencePage={evidencePage}
        evidencePageSize={evidencePageSize}
        loading={detailQuery.isLoading}
        initialTab={detailInitialTab}
        generationContent={(
          <RuleAssetGenerationStep
            seedJobId={detailQuery.data?.rule_seed_job_id || focusRuleSeedJobId}
            bundleId={detailQuery.data?.bundle_id || focusBundleId}
            datasetIds={detailDatasetIds}
            command={detailQuery.data?.canonical_command || initialSearch}
            offlineJobId={detailQuery.data?.offline_job_id || focusOfflineJobId}
            initialAction={
              detailQuery.data?.next_action?.code === 'release_bundle'
              || focusAction === 'release'
                ? 'release'
                : undefined
            }
            onChanged={() => {
              void listQuery.refetch()
              void detailQuery.refetch()
            }}
          />
        )}
        onEvidencePageChange={(nextPage, nextSize) => {
          setEvidencePage(nextSize === evidencePageSize ? nextPage : 1)
          setEvidencePageSize(nextSize)
        }}
        onClose={() => {
          setEvidencePage(1)
          setSelectedUnitId(undefined)
        }}
      />
      <CommandCatalogBindingDrawer
        open={Boolean(bindingUnit)}
        unit={bindingUnit}
        onClose={() => setBindingUnit(undefined)}
        onCreated={(change) => {
          setBindingUnit(undefined)
          setCatalogFocusChangeId(change.id)
          onViewChange('catalog')
        }}
      />
    </div>
  )
}

export default RuleAssetProductionWorkbench
