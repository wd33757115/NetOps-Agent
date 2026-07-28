import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  CheckOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useNavigate } from 'react-router-dom'

import {
  parserApi,
  ruleAssetSourceApi,
  type ParserJobItem,
  type ParserJobSample,
} from '../../../services/api'
import { codePanelStyle } from './parserUi'
import ParserTemplateApprovalDrawer from './ParserTemplateApprovalDrawer'

type SampleRole = 'unassigned' | 'generation' | 'acceptance' | 'negative'

interface SampleSuggestion {
  sample_id: string
  recommended_role: SampleRole
  classification?: string
  semantic_plan?: {
    target_section?: string
    record_unit?: string
    record_start?: string
    record_end?: string
    exclude_sections?: string[]
    ambiguities?: string[]
  }
}

interface SuggestionState {
  status?: 'queued' | 'running' | 'ready' | 'failed'
  error?: string
  result?: {
    classification?: string
    confidence?: number
    rationale?: string
    parser_spec?: Record<string, unknown>
    sample_suggestions?: SampleSuggestion[]
  }
}

const roleLabel: Record<SampleRole, string> = {
  unassigned: '待判定',
  generation: '生成样本',
  acceptance: '独立验收样本',
  negative: '负样本',
}

const classificationLabel: Record<string, string> = {
  data: '包含业务数据',
  valid_empty: '合法空输出',
  unsupported_command: '命令不支持',
  command_error: '命令执行错误',
  header_only_unverified: '仅有表头，待补样本',
  empty_unverified: '空输出待判定',
}

const blockerLabel: Record<string, string> = {
  parser_spec_missing: '语义方案尚未形成',
  parser_spec_not_confirmed: '语义方案尚未就绪',
  parser_spec_digest_mismatch: '语义方案摘要不一致',
  samples_missing: '缺少可用样本',
  samples_not_sanitized: '样本脱敏尚未完成',
  sample_roles_unassigned: '存在未分类样本',
  generation_sample_missing: '缺少包含业务数据的样本',
  generation_samples_without_data: '生成样本中没有可验证的数据记录',
  header_only_samples_unverified: '样本只有表头，没有实际数据行',
}

const jobStatusLabel: Record<string, string> = {
  collecting: '准备中',
  pending: '等待生成',
  running: '生成中',
  validating: '校验中',
  succeeded: '待人工确认',
  failed: '生成失败',
  cancelled: '已取消',
  superseded: '已替代',
}

const parserFailurePresentation = (job: ParserJobItem) => {
  const raw = String(job.agent_state_json?.stop_reason || job.last_error || '')
  const normalized = raw.toLowerCase()
  if (job.failure_code === 'parser_spec_revised') {
    return {
      title: 'ParserSpec 已更新',
      description: '新的语义方案已经保存，原候选与当前方案不再一致。请使用新方案重新生成 TextFSM。',
      retryAllowed: true,
      severity: 'warning' as const,
    }
  }
  if (
    normalized.includes('insufficient_sample')
    || normalized.includes('no positive structured data')
    || normalized.includes('zero interface data rows')
  ) {
    return {
      title: '样本不足，无法验证 Parser',
      description: '当前样本只有表头或空结果，没有实际业务数据行。请补充至少一份包含真实记录的同命令输出；使用相同样本重新生成不会改善结果。',
      retryAllowed: false,
      severity: 'error' as const,
    }
  }
  if (normalized.includes('semantic_review_failed')) {
    return {
      title: '解析结果未通过独立复核',
      description: '模板可以执行，但最终字段、记录范围或主键仍有歧义。请先查看解析结果，并在高级修订中调整语义方案。',
      retryAllowed: true,
      severity: 'error' as const,
    }
  }
  if (normalized.includes('compile') || normalized.includes('dialect')) {
    return {
      title: '生成的 TextFSM 未通过语法校验',
      description: '模型生成的候选不符合当前 TextFSM 方言。平台已保留诊断，可重新生成或在高级修订中约束字段与记录范围。',
      retryAllowed: true,
      severity: 'error' as const,
    }
  }
  return {
    title: '本次生成未通过',
    description: '平台没有得到可审批的 Parser 候选。请先查看技术诊断，再决定重新生成或定向修订。',
    retryAllowed: true,
    severity: 'error' as const,
  }
}

const refreshParserState = async (
  queryClient: ReturnType<typeof useQueryClient>,
  jobId: string,
) => {
  await Promise.all([
    queryClient.invalidateQueries(['parser-job-inline', jobId]),
    queryClient.invalidateQueries('parser-job-candidate-templates'),
    queryClient.invalidateQueries('parser-template-comparison-inline'),
    queryClient.invalidateQueries('parser-templates'),
    queryClient.invalidateQueries('rule-asset-production-units'),
    queryClient.invalidateQueries('rule-asset-production-unit'),
    queryClient.invalidateQueries('offline-command-asset-units'),
    queryClient.invalidateQueries('offline-command-asset-unit'),
    queryClient.invalidateQueries('offline-rule-asset-pipeline'),
  ])
}

const ParserGenerationStep: React.FC<{ jobId?: string | null }> = ({ jobId }) => {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const isMobile = typeof window !== 'undefined'
    && Boolean(window.matchMedia?.('(max-width: 767px)')?.matches)
  const autoSuggestionJob = useRef<string>()
  const autoDispatchJob = useRef<string>()
  const projectionRefreshKey = useRef<string>()

  const [specText, setSpecText] = useState('{}')
  const [specReason, setSpecReason] = useState('工程师定向修订语义方案')
  const [editingSampleId, setEditingSampleId] = useState<string>()
  const [sampleRole, setSampleRole] = useState<SampleRole>('unassigned')
  const [sampleRecordCount, setSampleRecordCount] = useState(0)
  const [sampleKeysText, setSampleKeysText] = useState('[]')
  const [sampleReason, setSampleReason] = useState('工程师修订样本角色或验收基准')
  const [approvalOpen, setApprovalOpen] = useState(false)

  const jobQuery = useQuery(
    ['parser-job-inline', jobId],
    () => parserApi.job(jobId || ''),
    {
      enabled: Boolean(jobId),
      refetchInterval: (job) => {
        const suggestion = (job?.agent_state_json?.parser_spec_suggestion || {}) as SuggestionState
        return (
          ['queued', 'running'].includes(suggestion.status || '')
          || ['pending', 'running', 'validating'].includes(job?.status || '')
        ) ? 2500 : false
      },
    },
  )
  const job = jobQuery.data
  const suggestion = (job?.agent_state_json?.parser_spec_suggestion || {}) as SuggestionState
  const suggestions = useMemo(
    () => suggestion.result?.sample_suggestions || [],
    [suggestion.result?.sample_suggestions],
  )
  const suggestionMap = useMemo(
    () => new Map(suggestions.map((item) => [item.sample_id, item])),
    [suggestions],
  )
  const sourceOfflineJobId = job?.samples?.find(
    (sample) => sample.source_context?.offline_job_id,
  )?.source_context?.offline_job_id

  function matchesJob(template: {
    source_job_id?: string
    vendor: string
    profile: string
    canonical_command: string
  }) {
    return (
      template.source_job_id === jobId
      || (
        template.vendor === job?.vendor
        && template.profile === job?.profile
        && template.canonical_command === job?.canonical_command
      )
    )
  }

  const templatesQuery = useQuery(
    ['parser-job-candidate-templates', jobId, job?.status],
    () => parserApi.templates({ search: job?.canonical_command, limit: 100 }),
    {
      enabled: Boolean(jobId && job),
      refetchInterval: (data) => (
        job?.status === 'succeeded'
        && !(data?.items || []).some((template) => matchesJob(template))
      ) ? 2000 : false,
    },
  )
  const candidateTemplate = templatesQuery.data?.items.find(
    (template) => template.status === 'pending_approval' && matchesJob(template),
  )
  const activeTemplate = templatesQuery.data?.items.find(
    (template) => template.status === 'active' && matchesJob(template),
  )
  const aiReview = candidateTemplate?.validation_json?.ai_review as {
    passed?: boolean
    summary?: string
  } | undefined

  const comparisonQuery = useQuery(
    ['parser-template-comparison-inline', candidateTemplate?.id],
    () => parserApi.comparison(candidateTemplate?.id || ''),
    { enabled: Boolean(approvalOpen && candidateTemplate?.id) },
  )

  useEffect(() => {
    setSpecText(JSON.stringify(job?.parser_spec_json || {}, null, 2))
  }, [job?.id, job?.parser_spec_json])

  const suggestSpec = useMutation(
    () => parserApi.suggestJobSpec(jobId || ''),
    {
      onSuccess: async () => {
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '语义方案生成失败'),
    },
  )
  const requestSpecSuggestion = suggestSpec.mutate
  const specSuggestionLoading = suggestSpec.isLoading

  const dispatch = useMutation(
    () => parserApi.dispatchJob(jobId || ''),
    {
      onSuccess: async () => {
        message.success('TextFSM 生成与校验已启动')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '启动生成失败'),
    },
  )
  const dispatchGeneration = dispatch.mutate
  const dispatchLoading = dispatch.isLoading

  useEffect(() => {
    if (
      !job
      || job.status !== 'collecting'
      || job.parser_spec_status !== 'missing'
      || suggestion.status
      || specSuggestionLoading
      || autoSuggestionJob.current === job.id
    ) return
    autoSuggestionJob.current = job.id
    requestSpecSuggestion()
  }, [
    job,
    job?.id,
    job?.parser_spec_status,
    job?.status,
    requestSpecSuggestion,
    specSuggestionLoading,
    suggestion.status,
  ])

  useEffect(() => {
    if (
      !job
      || job.status !== 'collecting'
      || suggestion.status !== 'ready'
      || !job.generation_readiness?.ready
      || dispatchLoading
      || autoDispatchJob.current === job.id
    ) return
    autoDispatchJob.current = job.id
    dispatchGeneration()
  }, [
    dispatchGeneration,
    dispatchLoading,
    job,
    job?.generation_readiness?.ready,
    job?.id,
    job?.status,
    suggestion.status,
  ])

  useEffect(() => {
    if (
      !job?.status
      || !sourceOfflineJobId
      || !['succeeded', 'failed', 'cancelled'].includes(job.status)
    ) return
    const key = `${jobId}:${job.status}:${candidateTemplate?.id || activeTemplate?.id || 'none'}`
    if (projectionRefreshKey.current === key) return
    projectionRefreshKey.current = key
    void ruleAssetSourceApi.refreshCommandAssetUnits(sourceOfflineJobId)
      .then(() => refreshParserState(queryClient, jobId || ''))
      .catch(() => undefined)
  }, [
    activeTemplate?.id,
    candidateTemplate?.id,
    job?.status,
    jobId,
    queryClient,
    sourceOfflineJobId,
  ])

  const saveSpec = useMutation(
    async () => {
      if (!jobId) throw new Error('缺少 Parser 任务 ID')
      if (!specReason.trim()) throw new Error('请填写修订原因')
      let parserSpec: Record<string, unknown>
      try {
        parserSpec = JSON.parse(specText) as Record<string, unknown>
      } catch {
        throw new Error('ParserSpec 不是有效 JSON')
      }
      return parserApi.updateJobSpec(jobId, {
        parser_spec: parserSpec,
        confirmed: true,
        reason: specReason.trim(),
      })
    },
    {
      onSuccess: async () => {
        message.success('语义方案修订已保存')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '保存语义方案失败'),
    },
  )

  const saveSample = useMutation(
    async () => {
      if (!jobId || !editingSampleId) return
      if (!sampleReason.trim()) throw new Error('请填写修订原因')
      let primaryKeys: string[][] = []
      try {
        primaryKeys = JSON.parse(sampleKeysText) as string[][]
      } catch {
        throw new Error('主键基准不是有效 JSON')
      }
      await parserApi.updateJobSampleRole(jobId, editingSampleId, {
        role: sampleRole,
        expectation: ['generation', 'acceptance'].includes(sampleRole) && sampleRecordCount > 0
          ? { record_count: sampleRecordCount, primary_keys: primaryKeys }
          : undefined,
        reason: sampleReason.trim(),
      })
    },
    {
      onSuccess: async () => {
        setEditingSampleId(undefined)
        message.success('样本修订已保存')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '保存样本修订失败'),
    },
  )

  const retryAndDispatch = useMutation(
    async () => {
      if (!jobId) throw new Error('缺少 Parser 任务 ID')
      await parserApi.retryJob(jobId)
      return parserApi.dispatchJob(jobId)
    },
    {
      onSuccess: async () => {
        message.success('Parser 已重新开始生成')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: async (error: Error) => {
        await refreshParserState(queryClient, jobId || '')
        message.error(error.message || '重新生成失败')
      },
    },
  )

  const recover = useMutation(
    () => parserApi.recoverJob(jobId || '', '工程师恢复长时间无进展的 Parser 任务'),
    {
      onSuccess: async () => {
        message.success('任务已标记为失败，可重新生成')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '恢复任务失败'),
    },
  )

  const approve = useMutation(
    () => parserApi.activate(
      candidateTemplate?.id || '',
      '工程师已确认最终解析结果、主键与原始命令输出一致',
    ),
    {
      onSuccess: async () => {
        setApprovalOpen(false)
        message.success('Parser 已审批发布')
        if (sourceOfflineJobId) {
          await ruleAssetSourceApi.refreshCommandAssetUnits(sourceOfflineJobId)
        }
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '审批发布失败'),
    },
  )

  const reject = useMutation(
    () => parserApi.reject(
      candidateTemplate?.id || '',
      '最终解析结果或主键不符合业务要求，需要定向修订',
    ),
    {
      onSuccess: async () => {
        setApprovalOpen(false)
        message.success('候选已驳回，可在高级修订中调整后重新生成')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '驳回候选失败'),
    },
  )

  const editSample = (sample: ParserJobSample) => {
    setEditingSampleId(sample.id)
    setSampleRole(sample.sample_role || 'unassigned')
    setSampleRecordCount(sample.expectation_json?.record_count || 0)
    setSampleKeysText(JSON.stringify(sample.expectation_json?.primary_keys || [], null, 2))
    setSampleReason('工程师修订样本角色或验收基准')
  }
  const openSampleImport = () => {
    const params = new URLSearchParams({
      workspace: 'production',
      subtab: 'source-jobs',
      command: job?.canonical_command || '',
      action: 'supplement-samples',
    })
    navigate(`/skills/parser?${params.toString()}`)
  }

  if (!jobId) return <Empty description="尚未创建 Parser 任务" />
  if (jobQuery.isLoading) {
    return <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
  }
  if (jobQuery.isError || !job) {
    return (
      <Alert
        type="error"
        showIcon
        message="Parser 任务加载失败"
        action={<Button onClick={() => jobQuery.refetch()}>重试</Button>}
      />
    )
  }

  const readiness = job.generation_readiness
  const sampleEditingLocked = ['pending', 'running', 'validating', 'succeeded', 'superseded'].includes(job.status)
  const jobActive = ['pending', 'running', 'validating'].includes(job.status)
  const semanticReady = job.parser_spec_status === 'confirmed'
  const reviewPassed = activeTemplate ? true : aiReview?.passed === true
  const parserSpecRevised = job.failure_code === 'parser_spec_revised'
  const failurePresentation = parserFailurePresentation(job)
  const semanticAnalysisActive = ['queued', 'running'].includes(suggestion.status || '')
  const suggestionNeedsPositiveSample = [
    'no_positive_data',
    'sample_collection_required',
  ].includes(suggestion.result?.classification || '')
  const needsPositiveSample = Boolean(
    suggestionNeedsPositiveSample
    || readiness?.blockers?.some((item) => [
      'generation_samples_without_data',
      'header_only_samples_unverified',
    ].includes(item)),
  )
  const pipelineCurrent = activeTemplate
    ? 4
    : candidateTemplate
      ? 4
      : job.status === 'succeeded'
        ? 3
        : jobActive
          ? 2
          : semanticReady
            ? 2
            : 1

  const sampleColumns: ColumnsType<ParserJobSample> = [
    {
      title: '样本',
      width: 240,
      render: (_, sample) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {sample.source_context?.device_name || sample.device_id}
          </Typography.Text>
          <Typography.Text type="secondary">
            {sample.source_context?.source_file_name || sample.dataset_id || sample.id}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '内容判定',
      width: 140,
      render: (_, sample) => (
        <Tag>
          {classificationLabel[sample.content_classification?.status || ''] || '待判定'}
        </Tag>
      ),
    },
    {
      title: '角色',
      width: 120,
      render: (_, sample) => <Tag>{roleLabel[sample.sample_role || 'unassigned']}</Tag>,
    },
    {
      title: '记录方案',
      render: (_, sample) => {
        const plan = suggestionMap.get(sample.id)?.semantic_plan
        return plan
          ? [plan.target_section, plan.record_unit].filter(Boolean).join(' / ') || '已形成'
          : '暂无'
      },
    },
    {
      title: '操作',
      width: 90,
      render: (_, sample) => (
        <Button size="small" disabled={sampleEditingLocked} onClick={() => editSample(sample)}>
          修订
        </Button>
      ),
    },
  ]

  const advancedContent = (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="ParserSpec 可在任何阶段人工修订"
        description="保存修订会使旧的生成结果失效，并要求使用新方案重新生成；已经发布的生产模板不会被直接修改。"
      />
      <section className="parser-generation-step">
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>ParserSpec 与记录语义方案</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              当前方案由 LLM 从脱敏样本生成，修改会留下审计记录。
            </Typography.Text>
          </div>
          <Space wrap>
            <Button
              icon={<RobotOutlined />}
              disabled={['queued', 'running'].includes(suggestion.status || '')}
              loading={specSuggestionLoading}
              onClick={() => requestSpecSuggestion()}
            >
              重新分析
            </Button>
            <Button
              icon={<CheckOutlined />}
              loading={saveSpec.isLoading}
              onClick={() => saveSpec.mutate()}
            >
              保存修订
            </Button>
          </Space>
        </div>
        <Input.TextArea
          aria-label="ParserSpec JSON"
          value={specText}
          onChange={(event) => setSpecText(event.target.value)}
          autoSize={{ minRows: 10, maxRows: 22 }}
          style={{ fontFamily: 'Consolas, Monaco, monospace' }}
        />
        <Input
          aria-label="ParserSpec 修订原因"
          value={specReason}
          onChange={(event) => setSpecReason(event.target.value)}
          placeholder="修订原因"
        />
        {suggestion.result?.rationale && (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {suggestion.result.rationale}
          </Typography.Paragraph>
        )}
      </section>

      <section className="parser-generation-step">
        <Typography.Text strong>样本角色与可选验收基准</Typography.Text>
        <Table
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: 820 }}
          dataSource={job.samples || []}
          columns={sampleColumns}
        />
        {editingSampleId && (
          <div className="parser-generation-step__editor">
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Typography.Text strong>定向修订样本</Typography.Text>
              <Select
                aria-label="样本角色"
                value={sampleRole}
                onChange={setSampleRole}
                options={Object.entries(roleLabel).map(([value, label]) => ({ value, label }))}
                style={{ width: 220 }}
              />
              {['generation', 'acceptance'].includes(sampleRole) && (
                <>
                  <InputNumber
                    min={0}
                    precision={0}
                    value={sampleRecordCount}
                    onChange={(value) => setSampleRecordCount(Number(value || 0))}
                    addonBefore="可选记录数基准"
                    style={{ width: 250 }}
                  />
                  <Input.TextArea
                    aria-label="可选主键基准 JSON"
                    value={sampleKeysText}
                    onChange={(event) => setSampleKeysText(event.target.value)}
                    autoSize={{ minRows: 4, maxRows: 10 }}
                    style={{ fontFamily: 'Consolas, Monaco, monospace' }}
                  />
                </>
              )}
              <Input
                aria-label="样本修订原因"
                value={sampleReason}
                onChange={(event) => setSampleReason(event.target.value)}
                placeholder="修订原因"
              />
              <Space>
                <Button onClick={() => setEditingSampleId(undefined)}>取消</Button>
                <Button loading={saveSample.isLoading} onClick={() => saveSample.mutate()}>
                  保存修订
                </Button>
              </Space>
              <pre style={{ ...codePanelStyle, maxHeight: 320, whiteSpace: 'pre-wrap' }}>
                {(job.samples || []).find(
                  (sample) => sample.id === editingSampleId,
                )?.sanitized_text || '无可展示内容'}
              </pre>
            </Space>
          </div>
        )}
      </section>
    </Space>
  )

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
        <Descriptions.Item label="命令" span={isMobile ? 1 : 2}>
          {job.canonical_command}
        </Descriptions.Item>
        <Descriptions.Item label="兼容范围">
          {[job.vendor, job.model || job.profile].filter(Boolean).join(' / ')}
        </Descriptions.Item>
        <Descriptions.Item label="当前状态">
          <Tag color={activeTemplate ? 'green' : parserSpecRevised ? 'gold' : undefined}>
            {activeTemplate
              ? '已发布'
              : parserSpecRevised
                ? '待重新生成'
                : jobStatusLabel[job.status] || job.status}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      <section className="parser-generation-step">
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>Parser 自动生成流程</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              平台自动完成语义设计、模板生成、本地校验与独立复核。
            </Typography.Text>
          </div>
          {jobActive && (
            <Button loading={recover.isLoading} onClick={() => recover.mutate()}>
              恢复无进展任务
            </Button>
          )}
        </div>
        <Steps
          current={pipelineCurrent}
          responsive
          items={[
            {
              title: '样本识别',
              status: job.sample_count > 0 ? 'finish' : 'error',
              description: `${job.sample_count} 份`,
            },
            {
              title: '语义方案',
              status: suggestion.status === 'failed'
                ? 'error'
                : semanticReady
                  ? 'finish'
                  : ['queued', 'running'].includes(suggestion.status || '')
                    ? 'process'
                    : 'wait',
              description: semanticReady ? '已自动采纳' : '分析字段与记录范围',
            },
            {
              title: '生成与校验',
              status: parserSpecRevised
                ? 'wait'
                : job.status === 'failed'
                ? 'error'
                : ['pending', 'running', 'validating'].includes(job.status)
                  ? 'process'
                  : ['succeeded'].includes(job.status)
                    ? 'finish'
                    : 'wait',
              description: 'TextFSM 与本地硬门禁',
            },
            {
              title: '独立复核',
              status: reviewPassed
                ? 'finish'
                : job.status === 'failed' && job.last_error?.includes('semantic')
                  ? 'error'
                  : job.status === 'validating'
                    ? 'process'
                    : 'wait',
              description: reviewPassed ? 'AI 复核通过' : '检查完整性与主键',
            },
            {
              title: '人工确认',
              status: activeTemplate ? 'finish' : candidateTemplate ? 'process' : 'wait',
              description: activeTemplate ? '已发布' : '确认结果与主键',
            },
          ]}
        />
      </section>

      {suggestion.status === 'failed' && (
        <Alert
          type={failurePresentation.severity}
          showIcon
          message="语义方案生成失败"
          description={suggestion.error}
          action={(
            <Button loading={specSuggestionLoading} onClick={() => requestSpecSuggestion()}>
              重新分析
            </Button>
          )}
        />
      )}

      {jobActive && (
        <Alert
          type="info"
          showIcon
          message={job.status === 'validating' ? '正在校验候选结果' : '正在生成 TextFSM'}
          description="模型每轮生成后都会调用本地编译与解析校验；未通过时，诊断会返回模型继续修正。"
        />
      )}

      {job.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          message={failurePresentation.title}
          description={(
            <Space direction="vertical" size={8}>
              <Typography.Text>{failurePresentation.description}</Typography.Text>
              {(job.last_error || job.agent_state_json?.stop_reason) && (
                <Collapse
                  ghost
                  size="small"
                  items={[{
                    key: 'technical-diagnostic',
                    label: '查看技术诊断',
                    children: (
                      <pre style={{ ...codePanelStyle, maxHeight: 240, whiteSpace: 'pre-wrap' }}>
                        {String(job.agent_state_json?.stop_reason || job.last_error)}
                      </pre>
                    ),
                  }]}
                />
              )}
            </Space>
          )}
          action={(
            failurePresentation.retryAllowed ? (
              <Button
                icon={<PlayCircleOutlined />}
                disabled={!readiness?.ready}
                loading={retryAndDispatch.isLoading}
                onClick={() => retryAndDispatch.mutate()}
              >
                重新生成
              </Button>
            ) : (
              <Button onClick={openSampleImport}>
                导入补充样本
              </Button>
            )
          )}
        />
      )}

      {!activeTemplate && candidateTemplate && (
        <Alert
          type="success"
          showIcon
          message="候选结果已通过本地校验与独立复核"
          description={aiReview?.summary || '请核对原始输出、最终解析结果和主键后发布。'}
          action={<Button onClick={() => setApprovalOpen(true)}>查看并确认结果</Button>}
        />
      )}

      {activeTemplate && (
        <Alert
          type="success"
          showIcon
          message="Parser 已发布"
          description={`版本 ${activeTemplate.version} 已进入运行时；审批时确认的解析结果已冻结为回归基线。`}
          action={(
            <Button
              onClick={() => {
                const datasets = Array.from(new Set(
                  (job.samples || [])
                    .map((sample) => sample.dataset_id)
                    .filter((value): value is string => Boolean(value)),
                ))
                const params = new URLSearchParams({
                  workspace: 'runtime',
                  subtab: 'coverage-backfill',
                  action: 'backfill',
                  open_backfill: '1',
                  template_id: activeTemplate.id,
                  run_modes: 'parser_validation',
                })
                if (sourceOfflineJobId) {
                  params.set('source_job_id', sourceOfflineJobId)
                  params.set('offline_job_id', sourceOfflineJobId)
                }
                if (datasets.length) params.set('dataset_ids', datasets.join(','))
                navigate(`/skills/parser?${params.toString()}`)
              }}
            >
              回填历史样本
            </Button>
          )}
        />
      )}

      {!activeTemplate && job.status === 'succeeded' && !candidateTemplate && !templatesQuery.isLoading && (
        <Alert
          type="warning"
          showIcon
          message="没有可确认的候选结果"
          description="上一候选已被驳回或撤回。可直接重新生成，也可先在高级修订中定向调整。"
          action={(
            <Button
              icon={<PlayCircleOutlined />}
              disabled={!readiness?.ready}
              loading={retryAndDispatch.isLoading}
              onClick={() => retryAndDispatch.mutate()}
            >
              重新生成
            </Button>
          )}
        />
      )}

      {!jobActive
        && ['collecting', 'pending'].includes(job.status)
        && !candidateTemplate
        && !activeTemplate
        && (
        <Alert
          type={readiness?.ready ? 'success' : needsPositiveSample ? 'warning' : 'info'}
          showIcon
          message={
            readiness?.ready
              ? '自动生成条件已满足'
              : semanticAnalysisActive
                ? '正在分析样本并生成语义方案'
              : needsPositiveSample
                ? '需要补充包含实际记录的样本'
                : '平台正在准备生成条件'
          }
          description={
            readiness?.ready
              ? '语义方案就绪后将自动启动 TextFSM 生成。'
              : semanticAnalysisActive
                ? '平台正在识别字段、主键、记录范围和样本角色；分析完成后会自动进入 TextFSM 生成。'
                : (readiness?.blockers || []).map((item) => blockerLabel[item] || item).join('；')
          }
          action={
            needsPositiveSample ? (
              <Button onClick={openSampleImport}>导入补充样本</Button>
            ) : readiness?.ready && job.status === 'collecting' ? (
              <Button loading={dispatchLoading} onClick={() => dispatchGeneration()}>
                立即开始
              </Button>
            ) : undefined
          }
        />
        )}

      <Collapse
        ghost
        items={[{
          key: 'advanced',
          label: (
            <Space>
              <SettingOutlined />
              <span>高级修订</span>
              <Typography.Text type="secondary">
                ParserSpec、记录方案、样本角色与可选验收基准
              </Typography.Text>
            </Space>
          ),
          children: advancedContent,
        }]}
      />

      <ParserTemplateApprovalDrawer
        open={approvalOpen}
        onClose={() => setApprovalOpen(false)}
        isMobile={isMobile}
        loading={comparisonQuery.isLoading}
        comparison={comparisonQuery.data}
        approving={approve.isLoading}
        rejecting={reject.isLoading}
        onApprove={() => approve.mutate()}
        onReject={() => reject.mutate()}
      />
    </Space>
  )
}

export default ParserGenerationStep
