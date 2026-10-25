import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Empty, Grid, Input, InputNumber, List, Modal,
  Pagination, Progress, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import {
  ArrowRightOutlined, CheckCircleOutlined, CheckOutlined, DeploymentUnitOutlined,
  HistoryOutlined, PlayCircleOutlined, ReloadOutlined, WarningOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useSearchParams } from 'react-router-dom'

import {
  parserApi,
  type CheckItemRuntimeBundleVersion,
  type CheckItemRuntimeBundleHealthItem,
  type CheckItemRuntimeBundleHealthStatus,
  type ParserBackfillJob,
  type RuntimeBundleAssemblyPayload,
} from '@netops/services/api'
import RolloutCampaignWorkspace from './RolloutCampaignWorkspace'
import RuleGapsWorkspace from './RuleGapsWorkspace'

type CoverageView = 'coverage' | 'backfill' | 'rollout' | 'gaps'

type RuntimeBundleAssemblySelection = Omit<
  RuntimeBundleAssemblyPayload,
  'check_item_version_id' | 'implementation_version_id'
>

const backfillStatusMeta: Record<string, { label: string; className: string }> = {
  draft: { label: '待试运行', className: 'is-neutral' },
  running: { label: '执行中', className: 'is-running' },
  validation_failed: { label: '试运行未通过', className: 'is-failed' },
  pending_approval: { label: '待批准写入', className: 'is-review' },
  committed: { label: '已完成', className: 'is-published' },
  completed: { label: '已完成', className: 'is-published' },
  failed: { label: '执行失败', className: 'is-failed' },
}

const assetTypeLabels: Record<string, string> = {
  parser_template: 'Parser',
  current_state_rule: 'CurrentStateRule',
  change_policy: 'ChangePolicy',
  event_rule: 'EventRule',
  comparison_profile: 'TextComparisonProfile',
  identity_policy: 'IdentityPolicy',
}

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const ratioPercent = (value: unknown) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.round(Math.max(0, Math.min(1, numeric)) * 100)
}

const backfillFilterValues = (row: ParserBackfillJob, key: string): string[] => {
  const value = row.filters_json?.[key]
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

const backfillScopeLabel = (row: ParserBackfillJob) => {
  const datasets = backfillFilterValues(row, 'dataset_ids')
  if (datasets.length > 0) return `${datasets.length} 个数据集：${datasets.join('、')}`
  const runs = backfillFilterValues(row, 'run_ids')
  if (runs.length > 0) return `${runs.length} 个 Run：${runs.join('、')}`
  const devices = backfillFilterValues(row, 'device_ids')
  if (devices.length > 0) return `${devices.length} 台设备`
  return '全部符合条件的历史数据'
}

const CoverageBackfillWorkspace: React.FC = () => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const navigate = useRuleTaskNavigate()
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const view: CoverageView = searchParams.get('subtab') === 'rule-gaps'
    ? 'gaps'
    : searchParams.get('action') === 'rollout'
      ? 'rollout'
      : searchParams.get('action') === 'backfill' || searchParams.get('open_backfill') === '1'
        ? 'backfill'
        : 'coverage'
  const changeView = (nextView: CoverageView) => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', 'runtime')
    next.set('subtab', nextView === 'gaps' ? 'rule-gaps' : 'coverage-backfill')
    next.delete('action')
    next.delete('open_backfill')
    if (nextView === 'backfill') next.set('action', 'backfill')
    if (nextView === 'rollout') next.set('action', 'rollout')
    navigate(businessHref(`/skills/parser?${next.toString()}`), { replace: true })
  }
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [runtimePage, setRuntimePage] = useState(1)
  const [runtimePageSize, setRuntimePageSize] = useState(25)
  const [runtimeStatus, setRuntimeStatus] = useState<CheckItemRuntimeBundleHealthStatus>()
  const targetCheckItemVersionId = searchParams.get('check_item_version_id') || ''
  const targetImplementationVersionId = searchParams.get('implementation_version_id') || ''
  const initialRuntimeSearch = searchParams.get('search') || ''
  const [runtimeSearchDraft, setRuntimeSearchDraft] = useState(initialRuntimeSearch)
  const [runtimeSearch, setRuntimeSearch] = useState(initialRuntimeSearch)
  const [runtimeDeepLinkHandled, setRuntimeDeepLinkHandled] = useState(false)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<string>()
  const [createOpen, setCreateOpen] = useState(searchParams.get('open_backfill') === '1')
  const [templateSearch, setTemplateSearch] = useState('')
  const [templateId, setTemplateId] = useState<string | undefined>(
    searchParams.get('template_id') || undefined,
  )
  const [deviceIds, setDeviceIds] = useState<string[]>([])
  const [datasetIds, setDatasetIds] = useState<string[]>(
    (searchParams.get('dataset_ids') || '').split(',').map((item) => item.trim()).filter(Boolean),
  )
  const [runModes, setRunModes] = useState<string[]>(
    (searchParams.get('run_modes') || 'production').split(',').map((item) => item.trim()).filter(Boolean),
  )
  const [observedFrom, setObservedFrom] = useState('')
  const [observedTo, setObservedTo] = useState('')
  const [snapshotLimit, setSnapshotLimit] = useState(1000)
  const [commitJob, setCommitJob] = useState<ParserBackfillJob>()
  const [commitReason, setCommitReason] = useState('')
  const [assemblyTarget, setAssemblyTarget] = useState<CheckItemRuntimeBundleHealthItem>()
  const [assemblySelection, setAssemblySelection] = useState<RuntimeBundleAssemblySelection>({})
  const [draftBundle, setDraftBundle] = useState<CheckItemRuntimeBundleVersion>()
  const [activationReason, setActivationReason] = useState('')

  const coverageQuery = useQuery(
    'product-parser-coverage',
    () => parserApi.coverage(),
  )
  const runtimeBundleQuery = useQuery(
    [
      'product-check-item-runtime-bundles',
      runtimePage,
      runtimePageSize,
      runtimeStatus,
      runtimeSearch,
    ],
    () => parserApi.checkItemRuntimeBundleHealth({
      page: runtimePage,
      page_size: runtimePageSize,
      status: runtimeStatus,
      search: runtimeSearch || undefined,
    }),
    { keepPreviousData: true, enabled: view === 'coverage' },
  )
  const assemblyQuery = useQuery(
    [
      'check-item-runtime-bundle-assembly',
      assemblyTarget?.check_item_version_id,
      assemblyTarget?.implementation_version_id,
      JSON.stringify(assemblySelection),
    ],
    () => parserApi.previewCheckItemRuntimeBundle({
      check_item_version_id: assemblyTarget!.check_item_version_id,
      implementation_version_id: assemblyTarget!.implementation_version_id,
      ...assemblySelection,
    }),
    {
      enabled: Boolean(assemblyTarget),
      retry: false,
      keepPreviousData: true,
    },
  )
  const backfillsQuery = useQuery(
    ['product-parser-backfills', page, pageSize, search, status],
    () => parserApi.backfills({
      limit: pageSize,
      offset: (page - 1) * pageSize,
      search: search || undefined,
      status,
    }),
    { keepPreviousData: true, enabled: view === 'backfill' },
  )
  const activeTemplatesQuery = useQuery(
    ['product-active-template-options', templateSearch],
    () => parserApi.templates({
      status: 'active',
      search: templateSearch || undefined,
      limit: 50,
      offset: 0,
    }),
    { enabled: createOpen },
  )

  const refresh = () => {
    queryClient.invalidateQueries('product-parser-coverage')
    queryClient.invalidateQueries('product-check-item-runtime-bundles')
    queryClient.invalidateQueries('product-parser-backfills')
  }

  const backfillMutation = useMutation(
    async (request: {
      action: 'create' | 'dry-run' | 'commit'
      id: string
      reason?: string
      filters?: Record<string, unknown>
    }) => {
      if (request.action === 'create') {
        const job = await parserApi.createBackfill({
          template_id: request.id,
          filters: request.filters,
        })
        if (['draft', 'validation_failed'].includes(job.status)) {
          return parserApi.dryRunBackfill(job.id)
        }
        return job
      }
      if (request.action === 'dry-run') return parserApi.dryRunBackfill(request.id)
      return parserApi.commitBackfill(request.id, { reason: request.reason || '' })
    },
    {
      onSuccess: (result, request) => {
        message.success(
          request.action === 'create'
            ? result.status === 'pending_approval'
              ? '试运行通过，请检查影响后批准写入'
              : '试运行已完成，请在任务列表检查结果'
            : request.action === 'dry-run'
              ? '试运行已完成'
              : '历史回填已批准写入',
        )
        if (request.action === 'create') {
          setCreateOpen(false)
          setPage(1)
        }
        if (request.action === 'commit') {
          setCommitJob(undefined)
          setCommitReason('')
        }
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '历史回填操作失败'),
    },
  )

  const runtimeBundleMutation = useMutation(
    async (request: { action: 'assemble' | 'activate'; versionId?: string }) => {
      if (request.action === 'assemble') {
        if (!assemblyTarget || !assemblyQuery.data) throw new Error('运行包装配预检尚未完成')
        return parserApi.assembleCheckItemRuntimeBundle(
          {
            check_item_version_id: assemblyTarget.check_item_version_id,
            implementation_version_id: assemblyTarget.implementation_version_id,
            ...assemblySelection,
            preview_digest: assemblyQuery.data.preview_digest,
          },
          `runtime-bundle-assemble-${crypto.randomUUID()}`,
        )
      }
      const versionId = request.versionId || draftBundle?.version_id
        || assemblyQuery.data?.drafts?.[0]?.version_id
      if (!versionId) throw new Error('没有可发布的运行包草稿')
      return parserApi.activateCheckItemRuntimeBundle(
        versionId,
        activationReason.trim(),
        `runtime-bundle-activate-${crypto.randomUUID()}`,
      )
    },
    {
      onSuccess: (result, request) => {
        setDraftBundle(result)
        if (request.action === 'assemble') {
          message.success('运行包草稿已保存，请核对后审批激活')
        } else {
          message.success('运行包已原子激活')
          setActivationReason('')
        }
        queryClient.invalidateQueries('check-item-runtime-bundle-assembly')
        queryClient.invalidateQueries('product-check-item-runtime-bundles')
      },
      onError: (error: any) => {
        const detail = error?.response?.data?.detail
        const blockers = Array.isArray(detail?.blockers) ? detail.blockers.join('；') : ''
        message.error(
          blockers
            ? `${detail?.message || '运行包操作失败'}：${blockers}`
            : detail?.message || detail || error?.message || '运行包操作失败',
        )
      },
    },
  )

  const coverage = coverageQuery.data
  const runtimeBundleSummary = runtimeBundleQuery.data?.summary
  const runtimeBundleRows = useMemo(
    () => runtimeBundleQuery.data?.items || [],
    [runtimeBundleQuery.data?.items],
  )
  const readyRate = ratioPercent(runtimeBundleSummary?.ready_rate)
  const runtimeDataAvailable = Boolean(runtimeBundleSummary)
  const runtimeCatalogReady = Boolean(runtimeBundleSummary?.ready)
  const runtimeGapCount = Number(runtimeBundleSummary?.missing_count || 0)
    + Number(runtimeBundleSummary?.invalid_count || 0)

  const backfillRows = backfillsQuery.data?.items || []
  const openProduction = () => navigate(businessHref('/skills/parser?workspace=production&subtab=asset-production-units'))
  const openAssembly = (row: CheckItemRuntimeBundleHealthItem) => {
    setAssemblyTarget(row)
    setAssemblySelection({})
    setDraftBundle(undefined)
    setActivationReason('')
  }

  useEffect(() => {
    if (
      runtimeDeepLinkHandled
      || !targetCheckItemVersionId
      || !targetImplementationVersionId
      || runtimeBundleQuery.isLoading
    ) return
    const target = runtimeBundleRows.find((item) => (
      item.check_item_version_id === targetCheckItemVersionId
      && item.implementation_version_id === targetImplementationVersionId
    ))
    if (target) openAssembly(target)
    setRuntimeDeepLinkHandled(true)
  }, [
    runtimeBundleRows,
    runtimeBundleQuery.isLoading,
    runtimeDeepLinkHandled,
    targetCheckItemVersionId,
    targetImplementationVersionId,
  ])

  const backfillColumns = [
    {
      title: '回填任务',
      key: 'job',
      width: 300,
      render: (_: unknown, row: ParserBackfillJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.template_id}</Typography.Text>
          <Typography.Text type="secondary">{row.id}</Typography.Text>
          <Typography.Text
            type="secondary"
            ellipsis={{ tooltip: backfillScopeLabel(row) }}
            style={{ maxWidth: 280 }}
          >
            范围：{backfillScopeLabel(row)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 150,
      render: (value: string) => {
        const meta = backfillStatusMeta[value] || { label: value, className: 'is-neutral' }
        return <Tag className={`product-status-tag ${meta.className}`}>{meta.label}</Tag>
      },
    },
    {
      title: '试运行影响',
      key: 'impact',
      width: 250,
      render: (_: unknown, row: ParserBackfillJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">
            Snapshot {row.summary_json?.snapshot_count || 0} · Observation {row.summary_json?.observation_count || 0}
            <br />
            Change {row.summary_json?.change_count || 0} · Event {row.summary_json?.event_count || 0}
          </Typography.Text>
          {row.status === 'validation_failed' && (
            <Typography.Text type="danger">
              {row.last_error
                || (Number(row.summary_json?.snapshot_count || 0) === 0
                  ? '未命中 Snapshot，请检查数据来源、设备和时间范围'
                  : `${row.summary_json?.failed_count || 0} 条 Snapshot 解析或归一化失败`)}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '申请信息',
      key: 'request',
      render: (_: unknown, row: ParserBackfillJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.requested_by || '-'}</Typography.Text>
          <Typography.Text type="secondary">{formatTime(row.created_at)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 230,
      fixed: 'right' as const,
      render: (_: unknown, row: ParserBackfillJob) => (
        <Space>
          {['draft', 'validation_failed', 'pending_approval'].includes(row.status) && (
            <Button
              type="text"
              icon={<PlayCircleOutlined />}
              loading={backfillMutation.isLoading}
              onClick={() => backfillMutation.mutate({ action: 'dry-run', id: row.id })}
            >
              {row.status === 'pending_approval' ? '重新试运行' : '试运行'}
            </Button>
          )}
          {row.status === 'pending_approval' && (
            <Button
              type="text"
              icon={<CheckOutlined />}
              onClick={() => {
                setCommitJob(row)
                setCommitReason('')
              }}
            >
              批准写入
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const invalidDateRange = Boolean(
    observedFrom && observedTo && new Date(observedFrom) > new Date(observedTo),
  )
  const assembly = assemblyQuery.data
  const assetRequirementProfile = assembly?.asset_requirement_profile
  const assetRequirements = assetRequirementProfile?.requirements || {}
  const parserRequired = Boolean(assetRequirements.parser_template?.required)
  const policyRelevant = Boolean(
    assetRequirements.change_policy?.required
    || assetRequirements.change_policy?.conditional_observation_types?.length,
  )
  const eventRuleRelevant = Boolean(
    assetRequirements.current_state_rule?.required
    || assetRequirements.current_state_rule?.conditional_observation_types?.length
    || assetRequirements.event_rule?.required
    || Object.keys(
      assetRequirements.event_rule?.conditional_event_types_by_observation || {},
    ).length,
  )
  const comparisonRequired = Boolean(assetRequirements.comparison_profile?.required)
  const hasSelectableAssets = (
    parserRequired
    || policyRelevant
    || eventRuleRelevant
    || comparisonRequired
  )
  const assemblyDraftVersionId = draftBundle?.version_id || assembly?.drafts?.[0]?.version_id
  const primaryRemediationAction = assembly?.blockers.find(
    (item) => Boolean(item.remediation_action?.target_url),
  )?.remediation_action
  const assemblyErrorDetail = (assemblyQuery.error as any)?.response?.data?.detail
  const assemblyErrorText = typeof assemblyErrorDetail === 'string'
    ? assemblyErrorDetail
    : assemblyErrorDetail?.message || '无法完成运行包装配预检'

  return (
    <div className="coverage-backfill-workspace">
      {view !== 'rollout' && (
        <>
          <div className="product-workspace-header">
            <div>
              <Typography.Title level={4}>覆盖与历史回填</Typography.Title>
              <Typography.Text type="secondary">生产规则覆盖、运行时完整性与历史数据修复</Typography.Text>
            </div>
            <Button aria-label="刷新覆盖与回填" icon={<ReloadOutlined />} onClick={refresh} />
          </div>

          <div className="coverage-metrics">
            <div>
              <span>生产运行包就绪率</span>
              <strong>{runtimeDataAvailable ? `${readyRate}%` : '-'}</strong>
            </div>
            <div>
              <span>活动目录实现</span>
              <strong>{runtimeDataAvailable ? runtimeBundleSummary?.implementation_pair_count : '-'}</strong>
            </div>
            <div>
              <span>Active RuntimeBundle</span>
              <strong>{runtimeDataAvailable ? runtimeBundleSummary?.ready_count : '-'}</strong>
            </div>
            <div><span>运行包缺口</span><strong>{runtimeDataAvailable ? runtimeGapCount : '-'}</strong></div>
            <div><span>待批准回填</span><strong>{coverage?.backfills_pending_approval || 0}</strong></div>
            <div><span>失败回填</span><strong>{coverage?.backfills_failed || 0}</strong></div>
          </div>
        </>
      )}

      <div className="rule-asset-view-switch coverage-view-switch" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'coverage'}
          className={view === 'coverage' ? 'is-active' : ''}
          onClick={() => changeView('coverage')}
        >
          覆盖健康
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'backfill'}
          className={view === 'backfill' ? 'is-active' : ''}
          onClick={() => changeView('backfill')}
        >
          历史回填
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'rollout'}
          className={view === 'rollout' ? 'is-active' : ''}
          onClick={() => changeView('rollout')}
        >
          渐进发布
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'gaps'}
          className={view === 'gaps' ? 'is-active' : ''}
          onClick={() => changeView('gaps')}
        >
          治理缺口
        </button>
      </div>

      {view === 'rollout' ? (
        <RolloutCampaignWorkspace />
      ) : view === 'gaps' ? (
        <RuleGapsWorkspace />
      ) : view === 'coverage' ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {runtimeBundleQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="运行包健康数据加载失败"
              description="当前无法判断生产就绪状态，请刷新后重试。"
            />
          )}
          <div className="coverage-phase-band">
            <div className="coverage-phase">
              <div className="coverage-phase__header">
                <span>1</span>
                <Typography.Text strong>活动检查项目录</Typography.Text>
                <Tag className={`product-status-tag ${runtimeDataAvailable ? 'is-published' : 'is-failed'}`}>
                  {runtimeDataAvailable ? '已冻结' : '未知'}
                </Tag>
              </div>
              <Typography.Text type="secondary">
                {runtimeDataAvailable ? `${runtimeBundleSummary?.check_item_count} 个检查项` : '目录数据不可用'}
              </Typography.Text>
              <Typography.Text>
                {runtimeDataAvailable
                  ? `${runtimeBundleSummary?.implementation_pair_count} 个检查项与采集实现组合`
                  : '不能计算生产目标组合'}
              </Typography.Text>
            </div>
            <div className="coverage-phase">
              <div className="coverage-phase__header">
                <span>2</span>
                <Typography.Text strong>原子运行包</Typography.Text>
                <Tag className={`product-status-tag ${runtimeCatalogReady ? 'is-published' : 'is-failed'}`}>
                  {runtimeCatalogReady ? '就绪' : runtimeDataAvailable ? '未就绪' : '未知'}
                </Tag>
              </div>
              <Typography.Text type="secondary">CheckItemRuntimeBundle</Typography.Text>
              <Typography.Text>
                {runtimeDataAvailable
                  ? `${runtimeBundleSummary?.ready_count} / ${runtimeBundleSummary?.implementation_pair_count} 已激活`
                  : '未取得运行包健康数据'}
              </Typography.Text>
            </div>
            <div className="coverage-phase">
              <div className="coverage-phase__header">
                <span>3</span>
                <Typography.Text strong>生产结论能力</Typography.Text>
                <Tag className={`product-status-tag ${runtimeCatalogReady ? 'is-published' : 'is-failed'}`}>
                  {runtimeCatalogReady ? '可用' : runtimeDataAvailable ? '受限' : '无法判断'}
                </Tag>
              </div>
              <Typography.Text type="secondary">Effective Plan 只读取 Active RuntimeBundle</Typography.Text>
              <Typography.Text>
                {!runtimeDataAvailable
                  ? '运行包健康数据不可用，禁止形成健康结论'
                  : runtimeCatalogReady
                  ? '活动目录具备完整生产运行包'
                  : '缺口检查项在巡检中必须形成无法判断'}
              </Typography.Text>
            </div>
          </div>

          <div className="coverage-plan-summary">
            <div>
              <Typography.Text strong>活动目录 RuntimeBundle 覆盖</Typography.Text>
              <Typography.Text type="secondary">
                {runtimeDataAvailable
                  ? `${runtimeBundleSummary?.ready_count} / ${runtimeBundleSummary?.implementation_pair_count} 个采集实现可进入生产计划`
                  : '当前无法计算生产覆盖'}
              </Typography.Text>
            </div>
            <Progress
              percent={readyRate}
              showInfo={false}
              strokeColor="#2f343a"
              trailColor="#e8eaed"
            />
          </div>

          <div className="product-section-header">
            <div>
              <Typography.Title level={5}>运行包就绪明细</Typography.Title>
              <Typography.Text type="secondary">
                {runtimeDataAvailable
                  ? `${runtimeGapCount} 个缺口影响生产结论完整性`
                  : '健康数据不可用，不能声明无缺口'}
              </Typography.Text>
            </div>
            <Space wrap>
              <Input.Search
                allowClear
                value={runtimeSearchDraft}
                placeholder="搜索检查项、采集实现或命令"
                onChange={(event) => setRuntimeSearchDraft(event.target.value)}
                onSearch={(value) => {
                  setRuntimeSearch(value.trim())
                  setRuntimePage(1)
                }}
                style={{ width: isMobile ? '100%' : 280 }}
              />
              <Select
                allowClear
                placeholder="运行包状态"
                value={runtimeStatus}
                onChange={(value) => {
                  setRuntimeStatus(value)
                  setRuntimePage(1)
                }}
                options={[
                  { value: 'ready', label: '已就绪' },
                  { value: 'missing', label: '缺少运行包' },
                  { value: 'invalid', label: '依赖失效' },
                ]}
                style={{ width: 150 }}
              />
              <Button icon={<ArrowRightOutlined />} onClick={openProduction}>前往生产工作台</Button>
            </Space>
          </div>

          {isMobile ? (
            <List
              className="product-mobile-list"
              dataSource={runtimeBundleRows}
              loading={runtimeBundleQuery.isLoading}
              locale={{ emptyText: <Empty description="没有符合条件的运行包" /> }}
              renderItem={(row: CheckItemRuntimeBundleHealthItem) => (
                <List.Item>
                  <div className="product-mobile-item">
                    <div className="product-mobile-item__header">
                      <Typography.Text strong>
                        {row.check_item_display_name || row.check_item_name}
                      </Typography.Text>
                      <Tag className={`product-status-tag ${row.ready ? 'is-published' : 'is-failed'}`}>
                        {row.status === 'ready' ? '已就绪' : row.status === 'invalid' ? '依赖失效' : '缺运行包'}
                      </Tag>
                    </div>
                    <Typography.Text code>{row.check_item_id}</Typography.Text>
                    <Typography.Text type="secondary">{row.implementation_name}</Typography.Text>
                    <Typography.Text type={row.ready ? 'secondary' : 'danger'}>
                      {row.blocking_reasons.join('；') || '依赖摘要完整并已激活'}
                    </Typography.Text>
                    <Button
                      icon={row.ready ? <DeploymentUnitOutlined /> : <ArrowRightOutlined />}
                      onClick={() => openAssembly(row)}
                    >
                      {row.ready ? '查看运行包' : '校验并装配'}
                    </Button>
                  </div>
                </List.Item>
              )}
            />
          ) : (
            <Table
              className="product-data-table"
              rowKey={(row) => `${row.check_item_version_id}:${row.implementation_version_id}`}
              dataSource={runtimeBundleRows}
              loading={runtimeBundleQuery.isLoading}
              pagination={false}
              columns={[
                {
                  title: '巡检检查项',
                  width: 250,
                  render: (_: unknown, row: CheckItemRuntimeBundleHealthItem) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>
                        {row.check_item_display_name || row.check_item_name}
                      </Typography.Text>
                      <Typography.Text code>{row.check_item_id}</Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '采集实现',
                  render: (_: unknown, row: CheckItemRuntimeBundleHealthItem) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text>{row.implementation_name}</Typography.Text>
                      <Typography.Text type="secondary">{row.commands.join(' / ') || '-'}</Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '运行包状态',
                  width: 160,
                  render: (_: unknown, row: CheckItemRuntimeBundleHealthItem) => (
                    <Tag className={`product-status-tag ${row.ready ? 'is-published' : 'is-failed'}`}>
                      {row.status === 'ready' ? '已就绪' : row.status === 'invalid' ? '依赖失效' : '缺少运行包'}
                    </Tag>
                  ),
                },
                {
                  title: '阻断原因',
                  width: 280,
                  render: (_: unknown, row: CheckItemRuntimeBundleHealthItem) => (
                    <Typography.Text type={row.ready ? 'secondary' : 'danger'}>
                      {row.blocking_reasons.join('；') || '依赖摘要完整并已激活'}
                    </Typography.Text>
                  ),
                },
                {
                  title: '操作',
                  width: 140,
                  render: (_: unknown, row: CheckItemRuntimeBundleHealthItem) => (
                    <Button
                      type="text"
                      icon={row.ready ? <DeploymentUnitOutlined /> : <ArrowRightOutlined />}
                      onClick={() => openAssembly(row)}
                    >
                      {row.ready ? '查看' : '校验并装配'}
                    </Button>
                  ),
                },
              ]}
            />
          )}
          {(runtimeBundleQuery.data?.total || 0) > 0 && (
            <Pagination
              current={runtimePage}
              pageSize={runtimePageSize}
              total={runtimeBundleQuery.data?.total || 0}
              showSizeChanger
              showTotal={(total) => `共 ${total} 个检查项实现`}
              onChange={(nextPage, nextPageSize) => {
                setRuntimePage(nextPageSize !== runtimePageSize ? 1 : nextPage)
                setRuntimePageSize(nextPageSize)
              }}
            />
          )}
        </Space>
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div className="product-section-header">
            <div>
              <Typography.Title level={5}>历史回填任务</Typography.Title>
              <Typography.Text type="secondary">先试运行评估影响，审批后才写入历史事实</Typography.Text>
            </div>
            <Button
              className="rule-asset-import-button"
              icon={<HistoryOutlined />}
              onClick={() => setCreateOpen(true)}
            >
              新建历史回填
            </Button>
          </div>

          <div className="product-list-filters">
            <Input.Search
              allowClear
              value={searchDraft}
              placeholder="搜索模板、任务 ID 或申请人"
              onChange={(event) => setSearchDraft(event.target.value)}
              onSearch={(value) => {
                setSearch(value.trim())
                setPage(1)
              }}
            />
            <Select
              allowClear
              placeholder="回填状态"
              value={status}
              onChange={(value) => {
                setStatus(value)
                setPage(1)
              }}
              options={Object.entries(backfillStatusMeta).map(([value, meta]) => ({
                value,
                label: meta.label,
              }))}
            />
          </div>

          {isMobile ? (
            <List
              className="product-mobile-list"
              loading={backfillsQuery.isLoading}
              dataSource={backfillRows}
              locale={{ emptyText: <Empty description="暂无历史回填任务" /> }}
              renderItem={(row) => {
                const meta = backfillStatusMeta[row.status] || { label: row.status, className: 'is-neutral' }
                return (
                  <List.Item>
                    <div className="product-mobile-item">
                      <div className="product-mobile-item__header">
                        <Typography.Text strong>{row.template_id}</Typography.Text>
                        <Tag className={`product-status-tag ${meta.className}`}>{meta.label}</Tag>
                      </div>
                      <Typography.Text type="secondary">{row.id}</Typography.Text>
                      <Typography.Text type="secondary">
                        范围：{backfillScopeLabel(row)}
                      </Typography.Text>
                      <Typography.Text>
                        Observation {row.summary_json?.observation_count || 0} · Change {row.summary_json?.change_count || 0} · Event {row.summary_json?.event_count || 0}
                      </Typography.Text>
                      {['draft', 'validation_failed', 'pending_approval'].includes(row.status) && (
                        <Button icon={<PlayCircleOutlined />} onClick={() => backfillMutation.mutate({ action: 'dry-run', id: row.id })}>
                          {row.status === 'pending_approval' ? '重新试运行' : '执行试运行'}
                        </Button>
                      )}
                      {row.status === 'pending_approval' && (
                        <Button icon={<CheckOutlined />} onClick={() => setCommitJob(row)}>批准写入</Button>
                      )}
                    </div>
                  </List.Item>
                )
              }}
            />
          ) : (
            <Table
              className="product-data-table"
              rowKey="id"
              loading={backfillsQuery.isLoading}
              dataSource={backfillRows}
              columns={backfillColumns}
              pagination={false}
              scroll={{ x: 1050 }}
            />
          )}

          <Pagination
            current={page}
            pageSize={pageSize}
            total={backfillsQuery.data?.total || 0}
            showSizeChanger={!isMobile}
            showTotal={(total) => `共 ${total} 个回填任务`}
            onChange={(nextPage, nextSize) => {
              setPage(nextPage)
              setPageSize(nextSize)
            }}
          />
        </Space>
      )}

      <Drawer
        rootClassName="product-detail-drawer runtime-bundle-assembly-drawer"
        title="运行包装配"
        open={Boolean(assemblyTarget)}
        width={isMobile ? '100vw' : 860}
        destroyOnClose
        onClose={() => setAssemblyTarget(undefined)}
        footer={(
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Button
              icon={<ArrowRightOutlined />}
              onClick={() => navigate(
                primaryRemediationAction?.target_url
                || businessHref('/skills/parser?workspace=production&subtab=asset-production-units'),
              )}
            >
              {primaryRemediationAction?.label || '修复规则资产'}
            </Button>
            <Space wrap>
              <Button onClick={() => setAssemblyTarget(undefined)}>关闭</Button>
              <Button
                icon={<DeploymentUnitOutlined />}
                loading={runtimeBundleMutation.isLoading}
                disabled={!assembly?.operationally_ready}
                onClick={() => runtimeBundleMutation.mutate({ action: 'assemble' })}
              >
                保存运行包草稿
              </Button>
              <Button
                className="product-primary-button"
                type="primary"
                icon={<CheckOutlined />}
                loading={runtimeBundleMutation.isLoading}
                disabled={!assemblyDraftVersionId}
                onClick={() => runtimeBundleMutation.mutate({
                  action: 'activate',
                  versionId: assemblyDraftVersionId,
                })}
              >
                审批激活
              </Button>
            </Space>
          </Space>
        )}
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
            <Descriptions.Item label="巡检检查项">
              {assembly?.check_item?.display_name
                || assemblyTarget?.check_item_display_name
                || assemblyTarget?.check_item_name
                || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="采集实现">
              {assemblyTarget?.implementation_name || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="结果模式">
              {assembly?.check_item?.result_mode || assemblyTarget?.result_mode || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="生产状态">
              {assembly?.active_bundle
                ? <Tag className="product-status-tag is-published">Active v{assembly.active_bundle.version}</Tag>
                : <Tag className="product-status-tag is-neutral">未发布运行包</Tag>}
            </Descriptions.Item>
          </Descriptions>

          {assemblyQuery.isLoading && <Alert type="info" showIcon message="正在校验运行包装配" />}
          {assemblyQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="装配预检失败"
              description={assemblyErrorText}
              action={<Button icon={<ReloadOutlined />} onClick={() => assemblyQuery.refetch()}>重试</Button>}
            />
          )}
          {assembly && (
            <Alert
              type={assembly.operationally_ready ? 'success' : 'warning'}
              showIcon
              icon={assembly.operationally_ready ? <CheckCircleOutlined /> : <WarningOutlined />}
              message={assembly.operationally_ready ? '运行包满足发布前置条件' : `存在 ${assembly.blockers.length} 个发布阻断项`}
              description={(
                assembly.operationally_ready
                  ? `主命令可用，可执行命令 ${assembly.compatibility.executable_commands?.length || 0} 条。`
                  : '先处理下方阻断项；平台不会把 Active Parser 或已发布规则资产冒充成运行就绪。'
              )}
            />
          )}

          {assembly && (
            <>
              {assetRequirementProfile && (
                <div>
                  <Typography.Title level={5}>资产需求画像</Typography.Title>
                  <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                    <Descriptions.Item label="结果模式">
                      {assetRequirementProfile.result_mode}
                    </Descriptions.Item>
                    <Descriptions.Item label="评估模式">
                      {assetRequirementProfile.assessment_mode}
                    </Descriptions.Item>
                    <Descriptions.Item label="必需资产" span={isMobile ? 1 : 2}>
                      <Space wrap>
                        {assetRequirementProfile.required_asset_types.map((item) => (
                          <Tag className="product-status-tag is-published" key={item}>
                            {assetTypeLabels[item] || item}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="不适用资产" span={isMobile ? 1 : 2}>
                      <Space wrap>
                        {Object.keys(assetRequirementProfile.not_required).map((item) => (
                          <Tag className="product-status-tag is-neutral" key={item}>
                            {assetTypeLabels[item] || item}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="运行事实" span={isMobile ? 1 : 2}>
                      <Space wrap>
                        {assetRequirementProfile.required_runtime_facts.map((item) => (
                          <Tag key={item}>{item}</Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="画像指纹" span={isMobile ? 1 : 2}>
                      <Typography.Text
                        code
                        copyable={{ text: assembly.asset_requirement_profile_digest || '' }}
                      >
                        {assembly.asset_requirement_profile_digest
                          ? `${assembly.asset_requirement_profile_digest.slice(0, 12)}…${assembly.asset_requirement_profile_digest.slice(-8)}`
                          : '-'}
                      </Typography.Text>
                    </Descriptions.Item>
                  </Descriptions>
                </div>
              )}

              <div className="product-section-header">
                <div>
                  <Typography.Title level={5}>命令执行路径</Typography.Title>
                  <Typography.Text type="secondary">
                    {parserRequired
                      ? '结构化命令路径'
                      : comparisonRequired
                        ? '文本比较命令路径'
                        : '证据采集命令路径'}
                  </Typography.Text>
                </div>
                <Tag className={`product-status-tag ${assembly.compatibility.command_availability === 'available' ? 'is-published' : 'is-review'}`}>
                  {assembly.compatibility.command_availability === 'available' ? '主命令可用' : '主命令待处理'}
                </Tag>
              </div>
              <Table
                size="small"
                rowKey="canonical_command"
                pagination={false}
                dataSource={assembly.command_routes}
                scroll={{ x: 720 }}
                columns={[
                  {
                    title: '命令',
                    dataIndex: 'command',
                    width: 230,
                    render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
                  },
                  {
                    title: '优先级',
                    dataIndex: 'priority',
                    width: 90,
                  },
                  {
                    title: '路径状态',
                    dataIndex: 'status',
                    width: 120,
                    render: (value: string) => (
                      <Tag className={`product-status-tag ${value === 'executable' ? 'is-published' : value === 'ambiguous' ? 'is-review' : 'is-failed'}`}>
                        {value === 'executable' ? '可执行' : value === 'ambiguous' ? '待选择' : '不可执行'}
                      </Tag>
                    ),
                  },
                   {
                     title: '冻结资产',
                     render: (_: unknown, row: any) => (
                       <Space direction="vertical" size={0}>
                         {parserRequired ? (
                           <>
                             <Typography.Text type="secondary">
                               Parser {row.selected_parser_template_id || '-'}
                             </Typography.Text>
                             <Typography.Text type="secondary">
                               V2 语义投影
                             </Typography.Text>
                           </>
                         ) : comparisonRequired ? (
                           <Typography.Text type="secondary">
                             TextComparisonProfile
                           </Typography.Text>
                         ) : (
                           <Typography.Text type="secondary">
                             Evidence policy
                           </Typography.Text>
                         )}
                       </Space>
                     ),
                   },
                ]}
              />

              {Boolean(assetRequirements.command_adapter_projection?.required) && (
                <div>
                  <Typography.Title level={5}>标准语义覆盖</Typography.Title>
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="检查项要求">
                      <Space wrap>
                        {(assembly.compatibility.expected_observation_types || []).map((item) => <Tag key={item}>{item}</Tag>)}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="适配器产生">
                      <Space wrap>
                        {(assembly.compatibility.produced_observation_types || []).map((item) => <Tag key={item}>{item}</Tag>)}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label="本运行包投影">
                      <Space wrap>
                        {(assembly.compatibility.observation_projection || []).map((item) => (
                          <Tag className="product-status-tag is-published" key={item}>{item}</Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  </Descriptions>
                </div>
              )}

              <div>
                <Typography.Title level={5}>资产选择</Typography.Title>
                {!hasSelectableAssets && (
                  <Alert
                    type="info"
                    showIcon
                    message="当前检查项无需人工选择运行资产"
                  />
                )}
                {hasSelectableAssets && (
                  <Descriptions bordered size="small" column={1} style={{ marginTop: 10 }}>
                  {parserRequired && (
                    <Descriptions.Item label="Parser">
                    <Select
                      allowClear
                      mode="multiple"
                      placeholder="留空由平台确定性选择"
                      value={assemblySelection.parser_template_ids}
                      style={{ width: '100%' }}
                      onChange={(value) => setAssemblySelection((current) => ({
                        ...current,
                        parser_template_ids: value,
                      }))}
                      options={assembly.candidates.parsers.map((item: any) => ({
                        value: item.parser_template_id,
                        label: `${item.vendor} ${item.profile} / ${item.canonical_command}${item.ready ? '' : '（不可用）'}`,
                      }))}
                    />
                  </Descriptions.Item>
                  )}
                  {policyRelevant && (
                    <Descriptions.Item label="ChangePolicy">
                    <Select
                      allowClear
                      mode="multiple"
                      placeholder="留空由平台按 canonical observation 选择"
                      value={assemblySelection.change_policy_version_ids}
                      style={{ width: '100%' }}
                      onChange={(value) => setAssemblySelection((current) => ({
                        ...current,
                        change_policy_version_ids: value,
                      }))}
                      options={assembly.candidates.change_policies.map((item: any) => ({
                        value: item.version_id,
                        label: `${item.observation_type} / ${item.version_id}`,
                      }))}
                    />
                  </Descriptions.Item>
                  )}
                  {eventRuleRelevant && (
                    <Descriptions.Item label="CurrentStateRule / EventRule">
                    <Select
                      allowClear
                      mode="multiple"
                      placeholder="留空选择全部显式匹配的规则"
                      value={assemblySelection.event_rule_version_ids}
                      style={{ width: '100%' }}
                      onChange={(value) => setAssemblySelection((current) => ({
                        ...current,
                        event_rule_version_ids: value,
                      }))}
                      options={assembly.candidates.event_rules.map((item: any) => ({
                        value: item.event_rule_version_id,
                        label: `${item.name} / ${(item.observation_types || []).join(', ') || '未声明语义'}`,
                      }))}
                    />
                  </Descriptions.Item>
                  )}
                  {comparisonRequired && (
                    <Descriptions.Item label="TextComparisonProfile">
                      <Select
                        allowClear
                        placeholder="选择非结构化比较规则"
                        value={assemblySelection.comparison_profile_version_id || undefined}
                        style={{ width: '100%' }}
                        onChange={(value) => setAssemblySelection((current) => ({
                          ...current,
                          comparison_profile_version_id: value || null,
                        }))}
                        options={assembly.candidates.comparison_profiles.map((item: any) => ({
                          value: item.profile_version_id,
                          label: `${item.name} / v${item.version}`,
                        }))}
                      />
                    </Descriptions.Item>
                  )}
                </Descriptions>
                )}
              </div>

              {assembly.blockers.length > 0 && (
                <div>
                  <Typography.Title level={5}>发布阻断</Typography.Title>
                  <List
                    size="small"
                    dataSource={assembly.blockers}
                    renderItem={(item) => (
                      <List.Item>
                        <Space align="start">
                          <WarningOutlined />
                          <div>
                            <Typography.Text strong>{item.message}</Typography.Text>
                            <br />
                            <Typography.Text code>{item.code}</Typography.Text>
                            {item.remediation && (
                              <>
                                <br />
                                <Typography.Text type="secondary">{item.remediation}</Typography.Text>
                              </>
                            )}
                            {item.remediation_action?.target_url && (
                              <>
                                <br />
                                <Button
                                  size="small"
                                  icon={<ArrowRightOutlined />}
                                  onClick={() => navigate(item.remediation_action!.target_url)}
                                >
                                  {item.remediation_action.label}
                                </Button>
                              </>
                            )}
                          </div>
                        </Space>
                      </List.Item>
                    )}
                  />
                </div>
              )}

              {assembly.warnings.length > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  message={assembly.operationally_ready
                    ? '可运行但存在降级项'
                    : '附加注意事项'}
                  description={[
                    ...(!assembly.operationally_ready
                      ? ['当前仍不可发布，以下说明不改变上方阻断状态']
                      : []),
                    ...assembly.warnings.map((item) => item.message),
                  ].join('；')}
                />
              )}

              {(assemblyDraftVersionId || draftBundle) && (
                <div>
                  <Typography.Title level={5}>发布审批</Typography.Title>
                  <Typography.Text type="secondary">
                    草稿 {assemblyDraftVersionId}。激活后 Effective Plan 才能读取该运行包。
                  </Typography.Text>
                  <Input.TextArea
                    rows={3}
                    value={activationReason}
                    placeholder="发布说明（可选）"
                    onChange={(event) => setActivationReason(event.target.value)}
                    style={{ marginTop: 10 }}
                  />
                </div>
              )}
            </>
          )}
        </Space>
      </Drawer>

      <Drawer
        rootClassName="product-detail-drawer"
        title="新建历史回填"
        open={createOpen}
        width={isMobile ? '100vw' : 640}
        destroyOnClose
        onClose={() => setCreateOpen(false)}
        footer={(
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
            <Button onClick={() => setCreateOpen(false)}>取消</Button>
            <Button
              type="primary"
              className="product-primary-button"
              icon={<PlayCircleOutlined />}
              loading={backfillMutation.isLoading}
              disabled={!templateId || runModes.length === 0 || invalidDateRange}
              onClick={() => templateId && backfillMutation.mutate({
                action: 'create',
                id: templateId,
                filters: {
                  device_ids: deviceIds,
                  dataset_ids: datasetIds,
                  run_modes: runModes,
                  observed_from: observedFrom ? new Date(observedFrom).toISOString() : null,
                  observed_to: observedTo ? new Date(observedTo).toISOString() : null,
                  limit: snapshotLimit,
                },
              })}
            >
              创建并试运行
            </Button>
          </Space>
        )}
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            第一步先解析所选历史数据并计算影响，不写入 Observation、Change 或 Event；
            试运行全部通过后，再由工程师检查结果并批准写入。
          </Typography.Text>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="Active Parser">
              <Select
                showSearch
                filterOption={false}
                value={templateId}
                placeholder="搜索命令、厂家或型号"
                style={{ width: '100%' }}
                loading={activeTemplatesQuery.isLoading}
                onSearch={setTemplateSearch}
                onChange={setTemplateId}
                options={(activeTemplatesQuery.data?.items || []).map((item) => ({
                  value: item.id,
                  label: `${item.vendor} ${item.profile} / ${item.canonical_command}`,
                }))}
              />
            </Descriptions.Item>
            <Descriptions.Item label="设备范围">
              <Select
                mode="tags"
                open={false}
                value={deviceIds}
                style={{ width: '100%' }}
                placeholder="留空表示全部，可粘贴设备 ID"
                tokenSeparators={[',', '，', ' ']}
                onChange={setDeviceIds}
              />
            </Descriptions.Item>
            <Descriptions.Item label="数据来源">
              <Select
                mode="multiple"
                value={runModes}
                style={{ width: '100%' }}
                onChange={setRunModes}
                options={[
                  { value: 'production', label: '在线巡检数据' },
                  { value: 'parser_validation', label: '离线规则样本' },
                ]}
              />
            </Descriptions.Item>
            <Descriptions.Item label="数据集">
              <Select
                mode="tags"
                open={false}
                value={datasetIds}
                style={{ width: '100%' }}
                placeholder="留空表示该数据来源下的全部数据集"
                tokenSeparators={[',', '，', ' ']}
                onChange={setDatasetIds}
              />
            </Descriptions.Item>
            <Descriptions.Item label="采集时间范围">
              <Space.Compact block>
                <Input type="datetime-local" value={observedFrom} onChange={(event) => setObservedFrom(event.target.value)} />
                <Input type="datetime-local" value={observedTo} onChange={(event) => setObservedTo(event.target.value)} />
              </Space.Compact>
              {invalidDateRange && <Typography.Text type="danger">开始时间不能晚于结束时间</Typography.Text>}
            </Descriptions.Item>
            <Descriptions.Item label="最大 Snapshot 数">
              <InputNumber min={1} max={5000} value={snapshotLimit} onChange={(value) => setSnapshotLimit(Number(value || 1000))} />
            </Descriptions.Item>
          </Descriptions>
        </Space>
      </Drawer>

      <Modal
        title="批准历史回填写入"
        open={Boolean(commitJob)}
        okText="批准写入"
        cancelText="取消"
        okButtonProps={{ className: 'product-primary-button' }}
        confirmLoading={backfillMutation.isLoading}
        onCancel={() => {
          setCommitJob(undefined)
          setCommitReason('')
        }}
        onOk={() => commitJob && backfillMutation.mutate({
          action: 'commit',
          id: commitJob.id,
          reason: commitReason.trim(),
        })}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text>
            将写入 Observation {commitJob?.summary_json?.observation_count || 0}、
            Change {commitJob?.summary_json?.change_count || 0}、
            Event {commitJob?.summary_json?.event_count || 0}。
          </Typography.Text>
          <Input.TextArea
            rows={4}
            value={commitReason}
            placeholder="审批说明（可选）"
            onChange={(event) => setCommitReason(event.target.value)}
          />
        </Space>
      </Modal>
    </div>
  )
}

export default CoverageBackfillWorkspace
