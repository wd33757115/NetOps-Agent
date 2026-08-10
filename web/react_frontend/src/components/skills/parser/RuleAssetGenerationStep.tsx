import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Space,
  Spin,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  EditOutlined,
  HistoryOutlined,
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
  workflowApi,
  type ParserAgentLoopAttempt,
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

const workflowFailureMessage = (raw: unknown) => {
  const text = String(raw || '').trim()
  if (!text) return '异步生成流程已结束，但没有返回可读的失败原因。'
  const jsonStart = text.indexOf('{')
  if (jsonStart >= 0) {
    try {
      const payload = JSON.parse(text.slice(jsonStart))
      const errors = payload?.validation?.errors || payload?.errors
      if (Array.isArray(errors) && errors.length) {
        return errors.slice(0, 3).map((item: unknown) => String(item)).join('；')
      }
      if (payload?.message) return String(payload.message)
      if (payload?.error) return String(payload.error)
    } catch {
      // Fall back to the workflow error text when a worker returned non-JSON output.
    }
  }
  return text.length > 600 ? `${text.slice(0, 600)}...` : text
}

const assetTypeLabel: Record<string, string> = {
  OBSERVATION_CONTRACT: 'Observation Contract',
  CHANGE_POLICY: 'Change Policy',
  EVENT_RULE: 'Current State / Event Rule',
}

const resultStatusLabel: Record<string, string> = {
  passed: '通过',
  ready: '待处理',
  blocked: '阻断',
  skipped: '不需要',
}

const jsonText = (value: unknown) => JSON.stringify(value ?? {}, null, 2)

const RuleAssetAttemptHistory: React.FC<{
  attempts: ParserAgentLoopAttempt[]
  loading: boolean
}> = ({ attempts, loading }) => {
  if (loading) {
    return <div style={{ display: 'grid', minHeight: 160, placeItems: 'center' }}><Spin /></div>
  }
  if (!attempts.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未产生模型生成轮次" />
  }
  return (
    <Collapse
      items={attempts.map((attempt) => ({
        key: attempt.id,
        label: (
          <Space wrap>
            <Typography.Text strong>
              {assetTypeLabel[String(attempt.asset_type || '')] || attempt.asset_type || attempt.stage}
              {' '}· 第 {attempt.attempt} 轮
            </Typography.Text>
            <Tag color={attempt.success ? 'green' : 'gold'}>
              {attempt.success ? '校验通过' : '需要修订'}
            </Tag>
            {attempt.generator && <Tag>{attempt.generator}</Tag>}
          </Space>
        ),
        children: (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="阶段">{attempt.stage || '-'}</Descriptions.Item>
              <Descriptions.Item label="下一步">{attempt.next_action || '-'}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{attempt.updated_at || '-'}</Descriptions.Item>
            </Descriptions>
            <Typography.Text strong>候选摘要</Typography.Text>
            <pre className="product-code-panel">{jsonText(attempt.output_summary)}</pre>
            <Typography.Text strong>本地验证反馈</Typography.Text>
            <pre className="product-code-panel">{jsonText(attempt.validator_feedback)}</pre>
            {attempt.revision_request && (
              <>
                <Typography.Text strong>修订请求</Typography.Text>
                <pre className="product-code-panel">{jsonText(attempt.revision_request)}</pre>
              </>
            )}
          </Space>
        ),
      }))}
    />
  )
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
  const [selectedPipelineStep, setSelectedPipelineStep] = useState<number | null>(null)
  const [followLatestBundle, setFollowLatestBundle] = useState(false)
  const [generationPending, setGenerationPending] = useState(false)
  const [generationLaunchError, setGenerationLaunchError] = useState('')
  const refreshedBundleRef = useRef('')
  const consumedInitialActionRef = useRef('')

  useEffect(() => {
    setFollowLatestBundle(false)
    setGenerationPending(false)
    setGenerationLaunchError('')
  }, [bundleId, seedJobId])

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
  const generationRunQuery = useQuery(
    ['product-rule-generation-workflow-run', latestRunId],
    () => workflowApi.getRun(latestRunId),
    {
      enabled: Boolean(latestRunId),
      refetchInterval: (data) => (
        ['pending', 'queued', 'running'].includes(String(data?.status || ''))
          ? 3000
          : false
      ),
    },
  )
  const contextReady = Boolean(storedContextId)
  const jobStatus = String(job?.status || 'draft')
  // 仅凭 latest_generation_run_id 不能判定已启动：start-generation 会先写 run_id，
  // 但 seed 可能仍停留在 draft；此时必须继续展示可点击的生成按钮。
  const workflowRunStatus = String(generationRunQuery.data?.status || '')
  const generationInFlight = generationPending
    || ['running', 'validating'].includes(jobStatus)
    || ['pending', 'queued', 'running'].includes(workflowRunStatus)
  const generationStarted = generationInFlight
    || Boolean(workflowRunStatus && workflowRunStatus !== 'failed')
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
    const latestBundleId = String(payload.latest_bundle_id || '')
    if (latestBundleId) {
      const latestBundle = items.find((item) => item.id === latestBundleId)
      if (latestBundle) return latestBundle
    }
    if (storedContextId) {
      const exactContext = items.find((item) => item.context_id === storedContextId)
      if (exactContext) return exactContext
      // A fresh immutable context represents a new generation revision. Do not
      // fall back to the previously published bundle while the replacement is
      // still being generated, otherwise the UI appears to ignore the action.
      return undefined
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
    payload.latest_bundle_id,
    storedContextId,
  ])
  const focusedBundle = focusedBundleQuery.data
  const bundle = !followLatestBundle && focusedBundle
    ? focusedBundle
    : discoveredBundle
  const attemptsQuery = useQuery(
    ['product-rule-generation-attempts', bundle?.id],
    () => parserApi.agentLoopAttempts({
      search: bundle?.id || '',
      limit: 200,
      offset: 0,
    }),
    {
      enabled: Boolean(bundle?.id),
      refetchInterval: generationInFlight ? 3000 : false,
    },
  )
  const engineeringDraftQuery = useQuery(
    ['product-rule-generation-engineering-draft', bundle?.id],
    () => parserApi.ruleAssetEngineeringDraft(bundle?.id || ''),
    {
      enabled: Boolean(
        bundle?.id
        && ['pending_approval', 'rejected', 'active'].includes(bundle.status),
      ),
      staleTime: 0,
    },
  )
  const effectiveStatus = bundle?.status || job?.status || 'draft'
  const generationFinished = Boolean(
    bundle && !['draft', 'running', 'validating'].includes(bundle.status),
  )
  const candidateReady = Boolean(
    bundle && ['pending_approval', 'active'].includes(bundle.status),
  )
  const canLaunchGeneration = contextReady
    && !generationInFlight
  const launchCreatesRevision = candidateReady
  const generationFailure = workflowRunStatus === 'failed'
    ? workflowFailureMessage((generationRunQuery.data as any)?.error_message)
    : ''
  const recordCount = Number(
    contextResult?.quality?.record_count
    || contextResult?.privacy?.sampled_record_count
    || asRecord(job?.validation_report_json).auto_context?.sampled_record_count
    || 0,
  )
  const engineeringReviewAccepted = bundle?.engineering_review?.decision === 'accepted'
  const replayRequired = bundle?.asset_requirements?.requires_historical_replay !== false
  const releaseAssetLabels = bundle
    ? [
        bundle.asset_counts.OBSERVATION_CONTRACT ? 'Observation Contract' : '',
        bundle.asset_counts.CHANGE_POLICY ? 'Change Policy' : '',
        bundle.asset_counts.EVENT_RULE ? 'Current State / Event Rule' : '',
      ].filter(Boolean)
    : []
  const releaseImpact = `${releaseAssetLabels.join('、') || '候选规则资产'}将在同一事务内激活；任一步失败都会整体回滚。`
  const pipelineCurrent = bundle?.status === 'active' || bundle?.release_ready
    ? 5
    : bundle?.status === 'pending_approval'
      ? 4
      : bundle && ['rejected', 'failed'].includes(bundle.status)
        ? 3
        : generationStarted
          ? generationFinished ? 3 : 2
          : contextReady
            ? 2
            : 1
  const visiblePipelineStep = selectedPipelineStep ?? pipelineCurrent

  const refresh = async () => {
    await Promise.allSettled([
      previewQuery.refetch(),
      bundleQuery.refetch(),
      bundleId ? focusedBundleQuery.refetch() : Promise.resolve(),
      bundle?.id ? attemptsQuery.refetch() : Promise.resolve(),
      bundle?.id && ['pending_approval', 'rejected', 'active'].includes(bundle.status)
        ? engineeringDraftQuery.refetch()
        : Promise.resolve(),
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
    () => {
      setGenerationPending(true)
      setGenerationLaunchError('')
      return parserApi.startRuleAssetGeneration(seedJobId || '', {
        context_id: storedContextId || undefined,
        dataset_ids: storedContextId ? undefined : effectiveDatasetIds,
      })
    },
    {
      onSuccess: async (result: any) => {
        setGenerationLaunchError('')
        const runId = String(result?.run_id || result?.workflow_run_id || '')
        setGenerationRunId(runId || undefined)
        setFollowLatestBundle(true)
        message.success('规则资产生成 Workflow 已启动')
        await refresh()
      },
      onError: (error: any) => {
        setGenerationPending(false)
        const detail = apiError(error)
        setGenerationLaunchError(detail)
        message.error(detail)
      },
    },
  )
  const regenerationMutation = useMutation(
    async () => {
      setGenerationPending(true)
      setGenerationLaunchError('')
      const prepared = await parserApi.autoBuildRuleGenerationContext(
        seedJobId || '',
        effectiveDatasetIds,
      )
      const nextContextId = String(prepared?.context_id || '')
      if (!nextContextId) {
        throw new Error('重新构建安全上下文后未返回 context_id')
      }
      const started = await parserApi.startRuleAssetGeneration(seedJobId || '', {
        context_id: nextContextId,
      })
      return { prepared, started }
    },
    {
      onSuccess: async ({ prepared, started }: any) => {
        setGenerationLaunchError('')
        setContextResult(asRecord(prepared))
        const runId = String(started?.run_id || started?.workflow_run_id || '')
        setGenerationRunId(runId || undefined)
        setFollowLatestBundle(true)
        setSelectedPipelineStep(2)
        message.success('已冻结最新样本并启动规则资产修订生成')
        await refresh()
      },
      onError: (error: any) => {
        setGenerationPending(false)
        const detail = apiError(error)
        setGenerationLaunchError(detail)
        message.error(detail)
      },
    },
  )

  useEffect(() => {
    if (!generationPending || !workflowRunStatus) return
    if (!['pending', 'queued', 'running'].includes(workflowRunStatus)) {
      setGenerationPending(false)
    }
  }, [generationPending, workflowRunStatus])
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

  const pipelineSteps = useMemo(() => ([
    {
      title: '来源与 Parser',
      description: '冻结离线来源和 Active Parser。',
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
      title: '候选生成',
      description: generationFinished
        ? '所需规则候选已经形成。'
        : generationStarted
        ? '正在生成所需 Contract、Policy 与 Rule。'
        : '上下文通过隐私门禁后才允许启动。',
      status: generationFinished
        ? 'finish' as const
        : generationStarted
          ? 'process' as const
          : 'wait' as const,
    },
    {
      title: replayRequired ? '验证与回放' : '确定性验证',
      description: bundle?.status === 'rejected'
        ? replayRequired ? '候选已保留，验证或历史回放需要处理。' : '候选已保留，确定性验证需要处理。'
        : generationFinished
          ? replayRequired ? '客观验证与历史回放已经结束。' : '客观确定性验证已经结束。'
          : replayRequired ? '候选形成后执行静态校验和历史回放。' : '候选形成后执行确定性校验。',
      status: bundle?.status === 'rejected'
        ? 'error' as const
        : generationFinished
          ? 'finish' as const
          : generationStarted
            ? 'process' as const
            : 'wait' as const,
    },
    {
      title: '工程复核',
      description: engineeringReviewAccepted
        ? '工程师已确认语义与回放证据。'
        : candidateReady || bundle?.status === 'rejected'
          ? '工程师可以查看、修订并重新回放候选。'
          : '候选验证结束后进入工程复核。',
      status: engineeringReviewAccepted
        ? 'finish' as const
        : candidateReady || bundle?.status === 'rejected'
            ? 'process' as const
            : 'wait' as const,
    },
    {
      title: '发布审批',
      description: bundle?.status === 'active'
        ? '规则资产包已原子发布。'
        : bundle?.release_ready
          ? '等待独立审批人确认生产作用域。'
          : '工程复核通过后才允许独立发布。',
      status: bundle?.status === 'active'
        ? 'finish' as const
        : bundle?.release_ready
          ? 'process' as const
          : 'wait' as const,
    },
  ]), [
    bundle?.status,
    bundle?.release_ready,
    candidateReady,
    contextReady,
    engineeringReviewAccepted,
    generationFinished,
    generationStarted,
    recordCount,
    replayRequired,
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

  const contextPrivacy = bundle?.context_gate?.privacy || asRecord(contextResult?.privacy)
  const attemptItems = attemptsQuery.data?.items || []
  const engineeringDraft = engineeringDraftQuery.data
  const pipelineStepContent = [
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>冻结来源与 Parser</Typography.Text>
        <Typography.Text type="secondary">
          规则生成只读取这个离线来源经 Active Parser 形成的结构化结果，后续步骤不得重新扫描生产数据。
        </Typography.Text>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="命令">{command || '-'}</Descriptions.Item>
          <Descriptions.Item label="离线来源">{offlineJobId || '-'}</Descriptions.Item>
          <Descriptions.Item label="数据集">
            {effectiveDatasetIds.length ? effectiveDatasetIds.join('、') : '尚未绑定'}
          </Descriptions.Item>
          <Descriptions.Item label="生成种子">
            <Typography.Text copyable>{seedJobId}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Parser 来源">
            {String(job?.source_ref || '已由生成种子冻结')}
          </Descriptions.Item>
        </Descriptions>
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>不可变安全上下文</Typography.Text>
        <Typography.Text type="secondary">
          这里只展示脱敏与来源完整性结果；模型输入仅使用 structured_json，禁止包含 raw CLI、真实设备名和管理 IP。
        </Typography.Text>
        {contextReady ? (
          <>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="上下文">
              <Typography.Text copyable>{storedContextId}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="摘要">
              <Typography.Text copyable>
                {String(bundle?.context_digest || contextResult?.context_digest || '-')}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="结构化记录">
              {contextPrivacy.sampled_record_count ?? recordCount ?? 0}
            </Descriptions.Item>
            <Descriptions.Item label="来源 Snapshot">
              {contextPrivacy.source_snapshot_count ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Raw CLI">
              <Tag color={contextPrivacy.raw_cli_included ? 'red' : 'green'}>
                {contextPrivacy.raw_cli_included ? '错误：已包含' : '未包含'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="来源范围">
              {String(contextPrivacy.scope_type || '-')}
            </Descriptions.Item>
          </Descriptions>
          <Button
            icon={<ReloadOutlined />}
            disabled={!effectiveDatasetIds.length}
            loading={contextMutation.isLoading}
            onClick={() => contextMutation.mutate()}
          >
            重新构建安全上下文
          </Button>
          </>
        ) : (
          <Alert
            type="info"
            showIcon
            message="尚未构建安全上下文"
            description="选择离线数据集后构建；该动作只冻结 structured_json 和脱敏后的 canonical 元数据。"
            action={(
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
          />
        )}
        {bundle?.context_gate?.errors?.length ? (
          <Alert
            type="error"
            showIcon
            message="安全上下文未通过"
            description={bundle.context_gate.errors.join('；')}
          />
        ) : null}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {(generationLaunchError || generationFailure) && (
          <Alert
            type="error"
            showIcon
            message="最近一次规则资产生成失败"
            description={generationLaunchError || generationFailure}
          />
        )}
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>候选生成历史</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              每轮模型输入摘要、候选摘要、本地验证反馈和修订请求都独立保存，可随时回看。
            </Typography.Text>
          </div>
          {canLaunchGeneration && (
            <Button
              className="product-primary-button"
              icon={<PlayCircleOutlined />}
              disabled={launchCreatesRevision && !effectiveDatasetIds.length}
              loading={generationMutation.isLoading || regenerationMutation.isLoading}
              onClick={() => (
                launchCreatesRevision
                  ? regenerationMutation.mutate()
                  : generationMutation.mutate()
              )}
            >
              {launchCreatesRevision
                ? '基于当前样本生成修订候选'
                : latestRunId
                  ? '重新生成完整规则资产包'
                  : '生成完整规则资产包'}
            </Button>
          )}
        </div>
        {contextReady && !generationStarted && (
          <Alert
            type="info"
            showIcon
            message="已绑定安全上下文"
            description="可以启动所需规则资产的候选生成。"
          />
        )}
        <RuleAssetAttemptHistory attempts={attemptItems} loading={attemptsQuery.isLoading} />
        {engineeringDraftQuery.isLoading ? (
          <div style={{ display: 'grid', minHeight: 120, placeItems: 'center' }}><Spin /></div>
        ) : engineeringDraft ? (
          <>
            <Typography.Text strong>当前候选资产内容</Typography.Text>
            <Collapse
              items={[
                {
                  key: 'contract',
                  label: `Observation Contract (${engineeringDraft.contract ? 1 : 0})`,
                  children: <pre className="product-code-panel">{jsonText(engineeringDraft.contract)}</pre>,
                },
                {
                  key: 'policies',
                  label: `Change Policy (${engineeringDraft.change_policies.length})`,
                  children: <pre className="product-code-panel">{jsonText(engineeringDraft.change_policies)}</pre>,
                },
                {
                  key: 'rules',
                  label: `Current State / Event Rule (${engineeringDraft.event_rules.length})`,
                  children: <pre className="product-code-panel">{jsonText(engineeringDraft.event_rules)}</pre>,
                },
              ]}
            />
          </>
        ) : bundle ? (
          <Alert
            type="warning"
            showIcon
            message="当前候选内容读取失败"
            description={engineeringDraftQuery.isError ? apiError(engineeringDraftQuery.error) : '候选尚未持久化。'}
          />
        ) : null}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>{replayRequired ? '客观验证与历史回放' : '客观确定性验证'}</Typography.Text>
        {bundle ? (
          <>
            <Descriptions bordered size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
              <Descriptions.Item label="观测契约">
                {bundle.asset_counts.OBSERVATION_CONTRACT || 0}
              </Descriptions.Item>
              <Descriptions.Item label="变化策略">
                {bundle.asset_counts.CHANGE_POLICY || 0}
              </Descriptions.Item>
              <Descriptions.Item label="当前状态 / 事件规则">
                {bundle.asset_counts.EVENT_RULE || 0}
              </Descriptions.Item>
              <Descriptions.Item label="历史回放">
                {replayRequired
                  ? `命中 ${bundle.replay.matched_change_count ?? 0} · 碰撞 ${bundle.replay.collision_count ?? 0}`
                  : '不需要（该资产模式不依赖双周期 Change）'}
              </Descriptions.Item>
            </Descriptions>
            <Collapse
              items={(bundle.steps || []).map((step) => ({
                key: step.key,
                label: (
                  <Space wrap>
                    <Typography.Text strong>{step.label}</Typography.Text>
                    <Tag color={step.status === 'passed' ? 'green' : step.status === 'blocked' ? 'red' : undefined}>
                      {resultStatusLabel[step.status] || step.status}
                    </Tag>
                  </Space>
                ),
                children: <pre className="product-code-panel">{jsonText(step.detail)}</pre>,
              }))}
            />
            {bundle.status === 'rejected' && (
              <Alert
                type="warning"
                showIcon
                message="候选已生成，但发布证据不足"
                description={(
                  <Space direction="vertical" size={4}>
                    <Typography.Text>
                      当前证据不足以证明 Change/Event 在目标范围内可靠运行；候选会保留供补样本或工程修订。
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
                    <Button icon={<EditOutlined />} onClick={() => setReviewBundle(bundle)}>
                      工程复核与修订
                    </Button>
                  </Space>
                )}
              />
            )}
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="候选形成后显示验证和回放结果" />
        )}
        {latestRunId && (
          <div className="rule-generation-step__workflow">
            <Typography.Title level={5}>生成 Workflow</Typography.Title>
            <WorkflowRunMonitor runId={latestRunId} plain subjectLabel="规则资产生成" />
          </div>
        )}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>工程师语义复核</Typography.Text>
        {bundle ? (
          <>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="候选规则包">
                <Typography.Text copyable>{bundle.id}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="复核结论">
                {engineeringReviewAccepted ? '已接受' : bundle.engineering_review?.decision || '待复核'}
              </Descriptions.Item>
              <Descriptions.Item label="复核人">{bundle.engineering_review?.actor || '-'}</Descriptions.Item>
              <Descriptions.Item label="复核理由">{bundle.engineering_review?.reason || '-'}</Descriptions.Item>
            </Descriptions>
            <Alert
              type={engineeringReviewAccepted ? 'success' : 'info'}
              showIcon
              message={engineeringReviewAccepted ? '工程复核已完成' : '请确认字段语义、变化判据和事件触发条件'}
              description={replayRequired
                ? '可以直接接受，也可以在保留原候选和历史证据的前提下创建修订版本并重新回放。'
                : '可以直接接受，也可以在保留原候选和验证证据的前提下创建修订版本并重新验证。'}
              action={(
                <Button icon={<EditOutlined />} onClick={() => setReviewBundle(bundle)}>
                  {engineeringReviewAccepted ? '查看复核结果' : '工程复核与修订'}
                </Button>
              )}
            />
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="候选验证结束后进入工程复核" />
        )}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>独立发布审批</Typography.Text>
        {bundle ? (
          <>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="候选规则包">
                <Typography.Text copyable>{bundle.id}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="当前状态">{statusLabel[bundle.status] || bundle.status}</Descriptions.Item>
              <Descriptions.Item label="发布就绪">
                {bundle.status === 'active' ? '已发布' : bundle.release_ready ? '是' : '否'}
              </Descriptions.Item>
              <Descriptions.Item label="阻断项">
                {bundle.blockers.length
                  ? bundle.blockers.map((item) => blockerLabel[item] || item).join('；')
                  : '无'}
              </Descriptions.Item>
            </Descriptions>
            {bundle.status === 'active' ? (
              <Alert type="success" showIcon message="规则资产包已原子发布" />
            ) : (
              <Alert
                type={bundle.release_ready ? 'success' : 'warning'}
                showIcon
                message={bundle.release_ready ? '等待独立审批人发布' : '尚未满足发布条件'}
                description="发布会在同一事务内激活所需 Contract、Policy 与 Rule；任一步失败都会整体回滚。"
                action={bundle.status === 'pending_approval' ? (
                  <Button
                    className="product-primary-button"
                    icon={<CheckOutlined />}
                    disabled={!bundle.release_ready}
                    onClick={() => setApprovalBundle(bundle)}
                  >
                    审批发布
                  </Button>
                ) : undefined}
              />
            )}
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="工程复核通过后进入独立发布审批" />
        )}
      </Space>
    ),
  ][visiblePipelineStep]

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
          {bundle?.status === 'active' && visiblePipelineStep !== 2 && (
            <Button
              icon={<PlayCircleOutlined />}
              disabled={!effectiveDatasetIds.length || generationInFlight}
              loading={regenerationMutation.isLoading}
              onClick={() => regenerationMutation.mutate()}
            >
              基于当前样本生成修订候选
            </Button>
          )}
        </Space>
      </div>

      <section className="parser-generation-step">
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>规则资产生产流程</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              查看历史阶段不会倒退任务状态；真正修改候选时会创建新修订并重新验证。
            </Typography.Text>
          </div>
          {bundle?.id && (
            <Typography.Text copyable type="secondary">{bundle.id}</Typography.Text>
          )}
        </div>
        <Steps
          current={visiblePipelineStep}
          onChange={setSelectedPipelineStep}
          responsive
          items={pipelineSteps}
        />
        <div
          style={{
            alignItems: 'center',
            borderTop: '1px solid var(--border-subtle, #e5e7eb)',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            justifyContent: 'space-between',
            marginTop: 16,
            paddingTop: 12,
          }}
        >
          <Space wrap>
            <HistoryOutlined />
            <Typography.Text>
              正在查看：{['来源与 Parser', '安全上下文', '候选生成', '验证与回放', '工程复核', '发布审批'][visiblePipelineStep]}
            </Typography.Text>
            {visiblePipelineStep !== pipelineCurrent && <Tag>历史查看</Tag>}
          </Space>
          <Space wrap>
            <Button
              icon={<ArrowLeftOutlined />}
              disabled={visiblePipelineStep === 0}
              onClick={() => setSelectedPipelineStep(Math.max(0, visiblePipelineStep - 1))}
            >
              返回上一步
            </Button>
            <Button
              disabled={visiblePipelineStep === 5}
              onClick={() => setSelectedPipelineStep(Math.min(5, visiblePipelineStep + 1))}
            >
              下一步 <ArrowRightOutlined />
            </Button>
            {visiblePipelineStep !== pipelineCurrent && (
              <Button onClick={() => setSelectedPipelineStep(null)}>回到当前进度</Button>
            )}
          </Space>
        </div>
        <div style={{ marginTop: 16 }}>{pipelineStepContent}</div>
      </section>

      {!effectiveDatasetIds.length && !contextReady && (
        <Alert
          type="error"
          showIcon
          message="缺少离线数据集"
          description="请从规则样本批次进入该命令资产，或重新选择包含结构化记录的数据集。"
        />
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
        impact={releaseImpact}
        blockers={approvalBundle?.release_ready ? [] : approvalBundle?.blockers || []}
        loading={approvalMutation.isLoading}
        reasonRequired={false}
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
