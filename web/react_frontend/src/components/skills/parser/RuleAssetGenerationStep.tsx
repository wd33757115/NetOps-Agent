import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckOutlined,
  EditOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useNavigate } from 'react-router-dom'

import {
  parserApi,
  ruleAssetSourceApi,
  type ParserRuleAssetBundle,
} from '../../../services/api'
import WorkflowRunMonitor from '../WorkflowRunMonitor'
import LifecycleActionDialog from './LifecycleActionDialog'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

interface RuleAssetGenerationStepProps {
  seedJobId?: string | null
  bundleId?: string | null
  datasetIds: string[]
  command: string
  offlineJobId?: string | null
  initialAction?: 'release'
  onChanged?: () => void
}

const asRecord = (value: unknown): Record<string, any> => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, any>
    : {}
)

const statusLabel: Record<string, string> = {
  draft: '等待生成',
  running: '生成中',
  validating: '验证中',
  pending_approval: '待工程复核',
  active: '已发布',
  rejected: '验证未通过',
  failed: '生成失败',
  deprecated: '已废弃',
}

const blockerLabel: Record<string, string> = {
  'historical replay contains no Change evidence': '历史回放没有 Change 证据',
  'historical replay is missing, failed or has collisions': '历史回放缺失、失败或存在规则碰撞',
  'immutable context has no replay_run_ids': '安全上下文没有绑定可回放的巡检 Run',
  'some immutable runs contain no Change evidence': '部分巡检 Run 没有产生 Change',
  'one or more child assets failed deterministic validation': '部分候选资产未通过确定性校验',
  'immutable sanitized context gate failed': '脱敏上下文未通过隐私或一致性门禁',
}

const apiError = (error: any) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || error?.message || '规则资产生成操作失败'
}

const RuleAssetGenerationStep: React.FC<RuleAssetGenerationStepProps> = ({
  seedJobId,
  bundleId,
  datasetIds,
  command,
  offlineJobId,
  initialAction,
  onChanged,
}) => {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [contextResult, setContextResult] = useState<Record<string, any>>()
  const [generationRunId, setGenerationRunId] = useState<string>()
  const [reviewBundle, setReviewBundle] = useState<ParserRuleAssetBundle | null>(null)
  const [approvalBundle, setApprovalBundle] = useState<ParserRuleAssetBundle | null>(null)
  const refreshedBundleRef = useRef('')
  const consumedInitialActionRef = useRef('')

  const previewQuery = useQuery(
    ['product-rule-generation-preview', seedJobId],
    () => parserApi.previewRuleGenerationJob(seedJobId || ''),
    {
      enabled: Boolean(seedJobId),
      refetchInterval: (data) => {
        const status = data?.job?.status
        return status && ['running', 'validating'].includes(status) ? 5000 : false
      },
    },
  )

  const job = previewQuery.data?.job
  const payload = asRecord(job?.payload_json)
  const storedContextId = String(
    contextResult?.context_id
    || payload.auto_context_id
    || payload.latest_generation_context_id
    || payload.context_id
    || '',
  )
  const latestRunId = String(
    generationRunId
    || payload.latest_generation_run_id
    || '',
  )
  const contextReady = Boolean(storedContextId)
  const jobStatus = String(job?.status || 'draft')
  // 仅凭 latest_generation_run_id 不能判定已启动：start-generation 会先写 run_id，
  // 但 seed 可能仍停留在 draft；此时必须继续展示可点击的生成按钮。
  const generationInFlight = ['running', 'validating'].includes(jobStatus)
  const generationStarted = generationInFlight
    || Boolean(jobStatus && !['draft', 'failed'].includes(jobStatus))
  const effectiveDatasetIds = useMemo(() => {
    if (datasetIds.length) return datasetIds
    const fromContext = contextResult?.dataset_ids
    if (Array.isArray(fromContext)) {
      return fromContext.map((item: unknown) => String(item || '').trim()).filter(Boolean)
    }
    const fromPayload = payload.dataset_ids || payload.candidate_dataset_ids
    if (Array.isArray(fromPayload)) {
      return fromPayload.map((item: unknown) => String(item || '').trim()).filter(Boolean)
    }
    return [] as string[]
  }, [contextResult?.dataset_ids, datasetIds, payload.candidate_dataset_ids, payload.dataset_ids])

  const bundleQuery = useQuery(
    [
      'product-rule-generation-bundle',
      job?.source_ref,
      storedContextId,
      latestRunId,
    ],
    () => parserApi.ruleAssetBundles({
      search: String(job?.source_ref || ''),
      limit: 50,
      offset: 0,
    }),
    {
      enabled: Boolean((generationStarted || Boolean(latestRunId)) && job?.source_ref),
      refetchInterval: (data) => {
        const items = data?.items || []
        if (!items.length) return 3000
        return items.some((item) => ['draft', 'running', 'validating'].includes(item.status))
          ? 3000
          : false
      },
    },
  )
  const focusedBundleQuery = useQuery(
    ['product-rule-generation-bundle-by-id', bundleId],
    () => parserApi.ruleAssetBundle(bundleId || ''),
    {
      enabled: Boolean(bundleId),
      staleTime: 0,
    },
  )
  const discoveredBundle = useMemo(() => {
    const items = bundleQuery.data?.items || []
    if (!items.length) return undefined
    if (storedContextId) {
      const exactContext = items.find((item) => item.context_id === storedContextId)
      if (exactContext) return exactContext
    }
    if (offlineJobId) {
      const exactSource = items.find((item) => (
        item.offline_job_id === offlineJobId
        || item.offline_job_ids?.includes(offlineJobId)
      ))
      if (exactSource) return exactSource
    }
    return items.find((item) => item.source_ref === job?.source_ref) || items[0]
  }, [
    bundleQuery.data?.items,
    job?.source_ref,
    offlineJobId,
    storedContextId,
  ])
  const bundle = focusedBundleQuery.data || discoveredBundle
  const effectiveStatus = bundle?.status || job?.status || 'draft'
  const generationFinished = Boolean(
    bundle && !['draft', 'running', 'validating'].includes(bundle.status),
  )
  const candidateReady = Boolean(
    bundle && ['pending_approval', 'active'].includes(bundle.status),
  )
  const canLaunchGeneration = contextReady && !generationInFlight && !candidateReady
  const recordCount = Number(
    contextResult?.quality?.record_count
    || contextResult?.privacy?.sampled_record_count
    || asRecord(job?.validation_report_json).auto_context?.sampled_record_count
    || 0,
  )

  const refresh = async () => {
    await Promise.allSettled([
      previewQuery.refetch(),
      bundleQuery.refetch(),
      bundleId ? focusedBundleQuery.refetch() : Promise.resolve(),
      offlineJobId
        ? ruleAssetSourceApi.refreshCommandAssetUnits(offlineJobId)
        : Promise.resolve(),
    ])
    await queryClient.invalidateQueries('rule-asset-production-units')
    await queryClient.invalidateQueries('rule-asset-production-unit')
    onChanged?.()
  }

  useEffect(() => {
    if (!bundle?.id || !offlineJobId || refreshedBundleRef.current === bundle.id) return
    refreshedBundleRef.current = bundle.id
    void ruleAssetSourceApi.refreshCommandAssetUnits(offlineJobId)
      .then(async () => {
        await queryClient.invalidateQueries('rule-asset-production-units')
        await queryClient.invalidateQueries('rule-asset-production-unit')
        onChanged?.()
      })
      .catch(() => {
        refreshedBundleRef.current = ''
      })
  }, [bundle?.id, offlineJobId, onChanged, queryClient])

  const contextMutation = useMutation(
    () => parserApi.autoBuildRuleGenerationContext(seedJobId || '', effectiveDatasetIds),
    {
      onSuccess: async (result: any) => {
        setContextResult(asRecord(result))
        message.success('安全上下文已构建，可以启动规则资产生成')
        await refresh()
      },
      onError: (error: any) => message.error(apiError(error)),
    },
  )

  const generationMutation = useMutation(
    () => parserApi.startRuleAssetGeneration(seedJobId || '', {
      context_id: storedContextId || undefined,
      dataset_ids: storedContextId ? undefined : effectiveDatasetIds,
    }),
    {
      onSuccess: async (result: any) => {
        const runId = String(result?.run_id || result?.workflow_run_id || '')
        setGenerationRunId(runId || undefined)
        message.success('规则资产生成 Workflow 已启动')
        await refresh()
      },
      onError: (error: any) => message.error(apiError(error)),
    },
  )
  const approvalMutation = useMutation(
    ({ id, reason }: { id: string; reason: string }) => (
      parserApi.approveRuleGenerationJob(id, { reason })
    ),
    {
      onSuccess: async () => {
        message.success('规则资产包已原子发布')
        setApprovalBundle(null)
        await refresh()
      },
      onError: (error: any) => message.error(apiError(error)),
    },
  )

  useEffect(() => {
    if (initialAction !== 'release' || !bundle?.id) return
    if (consumedInitialActionRef.current === bundle.id) return
    consumedInitialActionRef.current = bundle.id
    if (bundle.status === 'pending_approval') {
      setApprovalBundle(bundle)
    }
  }, [bundle, initialAction])

  const steps = useMemo(() => ([
    {
      title: 'Parser 已发布',
      description: '以当前 Active Parser 的结构化结果作为规则输入。',
      status: seedJobId ? 'finish' as const : 'error' as const,
    },
    {
      title: '安全上下文',
      description: contextReady
        ? `已从离线数据构建不可变上下文${recordCount ? `，包含 ${recordCount} 条脱敏记录` : ''}。`
        : '从所选离线数据集提取 structured_json，不读取或发送原始 CLI。',
      status: contextReady ? 'finish' as const : 'process' as const,
    },
    {
      title: '生成与确定性验证',
      description: generationFinished
        ? 'Contract、Policy 与 Event Rule 候选已经形成，静态验证和历史回放已经结束。'
        : generationStarted
        ? 'LLM 生成 Contract、Policy、Event Rule，并执行静态验证和历史回放。'
        : '上下文通过隐私门禁后才允许启动。',
      status: generationFinished
        ? 'finish' as const
        : generationStarted
          ? 'process' as const
          : 'wait' as const,
    },
    {
      title: '工程复核与发布',
      description: bundle?.status === 'rejected'
        ? '候选已保留，但发布门禁未通过。请补充证据或修订不合理的规则后重新回放。'
        : candidateReady
        ? '候选已形成，请在命令资产的规则资产页签中复核。'
        : '候选生成后由工程师修订，审批通过后才进入 Active runtime。',
      status: bundle?.status === 'active'
        ? 'finish' as const
        : bundle?.status === 'rejected'
          ? 'error' as const
          : candidateReady
            ? 'process' as const
            : 'wait' as const,
    },
  ]), [
    bundle?.status,
    candidateReady,
    contextReady,
    generationFinished,
    generationStarted,
    recordCount,
    seedJobId,
  ])

  if (!seedJobId) {
    return (
      <Alert
        type="warning"
        showIcon
        message="尚未创建规则生成种子"
        description="请先确认 Active Parser 与 Observation Contract 语义，系统会幂等创建生成种子。"
      />
    )
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className="rule-generation-step__header">
        <div>
          <Typography.Title level={5}>规则资产生成</Typography.Title>
          <Typography.Text type="secondary">
            {command} · 从离线结构化数据生成 Contract、Policy 与 Event Rule
          </Typography.Text>
        </div>
        <Space>
          <Tag className={`product-status-tag ${
            effectiveStatus === 'active'
              ? 'is-published'
              : ['failed', 'rejected'].includes(effectiveStatus)
                ? 'is-failed'
                : ['running', 'validating'].includes(effectiveStatus)
                  ? 'is-running'
                  : 'is-review'
          }`}>
            {statusLabel[effectiveStatus] || effectiveStatus || '读取中'}
          </Tag>
          <Button
            aria-label="刷新规则生成状态"
            icon={<ReloadOutlined />}
            loading={previewQuery.isFetching}
            onClick={() => refresh()}
          />
        </Space>
      </div>

      <Steps direction="vertical" size="small" items={steps} />

      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="来源样本">
          {offlineJobId || '-'}
        </Descriptions.Item>
        <Descriptions.Item label="数据集">
          {effectiveDatasetIds.length
            ? effectiveDatasetIds.join('、')
            : (contextReady ? '已绑定安全上下文' : '尚未选择')}
        </Descriptions.Item>
        <Descriptions.Item label="隐私边界">
          <Space>
            <SafetyCertificateOutlined />
            仅使用 structured_json，禁止 raw CLI、真实设备名和管理 IP
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="生成种子">
          <Typography.Text copyable>{seedJobId}</Typography.Text>
        </Descriptions.Item>
        {storedContextId && (
          <Descriptions.Item label="上下文">
            <Typography.Text copyable>{storedContextId}</Typography.Text>
          </Descriptions.Item>
        )}
        {bundle?.id && (
          <Descriptions.Item label="候选规则包">
            <Typography.Text copyable>{bundle.id}</Typography.Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {!effectiveDatasetIds.length && !contextReady && (
        <Alert
          type="error"
          showIcon
          message="缺少离线数据集"
          description="请从规则样本批次进入该命令资产，或重新选择包含结构化记录的数据集。"
        />
      )}

      {bundle && (
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
          <Descriptions.Item label="观测契约">
            {bundle.asset_counts.OBSERVATION_CONTRACT || 0}
          </Descriptions.Item>
          <Descriptions.Item label="变化策略">
            {bundle.asset_counts.CHANGE_POLICY || 0}
          </Descriptions.Item>
          <Descriptions.Item label="事件规则">
            {bundle.asset_counts.EVENT_RULE || 0}
          </Descriptions.Item>
          <Descriptions.Item label="历史回放">
            命中 {bundle.replay.matched_change_count ?? 0} ·
            碰撞 {bundle.replay.collision_count ?? 0}
          </Descriptions.Item>
        </Descriptions>
      )}

      {bundle?.status === 'rejected' && (
        <Alert
          type="warning"
          showIcon
          message="候选已生成，但发布证据不足"
          description={(
            <Space direction="vertical" size={4}>
              <Typography.Text>
                当前离线任务只有一个采集周期，能够验证 Parser，却无法产生可用于
                Change/Event 回放的跨周期变化。系统已保留候选，但禁止进入生产。
              </Typography.Text>
              {bundle.blockers.length > 0 && (
                <Typography.Text type="secondary">
                  门禁原因：{[...new Set(bundle.blockers.map(
                    (item) => blockerLabel[item] || item,
                  ))].join('；')}
                </Typography.Text>
              )}
            </Space>
          )}
          action={(
            <Space wrap>
              <Button
                icon={<UploadOutlined />}
                onClick={() => navigate('/skills/parser?workspace=production&subtab=source-jobs')}
              >
                补充对比周期
              </Button>
              <Button
                icon={<EditOutlined />}
                onClick={() => setReviewBundle(bundle)}
              >
                工程复核与修订
              </Button>
            </Space>
          )}
        />
      )}

      {bundle?.status === 'pending_approval' && (
        <Alert
          type="success"
          showIcon
          message="候选规则包已通过自动门禁"
          description="请核对字段语义、变化策略和事件触发条件；工程复核通过后才能进入发布审批。"
          action={(
            <Space wrap>
              <Button icon={<EditOutlined />} onClick={() => setReviewBundle(bundle)}>
                工程复核
              </Button>
              <Button
                className="product-primary-button"
                icon={<CheckOutlined />}
                disabled={!bundle.release_ready}
                onClick={() => setApprovalBundle(bundle)}
              >
                审批发布
              </Button>
            </Space>
          )}
        />
      )}

      <Space wrap>
        {!contextReady && (
          <Button
            className="product-primary-button"
            icon={<SafetyCertificateOutlined />}
            disabled={!effectiveDatasetIds.length}
            loading={contextMutation.isLoading}
            onClick={() => contextMutation.mutate()}
          >
            构建安全上下文
          </Button>
        )}
        {canLaunchGeneration && (
          <Button
            className="product-primary-button"
            icon={<PlayCircleOutlined />}
            loading={generationMutation.isLoading}
            onClick={() => generationMutation.mutate()}
          >
            {latestRunId ? '重新生成完整规则资产包' : '生成完整规则资产包'}
          </Button>
        )}
      </Space>

      {latestRunId && (
        <div className="rule-generation-step__workflow">
          <Typography.Title level={5}>生成 Workflow</Typography.Title>
          <WorkflowRunMonitor
            runId={latestRunId}
            plain
            subjectLabel="规则资产生成"
          />
        </div>
      )}

      {previewQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="规则生成状态读取失败"
          description={apiError(previewQuery.error)}
        />
      )}
      {(bundleQuery.isError || focusedBundleQuery.isError) && (
        <Alert
          type="error"
          showIcon
          message="候选规则包状态读取失败"
          description={apiError(focusedBundleQuery.error || bundleQuery.error)}
        />
      )}

      <RuleAssetEngineeringReviewDrawer
        open={Boolean(reviewBundle)}
        bundle={reviewBundle}
        onClose={() => setReviewBundle(null)}
        onChanged={async () => {
          await refresh()
          setReviewBundle(null)
        }}
      />
      <LifecycleActionDialog
        open={Boolean(approvalBundle)}
        title="审批并发布规则资产包"
        actionLabel="审批发布"
        resourceName={
          approvalBundle?.semantic_label
          || approvalBundle?.observation_type
          || approvalBundle?.id
          || '规则资产包'
        }
        impact="Observation Contract、Change Policy 和全部 Event Rule 将在同一事务内激活；任一步失败都会整体回滚。"
        blockers={approvalBundle?.release_ready ? [] : approvalBundle?.blockers || []}
        loading={approvalMutation.isLoading}
        onCancel={() => setApprovalBundle(null)}
        onConfirm={(reason) => approvalBundle && approvalMutation.mutate({
          id: approvalBundle.id,
          reason,
        })}
      />
    </Space>
  )
}

export default RuleAssetGenerationStep
