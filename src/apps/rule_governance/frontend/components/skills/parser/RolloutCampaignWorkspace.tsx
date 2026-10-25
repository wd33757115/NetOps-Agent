import React, { useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Grid,
  Input,
  List,
  Modal,
  Pagination,
  Segmented,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckOutlined,
  EyeOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'

import {
  parserApi,
  type RolloutCampaign,
  type RolloutCampaignFreezePayload,
  type RolloutCampaignStatus,
  type RolloutStageAttempt,
  type RolloutStageExecution,
} from '@netops/services/api'

const stageLabels: Record<string, string> = {
  shadow: '影子验证',
  canary_5: '5% 灰度',
  canary_20: '20% 灰度',
  canary_50: '50% 灰度',
  full_100: '100% 切换',
  observing: '发布观察',
}

const statusMeta: Record<string, { label: string; className: string }> = {
  draft: { label: '待冻结目标', className: 'is-neutral' },
  target_frozen: { label: '目标已冻结', className: 'is-review' },
  shadow: { label: '影子验证', className: 'is-running' },
  canary_5: { label: '5% 灰度', className: 'is-running' },
  canary_20: { label: '20% 灰度', className: 'is-running' },
  canary_50: { label: '50% 灰度', className: 'is-running' },
  full_100: { label: '100% 切换', className: 'is-running' },
  observing: { label: '观察期', className: 'is-review' },
  paused: { label: '已暂停', className: 'is-failed' },
  rolled_back: { label: '已回滚', className: 'is-failed' },
  completed: { label: '已完成', className: 'is-published' },
  pending: { label: '待启动', className: 'is-neutral' },
  running: { label: '执行中', className: 'is-running' },
  awaiting_approval: { label: '待批准', className: 'is-review' },
  passed: { label: '已通过', className: 'is-published' },
  failed: { label: '未通过', className: 'is-failed' },
  queued: { label: '已排队', className: 'is-neutral' },
  evaluating: { label: '对账中', className: 'is-running' },
  capturing: { label: '采集中', className: 'is-running' },
  cancelled: { label: '已取消', className: 'is-neutral' },
}

type LifecycleAction = 'start' | 'approve' | 'pause' | 'resume' | 'rollback'

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

const shortDigest = (value?: string | null) => (
  value ? `${value.slice(0, 10)}…${value.slice(-6)}` : '-'
)

const statusTag = (status?: string | null) => {
  const meta = statusMeta[status || ''] || {
    label: status || '未知',
    className: 'is-neutral',
  }
  return (
    <Tag className={`product-status-tag ${meta.className}`}>
      {meta.label}
    </Tag>
  )
}

const errorMessage = (error: any) => {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail?.blockers) && detail.blockers.length > 0) {
    return detail.blockers.join('；')
  }
  return detail?.message || detail || error?.message || '操作失败'
}

const idempotencyKey = (action: string, id = 'new') => (
  `${action}-${id}-${crypto.randomUUID()}`
)

const RolloutCampaignWorkspace: React.FC = () => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<RolloutCampaignStatus>()
  const [selectedId, setSelectedId] = useState<string>()
  const [selectedExecutionId, setSelectedExecutionId] = useState<string>()
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [freezeCampaign, setFreezeCampaign] = useState<RolloutCampaign>()
  const [targetManifestText, setTargetManifestText] = useState('{\n  "matrix": []\n}')
  const [runtimeRefsText, setRuntimeRefsText] = useState('[]')
  const [contextIdsText, setContextIdsText] = useState('')
  const [baselineStart, setBaselineStart] = useState('')
  const [baselineEnd, setBaselineEnd] = useState('')
  const [thresholdsText, setThresholdsText] = useState('{}')
  const [rollbackMode, setRollbackMode] = useState<
    'legacy_route' | 'runtime_bundle' | 'no_prior_production'
  >('legacy_route')
  const [rollbackEvidenceText, setRollbackEvidenceText] = useState('')
  const [freezeReason, setFreezeReason] = useState('')
  const [lifecycleAction, setLifecycleAction] = useState<LifecycleAction>()
  const [lifecycleCampaign, setLifecycleCampaign] = useState<RolloutCampaign>()
  const [lifecycleReason, setLifecycleReason] = useState('')
  const [executionMode, setExecutionMode] = useState<
    'snapshot_replay' | 'live_capture'
  >('snapshot_replay')
  const [rollbackEvidence, setRollbackEvidence] = useState('')

  const campaignsQuery = useQuery(
    ['check-item-rollout-campaigns', page, pageSize, status, search],
    () => parserApi.rolloutCampaigns({
      page,
      page_size: pageSize,
      status,
      search: search || undefined,
    }),
    { keepPreviousData: true },
  )
  const detailQuery = useQuery(
    ['check-item-rollout-campaign', selectedId],
    () => parserApi.getRolloutCampaign(selectedId!),
    { enabled: Boolean(selectedId), refetchInterval: 5000 },
  )
  const executionDetailQuery = useQuery(
    ['check-item-rollout-stage-execution', selectedId, selectedExecutionId],
    () => parserApi.getRolloutStageExecution(selectedId!, selectedExecutionId!),
    {
      enabled: Boolean(selectedId && selectedExecutionId),
      refetchInterval: (data) => (
        data && ['queued', 'capturing', 'running', 'evaluating'].includes(data.status)
          ? 3000
          : false
      ),
    },
  )

  const refresh = () => {
    queryClient.invalidateQueries('check-item-rollout-campaigns')
    queryClient.invalidateQueries('check-item-rollout-campaign')
    queryClient.invalidateQueries('check-item-rollout-stage-execution')
  }

  const createMutation = useMutation(
    () => parserApi.createRolloutCampaign(
      {
        name: createName.trim(),
        description: createDescription.trim() || undefined,
      },
      idempotencyKey('rollout-create'),
    ),
    {
      onSuccess: (campaign) => {
        message.success('发布 Campaign 已创建')
        setCreateOpen(false)
        setCreateName('')
        setCreateDescription('')
        setSelectedId(campaign.id)
        refresh()
      },
      onError: (error: any) => message.error(errorMessage(error)),
    },
  )

  const freezeMutation = useMutation(
    async () => {
      if (!freezeCampaign) throw new Error('未选择 Campaign')
      const targetManifest = JSON.parse(targetManifestText)
      const runtimeRefs = JSON.parse(runtimeRefsText)
      const thresholds = JSON.parse(thresholdsText || '{}')
      if (!Array.isArray(runtimeRefs)) throw new Error('RuntimeBundle 引用必须是数组')
      const contextIds = contextIdsText
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
      const evidenceRefs = rollbackEvidenceText
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
        .map((ref) => ({ ref }))
      const payload: RolloutCampaignFreezePayload = {
        target_manifest: targetManifest,
        runtime_bundle_refs: runtimeRefs,
        comparison_context_ids: contextIds,
        baseline_window: {
          start_at: new Date(baselineStart).toISOString(),
          end_at: new Date(baselineEnd).toISOString(),
        },
        thresholds,
        rollback_plan: {
          mode: rollbackMode,
          verified: evidenceRefs.length > 0,
          evidence_refs: evidenceRefs,
        },
        reason: freezeReason.trim(),
      }
      return parserApi.freezeRolloutCampaign(
        freezeCampaign.id,
        payload,
        idempotencyKey('rollout-freeze', freezeCampaign.id),
      )
    },
    {
      onSuccess: (campaign) => {
        message.success('目标矩阵与发布基线已冻结')
        setFreezeCampaign(undefined)
        setSelectedId(campaign.id)
        refresh()
      },
      onError: (error: any) => message.error(errorMessage(error)),
    },
  )

  const lifecycleMutation = useMutation(
    async () => {
      if (!lifecycleCampaign || !lifecycleAction) throw new Error('未选择发布操作')
      const campaignId = lifecycleCampaign.id
      const key = idempotencyKey(`rollout-${lifecycleAction}`, campaignId)
      if (lifecycleAction === 'start') {
        return parserApi.startRolloutStage(
          campaignId,
          lifecycleReason,
          key,
          executionMode,
        )
      }
      if (lifecycleAction === 'approve') {
        return parserApi.approveRolloutStage(campaignId, lifecycleReason, key)
      }
      if (lifecycleAction === 'pause') {
        return parserApi.pauseRolloutCampaign(campaignId, lifecycleReason, key)
      }
      if (lifecycleAction === 'resume') {
        return parserApi.resumeRolloutCampaign(
          campaignId,
          {
            reason: lifecycleReason,
            observed_digests: {
              target_manifest_digest: lifecycleCampaign.target_manifest_digest || '',
              runtime_bundle_digest: lifecycleCampaign.runtime_bundle_digest || '',
              comparison_context_digest: lifecycleCampaign.comparison_context_digest || '',
            },
          },
          key,
        )
      }
      const evidenceRefs = rollbackEvidence
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
        .map((ref) => ({ ref }))
      return parserApi.rollbackRolloutCampaign(
        campaignId,
        { reason: lifecycleReason, evidence_refs: evidenceRefs },
        key,
      )
    },
    {
      onSuccess: (campaign) => {
        const label = lifecycleAction === 'approve'
          ? '阶段已批准'
          : lifecycleAction === 'rollback'
            ? 'Campaign 已回滚'
            : '操作已提交'
        message.success(label)
        setLifecycleAction(undefined)
        setLifecycleCampaign(undefined)
        setLifecycleReason('')
        setExecutionMode('snapshot_replay')
        setRollbackEvidence('')
        setSelectedId(campaign.id)
        refresh()
      },
      onError: (error: any) => message.error(errorMessage(error)),
    },
  )

  const rows = campaignsQuery.data?.items || []
  const summary = campaignsQuery.data?.summary
  const selected = detailQuery.data
  const targetCount = selected?.target_manifest_json?.matrix?.length || 0
  const stageItems = (selected?.stage_sequence || []).map((stage) => {
    const attempts = (selected?.attempts || []).filter((item) => item.stage === stage)
    const attempt = attempts[attempts.length - 1]
    const isCurrent = selected?.current_stage === stage
    const stepStatus = attempt?.status === 'passed'
      ? 'finish'
      : ['failed', 'rolled_back'].includes(attempt?.status || '')
        ? 'error'
        : isCurrent
          ? 'process'
          : 'wait'
    return {
      title: stageLabels[stage] || stage,
      status: stepStatus as 'finish' | 'error' | 'process' | 'wait',
      description: attempt
        ? `${statusMeta[attempt.status]?.label || attempt.status} · 第 ${attempt.attempt_number} 次`
        : '尚未开始',
    }
  })

  const openFreeze = (campaign: RolloutCampaign) => {
    setFreezeCampaign(campaign)
    setTargetManifestText(JSON.stringify(
      campaign.target_manifest_json || { matrix: [] },
      null,
      2,
    ))
    setRuntimeRefsText(JSON.stringify(campaign.runtime_bundle_refs_json || [], null, 2))
    setContextIdsText((campaign.comparison_context_ids_json || []).join('\n'))
    setBaselineStart('')
    setBaselineEnd('')
    setThresholdsText(JSON.stringify(campaign.thresholds_json || {}, null, 2))
    setRollbackEvidenceText('')
    setFreezeReason('')
  }

  const openLifecycle = (campaign: RolloutCampaign, action: LifecycleAction) => {
    setLifecycleCampaign(campaign)
    setLifecycleAction(action)
    setLifecycleReason('')
    setExecutionMode('snapshot_replay')
    setRollbackEvidence('')
  }

  const renderActions = (campaign: RolloutCampaign) => (
    <Space wrap size={4}>
      {campaign.status === 'draft' && (
        <Button type="text" onClick={() => openFreeze(campaign)}>
          冻结目标
        </Button>
      )}
      {campaign.next_action === 'start_stage' && (
        <Button
          type="text"
          icon={<PlayCircleOutlined />}
          onClick={() => openLifecycle(campaign, 'start')}
        >
          启动阶段
        </Button>
      )}
      {campaign.next_action === 'approve_stage' && (
        <Button
          type="text"
          icon={<CheckOutlined />}
          onClick={() => openLifecycle(campaign, 'approve')}
        >
          批准晋级
        </Button>
      )}
      {campaign.status === 'paused' && (
        <Button type="text" onClick={() => openLifecycle(campaign, 'resume')}>
          恢复
        </Button>
      )}
      {!['draft', 'paused', 'rolled_back', 'completed'].includes(campaign.status) && (
        <Button
          type="text"
          icon={<PauseOutlined />}
          onClick={() => openLifecycle(campaign, 'pause')}
        >
          暂停
        </Button>
      )}
      {!['draft', 'rolled_back', 'completed'].includes(campaign.status) && (
        <Button
          type="text"
          icon={<RollbackOutlined />}
          onClick={() => openLifecycle(campaign, 'rollback')}
        >
          回滚
        </Button>
      )}
    </Space>
  )

  const lifecycleTitle = {
    start: '启动当前发布阶段',
    approve: '批准当前发布阶段',
    pause: '暂停发布 Campaign',
    resume: '恢复发布 Campaign',
    rollback: '回滚发布 Campaign',
  }[lifecycleAction || 'start']

  const lifecycleImpact = lifecycleAction === 'approve'
    ? '批准后会进入下一灰度阶段；当前阶段的目标、资产与对账摘要保持不可变。'
    : lifecycleAction === 'rollback'
      ? '候选路由将立即失效，生产流量回到冻结的上一稳定路径。'
      : lifecycleAction === 'pause'
        ? '候选路由将停止，当前阶段保留现场和证据。'
        : lifecycleAction === 'resume'
          ? '系统会重新校验三类摘要；存在漂移时必须新建 Campaign。'
          : '启动后由执行器生成阶段指标和新旧链路对账，人工不能填写通过结果。'

  return (
    <div className="rollout-campaign-workspace">
      <div className="product-workspace-header">
        <div>
          <Typography.Title level={4}>渐进发布</Typography.Title>
          <Typography.Text type="secondary">
            冻结目标与运行资产，按影子、灰度和观察期形成发布证据
          </Typography.Text>
        </div>
        <Space>
          <Button
            aria-label="刷新渐进发布"
            icon={<ReloadOutlined />}
            onClick={refresh}
          />
          <Button
            className="product-primary-button"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            新建发布
          </Button>
        </Space>
      </div>

      <div className="rollout-metrics">
        <div><span>发布总数</span><strong>{summary?.total ?? '-'}</strong></div>
        <div><span>活动中</span><strong>{summary?.active ?? '-'}</strong></div>
        <div><span>待批准</span><strong>{summary?.awaiting_approval ?? '-'}</strong></div>
        <div><span>已暂停</span><strong>{summary?.paused ?? '-'}</strong></div>
        <div><span>已回滚</span><strong>{summary?.rolled_back ?? '-'}</strong></div>
      </div>

      {campaignsQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="渐进发布数据加载失败"
          description="当前无法判断发布状态，请刷新后重试。"
          style={{ marginBottom: 12 }}
        />
      )}

      <div className="product-list-filters">
        <Input.Search
          allowClear
          value={searchDraft}
          placeholder="搜索发布名称或 Campaign ID"
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="发布状态"
          value={status}
          onChange={(value) => {
            setStatus(value)
            setPage(1)
          }}
          options={Object.entries(statusMeta)
            .filter(([key]) => ![
              'pending',
              'running',
              'awaiting_approval',
              'passed',
              'failed',
            ].includes(key))
            .map(([value, meta]) => ({ value, label: meta.label }))}
        />
      </div>

      {isMobile ? (
        <List
          className="product-mobile-list"
          loading={campaignsQuery.isLoading}
          dataSource={rows}
          locale={{ emptyText: <Empty description="暂无发布 Campaign" /> }}
          renderItem={(campaign) => (
            <List.Item>
              <div className="product-mobile-item">
                <div className="product-mobile-item__header">
                  <Typography.Text strong>{campaign.name}</Typography.Text>
                  {statusTag(campaign.status)}
                </div>
                <Typography.Text type="secondary">
                  {stageLabels[campaign.current_stage || ''] || '尚未冻结目标'}
                </Typography.Text>
                <div className="product-mobile-item__meta">
                  <span>目标 {campaign.target_manifest_json?.matrix?.length || 0}</span>
                  <span>{formatTime(campaign.updated_at)}</span>
                </div>
                <Button icon={<EyeOutlined />} onClick={() => setSelectedId(campaign.id)}>
                  查看详情
                </Button>
              </div>
            </List.Item>
          )}
        />
      ) : (
        <Table
          className="product-data-table"
          rowKey="id"
          loading={campaignsQuery.isLoading}
          dataSource={rows}
          pagination={false}
          columns={[
            {
              title: '发布 Campaign',
              render: (_: unknown, campaign: RolloutCampaign) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text strong>{campaign.name}</Typography.Text>
                  <Typography.Text type="secondary" copyable>
                    {campaign.id}
                  </Typography.Text>
                </Space>
              ),
            },
            {
              title: '冻结范围',
              width: 170,
              render: (_: unknown, campaign: RolloutCampaign) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text>
                    {campaign.target_manifest_json?.matrix?.length || 0} 个目标对
                  </Typography.Text>
                  <Typography.Text type="secondary">
                    {campaign.runtime_bundle_refs_json?.length || 0} 个运行包
                  </Typography.Text>
                </Space>
              ),
            },
            {
              title: '当前阶段',
              width: 160,
              render: (_: unknown, campaign: RolloutCampaign) => (
                <Space direction="vertical" size={2}>
                  {statusTag(campaign.status)}
                  <Typography.Text type="secondary">
                    {campaign.current_attempt
                      ? statusMeta[campaign.current_attempt.status]?.label
                      : '尚未开始'}
                  </Typography.Text>
                </Space>
              ),
            },
            {
              title: '更新时间',
              dataIndex: 'updated_at',
              width: 180,
              render: formatTime,
            },
            {
              title: '操作',
              width: 112,
              render: (_: unknown, campaign: RolloutCampaign) => (
                <Button
                  type="text"
                  icon={<EyeOutlined />}
                  onClick={() => setSelectedId(campaign.id)}
                >
                  查看
                </Button>
              ),
            },
          ]}
        />
      )}

      {(campaignsQuery.data?.total || 0) > 0 && (
        <Pagination
          current={page}
          pageSize={pageSize}
          total={campaignsQuery.data?.total || 0}
          showSizeChanger
          showTotal={(total) => `共 ${total} 个发布 Campaign`}
          onChange={(nextPage, nextPageSize) => {
            setPage(nextPageSize !== pageSize ? 1 : nextPage)
            setPageSize(nextPageSize)
          }}
        />
      )}

      <Drawer
        title="渐进发布详情"
        width={isMobile ? '100%' : 920}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(undefined)}
        extra={selected ? renderActions(selected) : null}
      >
        {detailQuery.isLoading || !selected ? (
          <Typography.Text type="secondary">正在加载发布证据…</Typography.Text>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {selected.last_error && (
              <Alert
                type="error"
                showIcon
                message="当前发布已被阻断"
                description={selected.last_error}
              />
            )}
            {selected.current_attempt?.status === 'running' && (
              <Alert
                type="info"
                showIcon
                message="阶段执行中"
                description="执行器正在生成指标、差异对账和证据引用；最终结果尚未形成。"
              />
            )}
            <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
              <Descriptions.Item label="名称">{selected.name}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusTag(selected.status)}</Descriptions.Item>
              <Descriptions.Item label="当前阶段">
                {stageLabels[selected.current_stage || ''] || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="目标对">{targetCount}</Descriptions.Item>
              <Descriptions.Item label="运行包">
                {selected.runtime_bundle_refs_json?.length || 0}
              </Descriptions.Item>
              <Descriptions.Item label="比较上下文">
                {selected.comparison_context_ids_json?.length || 0}
              </Descriptions.Item>
              <Descriptions.Item label="创建人">{selected.created_by}</Descriptions.Item>
              <Descriptions.Item label="冻结人">{selected.frozen_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{formatTime(selected.updated_at)}</Descriptions.Item>
              <Descriptions.Item label="目标摘要">
                <Typography.Text copyable={{ text: selected.target_manifest_digest || '' }}>
                  {shortDigest(selected.target_manifest_digest)}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="运行包摘要">
                <Typography.Text copyable={{ text: selected.runtime_bundle_digest || '' }}>
                  {shortDigest(selected.runtime_bundle_digest)}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="比较上下文摘要">
                <Typography.Text copyable={{ text: selected.comparison_context_digest || '' }}>
                  {shortDigest(selected.comparison_context_digest)}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>

            <div className="product-section-header">
              <div>
                <Typography.Title level={5}>发布阶段</Typography.Title>
                <Typography.Text type="secondary">
                  每次尝试冻结同一目标、资产、比较上下文和阈值
                </Typography.Text>
              </div>
            </div>
            <Steps
              responsive
              size="small"
              current={Math.max(0, selected.stage_sequence.indexOf(selected.current_stage || ''))}
              items={stageItems}
            />

            <div className="product-section-header">
              <div>
                <Typography.Title level={5}>阶段尝试与证据</Typography.Title>
                <Typography.Text type="secondary">
                  指标越界或摘要漂移会自动暂停并回到上一稳定路径
                </Typography.Text>
              </div>
            </div>
            <Table
              className="product-data-table"
              rowKey="id"
              size="small"
              dataSource={selected.attempts}
              pagination={false}
              scroll={{ x: 760 }}
              columns={[
                {
                  title: '阶段',
                  dataIndex: 'stage',
                  width: 130,
                  render: (value) => stageLabels[value] || value,
                },
                {
                  title: '尝试',
                  dataIndex: 'attempt_number',
                  width: 72,
                },
                {
                  title: '范围',
                  dataIndex: 'percentage',
                  width: 72,
                  render: (value, attempt: RolloutStageAttempt) => (
                    attempt.stage === 'shadow' ? '影子' : `${value}%`
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 110,
                  render: (value) => statusTag(value),
                },
                {
                  title: '证据',
                  width: 88,
                  render: (_: unknown, attempt: RolloutStageAttempt) => (
                    attempt.evidence_refs_json?.length || 0
                  ),
                },
                {
                  title: '阻断项',
                  render: (_: unknown, attempt: RolloutStageAttempt) => (
                    <Typography.Text type={attempt.blockers_json?.length ? 'danger' : 'secondary'}>
                      {attempt.blockers_json?.join('；') || '无'}
                    </Typography.Text>
                  ),
                },
                {
                  title: '批准人',
                  dataIndex: 'approved_by',
                  width: 130,
                  render: (value) => value || '-',
                },
              ]}
            />

            <div className="product-section-header">
              <div>
                <Typography.Title level={5}>阶段执行实例</Typography.Title>
                <Typography.Text type="secondary">
                  记录任务排队、Worker 心跳、同源 Snapshot 和不可变比较证据
                </Typography.Text>
              </div>
            </div>
            <Table
              className="product-data-table"
              rowKey="id"
              size="small"
              dataSource={selected.stage_executions || []}
              pagination={false}
              scroll={{ x: 980 }}
              locale={{ emptyText: '当前阶段尚未创建执行实例' }}
              columns={[
                {
                  title: '执行实例',
                  width: 190,
                  render: (_: unknown, execution: RolloutStageExecution) => (
                    <Typography.Text copyable={{ text: execution.id }}>
                      {shortDigest(execution.id)}
                    </Typography.Text>
                  ),
                },
                {
                  title: '模式',
                  dataIndex: 'execution_mode',
                  width: 110,
                  render: (value) => (
                    value === 'snapshot_replay' ? '同源回放' : '实时采集'
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 105,
                  render: (value) => statusTag(value),
                },
                {
                  title: 'Snapshot',
                  width: 92,
                  render: (_: unknown, execution: RolloutStageExecution) => (
                    execution.source_snapshot_ids_json?.length || 0
                  ),
                },
                {
                  title: '尝试',
                  dataIndex: 'attempt_count',
                  width: 72,
                },
                {
                  title: '最近心跳',
                  dataIndex: 'heartbeat_at',
                  width: 180,
                  render: formatTime,
                },
                {
                  title: '比较证据',
                  width: 150,
                  render: (_: unknown, execution: RolloutStageExecution) => (
                    execution.comparison_manifest_id
                      ? shortDigest(execution.comparison_manifest_id)
                      : '尚未形成'
                  ),
                },
                {
                  title: '失败原因',
                  render: (_: unknown, execution: RolloutStageExecution) => (
                    <Typography.Text type={execution.failure_code ? 'danger' : 'secondary'}>
                      {execution.failure_code || execution.error || '无'}
                    </Typography.Text>
                  ),
                },
                {
                  title: '操作',
                  width: 84,
                  fixed: 'right' as const,
                  render: (_: unknown, execution: RolloutStageExecution) => (
                    <Button
                      type="text"
                      icon={<EyeOutlined />}
                      aria-label={`查看执行实例 ${execution.id}`}
                      onClick={() => setSelectedExecutionId(execution.id)}
                    >
                      查看
                    </Button>
                  ),
                },
              ]}
            />

            <details>
              <summary>冻结目标与基线</summary>
              <pre className="product-code-panel">
                {JSON.stringify({
                  target_manifest: selected.target_manifest_json,
                  runtime_bundle_refs: selected.runtime_bundle_refs_json,
                  comparison_context_ids: selected.comparison_context_ids_json,
                  baseline_window: selected.baseline_window_json,
                  thresholds: selected.thresholds_json,
                  rollback_plan: selected.rollback_plan_json,
                }, null, 2)}
              </pre>
            </details>
          </Space>
        )}
      </Drawer>

      <Drawer
        title="阶段执行证据"
        width={isMobile ? '100%' : 760}
        open={Boolean(selectedExecutionId)}
        onClose={() => setSelectedExecutionId(undefined)}
      >
        {executionDetailQuery.isLoading || !executionDetailQuery.data ? (
          <Typography.Text type="secondary">正在读取执行证据…</Typography.Text>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="执行实例">
                <Typography.Text copyable>
                  {executionDetailQuery.data.id}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {statusTag(executionDetailQuery.data.status)}
              </Descriptions.Item>
              <Descriptions.Item label="执行模式">
                {executionDetailQuery.data.execution_mode === 'snapshot_replay'
                  ? '同源 Snapshot 回放'
                  : '实时采集'}
              </Descriptions.Item>
              <Descriptions.Item label="Worker">
                {executionDetailQuery.data.worker_owner || '-'}
              </Descriptions.Item>
              {executionDetailQuery.data.execution_mode === 'live_capture' && (
                <Descriptions.Item label="采集批次">
                  <Typography.Text copyable>
                    {executionDetailQuery.data.capture_batch_id || '尚未创建'}
                  </Typography.Text>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="尝试次数">
                {executionDetailQuery.data.attempt_count}
              </Descriptions.Item>
              <Descriptions.Item label="最近心跳">
                {formatTime(executionDetailQuery.data.heartbeat_at)}
              </Descriptions.Item>
              <Descriptions.Item label="源 Snapshot">
                {executionDetailQuery.data.source_snapshot_ids_json?.length || 0}
              </Descriptions.Item>
              <Descriptions.Item label="事实高水位">
                {formatTime(executionDetailQuery.data.fact_high_watermark)}
              </Descriptions.Item>
              <Descriptions.Item label="Snapshot 摘要">
                <Typography.Text copyable={{ text: executionDetailQuery.data.snapshot_set_digest }}>
                  {shortDigest(executionDetailQuery.data.snapshot_set_digest)}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="决策摘要">
                <Typography.Text copyable={{ text: executionDetailQuery.data.decision_digest || '' }}>
                  {shortDigest(executionDetailQuery.data.decision_digest)}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
            {executionDetailQuery.data.failure_code && (
              <Alert
                type="error"
                showIcon
                message={executionDetailQuery.data.failure_code}
                description={executionDetailQuery.data.error || '执行失败，未形成可信比较结论。'}
              />
            )}
            <details open>
              <summary>目标与 Snapshot 清单</summary>
              <pre className="product-code-panel">
                {JSON.stringify({
                  selection_manifest: executionDetailQuery.data.selection_manifest_json,
                  capture_manifest: executionDetailQuery.data.capture_manifest_json,
                  source_snapshot_ids: executionDetailQuery.data.source_snapshot_ids_json,
                }, null, 2)}
              </pre>
            </details>
            <details>
              <summary>双轨派生事实</summary>
              <pre className="product-code-panel">
                {JSON.stringify(executionDetailQuery.data.projections || [], null, 2)}
              </pre>
            </details>
            <details>
              <summary>不可变比较证据</summary>
              <pre className="product-code-panel">
                {JSON.stringify(executionDetailQuery.data.comparison_evidence || null, null, 2)}
              </pre>
            </details>
          </Space>
        )}
      </Drawer>

      <Modal
        title="新建发布 Campaign"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        okButtonProps={{
          className: 'product-primary-button',
          disabled: !createName.trim(),
          loading: createMutation.isLoading,
        }}
        onOk={() => createMutation.mutate()}
        onCancel={() => setCreateOpen(false)}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <label>
            <Typography.Text strong>名称</Typography.Text>
            <Input
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
              placeholder="例如：西安核心网络 CPU 首批发布"
            />
          </label>
          <label>
            <Typography.Text strong>说明</Typography.Text>
            <Input.TextArea
              value={createDescription}
              onChange={(event) => setCreateDescription(event.target.value)}
              rows={3}
            />
          </label>
        </Space>
      </Modal>

      <Drawer
        title="冻结发布目标"
        width={isMobile ? '100%' : 720}
        open={Boolean(freezeCampaign)}
        onClose={() => setFreezeCampaign(undefined)}
        extra={(
          <Button
            className="product-primary-button"
            icon={<CheckOutlined />}
            loading={freezeMutation.isLoading}
            disabled={
              !baselineStart
              || !baselineEnd
              || !rollbackEvidenceText.trim()
              || !freezeReason.trim()
            }
            onClick={() => freezeMutation.mutate()}
          >
            冻结
          </Button>
        )}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            message="冻结后不可在原 Campaign 内替换目标或资产"
          />
          <label>
            <Typography.Text strong>设备 × 检查项目标矩阵</Typography.Text>
            <Input.TextArea
              className="rollout-json-input"
              rows={10}
              value={targetManifestText}
              onChange={(event) => setTargetManifestText(event.target.value)}
            />
          </label>
          <label>
            <Typography.Text strong>Active RuntimeBundle 引用</Typography.Text>
            <Input.TextArea
              className="rollout-json-input"
              rows={6}
              value={runtimeRefsText}
              onChange={(event) => setRuntimeRefsText(event.target.value)}
            />
          </label>
          <label>
            <Typography.Text strong>ComparisonContext ID</Typography.Text>
            <Input.TextArea
              rows={4}
              value={contextIdsText}
              onChange={(event) => setContextIdsText(event.target.value)}
              placeholder="每行一个 ID"
            />
          </label>
          <div className="rollout-baseline-grid">
            <label>
              <Typography.Text strong>基线开始</Typography.Text>
              <Input
                type="datetime-local"
                value={baselineStart}
                onChange={(event) => setBaselineStart(event.target.value)}
              />
            </label>
            <label>
              <Typography.Text strong>基线结束</Typography.Text>
              <Input
                type="datetime-local"
                value={baselineEnd}
                onChange={(event) => setBaselineEnd(event.target.value)}
              />
            </label>
          </div>
          <label>
            <Typography.Text strong>阶段阈值</Typography.Text>
            <Input.TextArea
              className="rollout-json-input"
              rows={5}
              value={thresholdsText}
              onChange={(event) => setThresholdsText(event.target.value)}
            />
          </label>
          <label>
            <Typography.Text strong>回滚路径</Typography.Text>
            <Select
              value={rollbackMode}
              onChange={setRollbackMode}
              style={{ width: '100%' }}
              options={[
                { value: 'legacy_route', label: '上一稳定链路' },
                { value: 'runtime_bundle', label: '上一 RuntimeBundle' },
                { value: 'no_prior_production', label: '停止候选路由' },
              ]}
            />
          </label>
          <label>
            <Typography.Text strong>回滚演练证据</Typography.Text>
            <Input.TextArea
              rows={3}
              value={rollbackEvidenceText}
              onChange={(event) => setRollbackEvidenceText(event.target.value)}
              placeholder="每行一个不可变证据引用"
            />
          </label>
          <label>
            <Typography.Text strong>冻结理由</Typography.Text>
            <Input.TextArea
              rows={3}
              value={freezeReason}
              onChange={(event) => setFreezeReason(event.target.value)}
            />
          </label>
        </Space>
      </Drawer>

      <Modal
        title={lifecycleTitle}
        open={Boolean(lifecycleAction && lifecycleCampaign)}
        okText={lifecycleAction === 'approve' ? '批准' : '确认'}
        cancelText="取消"
        okButtonProps={{
          className: 'product-primary-button',
          danger: lifecycleAction === 'rollback',
          disabled: !lifecycleReason.trim()
            || (lifecycleAction === 'rollback' && !rollbackEvidence.trim()),
          loading: lifecycleMutation.isLoading,
        }}
        onOk={() => lifecycleMutation.mutate()}
        onCancel={() => {
          setLifecycleAction(undefined)
          setLifecycleCampaign(undefined)
        }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type={lifecycleAction === 'rollback' ? 'error' : 'warning'}
            showIcon
            message={lifecycleImpact}
          />
          {lifecycleAction === 'start' && (
            <label>
              <Typography.Text strong>证据获取方式</Typography.Text>
              <Segmented
                block
                value={executionMode}
                onChange={(value) => setExecutionMode(
                  value as 'snapshot_replay' | 'live_capture',
                )}
                options={[
                  {
                    label: '历史快照回放',
                    value: 'snapshot_replay',
                  },
                  {
                    label: '实时只读采集',
                    value: 'live_capture',
                  },
                ]}
              />
              <Typography.Text type="secondary">
                {executionMode === 'live_capture'
                  ? '按冻结设备和命令执行一次真实只读采集，同一批原始输出供新旧规则双轨解析。'
                  : '使用冻结时间窗内最近两个完整巡检周期，适合先做影子验证。'}
              </Typography.Text>
            </label>
          )}
          {lifecycleAction === 'rollback' && (
            <label>
              <Typography.Text strong>回滚证据</Typography.Text>
              <Input.TextArea
                rows={3}
                value={rollbackEvidence}
                onChange={(event) => setRollbackEvidence(event.target.value)}
                placeholder="每行一个不可变证据引用"
              />
            </label>
          )}
          <label>
            <Typography.Text strong>
              {lifecycleAction === 'approve' ? '审批意见' : '操作理由'}
            </Typography.Text>
            <Input.TextArea
              rows={4}
              value={lifecycleReason}
              onChange={(event) => setLifecycleReason(event.target.value)}
            />
          </label>
        </Space>
      </Modal>
    </div>
  )
}

export default RolloutCampaignWorkspace
