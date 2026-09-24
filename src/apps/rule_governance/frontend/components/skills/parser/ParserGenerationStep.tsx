import { businessHref } from '@netops/apps/appRoutes'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
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
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  CodeOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  SettingOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useNavigate } from 'react-router-dom'

import {
  parserApi,
  ruleAssetSourceApi,
  type AdapterProjectionPreviewResult,
  type ParserGenerationAttempt,
  type ParserGenerationProgress,
  type ParserJobItem,
  type ParserJobSample,
} from '@netops/services/api'
import { codePanelStyle } from './parserUi'
import ParserTemplateApprovalDrawer from './ParserTemplateApprovalDrawer'
import StandardResultTable from './StandardResultTable'

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
  stage?: string
  task_id?: string
  requested_at?: string
  updated_at?: string
  heartbeat_at?: string
  deadline_at?: string
  elapsed_ms?: number
  cancellable?: boolean
  check_item_id?: string | null
  error?: string
  result?: {
    classification?: string
    confidence?: number
    rationale?: string
    parser_spec?: Record<string, unknown>
    sample_suggestions?: SampleSuggestion[]
    normalization_warnings?: string[]
    field_naming_context?: {
      semantic_profile_id?: string | null
      preferred_value_names?: string[]
      policy?: {
        preference?: string
        fallback?: string
        validation?: string
      }
    }
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

const periodRoleLabel: Record<string, string> = {
  sample: '样本周期',
  baseline: '基线周期',
  current: '当前周期',
}

const sampleIdentity = (sample: ParserJobSample) => {
  const source = sample.source_context
  return {
    device: source?.device_name || sample.device_id,
    file: source?.source_file_name || sample.dataset_id || sample.id,
    period: source?.period_role
      ? periodRoleLabel[source.period_role] || source.period_role
      : undefined,
    observedAt: source?.observed_at,
  }
}

const sampleProvenanceText = (sample: ParserJobSample) => {
  const identity = sampleIdentity(sample)
  const details = [
    identity.period,
    identity.observedAt ? `采集：${identity.observedAt}` : undefined,
  ].filter(Boolean)
  return details.join(' · ')
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

const generationStageLabel: Record<string, string> = {
  queued: '等待 AI Worker',
  context_prepared: '生成上下文已冻结',
  model_generating: '模型生成候选',
  local_compiling: '编译 TextFSM',
  sample_replaying: '全样本重放',
  model_revising: '模型定向修复',
  semantic_reviewing: '独立语义复核',
  attempt_persisted: '本轮结果已保存',
  candidate_freezing: '冻结审批候选',
  generation_stopped: '生成已停止',
  succeeded: '候选已就绪',
  failed: '生成失败',
  cancelled: '生成已取消',
}

const formatElapsed = (milliseconds?: number) => {
  const seconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000))
  const minutes = Math.floor(seconds / 60)
  const remaining = seconds % 60
  return minutes > 0 ? `${minutes} 分 ${remaining} 秒` : `${remaining} 秒`
}

const asRecord = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
)

const attemptActionLabel = (value: string) => ({
  'seed:ntc_templates': 'NTC 模板候选',
  llm_revalidate_persisted: '历史候选重新执行',
  'deterministic:table': '表格编译器候选',
  'deterministic:key_value': '键值编译器候选',
  'deterministic:repeated_block': '重复块编译器候选',
  llm_tool_round_1: '第 1 轮模型生成',
  llm_tool_round_2: '第 2 轮模型修复',
  llm_tool_round_3: '第 3 轮模型修复',
}[value] || value)

const executionCheckLabel: Record<string, string> = {
  bundle_complete: 'ParserBundle 结构完整',
  compiled: 'TextFSM 编译',
  executed: '脱敏样本执行',
  source_executed: '源样本执行',
  source_shape_compatible: '脱敏前后结构一致',
  positive_samples_parsed: '历史校验：正样本有记录',
  required_fields_complete: '历史校验：必填字段完整',
  primary_keys_valid: '历史校验：主键有效',
  field_types_valid: '历史校验：字段类型',
  values_normalized: '历史校验：值已规范化',
  expectations_matched: '历史校验：预期结果一致',
  negative_samples_rejected: '历史校验：负样本无记录',
}

const AttemptExecutionEvidence: React.FC<{
  report?: Record<string, unknown>
}> = ({ report }) => {
  if (!report) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有该类样本" />
  const checks = asRecord(report.hard_gates)
  const diagnostics = Array.isArray(report.diagnostics) ? report.diagnostics : []
  const samples = Array.isArray(report.samples)
    ? report.samples.map(asRecord)
    : []
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        {Object.entries(checks).map(([name, passed]) => (
          <Tag key={name} color={passed === true ? 'success' : 'error'}>
            {executionCheckLabel[name] || name}: {passed === true ? '通过' : '未通过'}
          </Tag>
        ))}
      </Space>
      {diagnostics.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="本地执行诊断"
          description={diagnostics.map(String).join('；')}
        />
      )}
      <Collapse
        ghost
        items={samples.map((sample, sampleIndex) => {
          const records = Array.isArray(sample.records)
            ? sample.records.map(asRecord)
            : []
          const fieldNames = Array.from(new Set(records.flatMap((record) => Object.keys(record))))
          const columns: ColumnsType<Record<string, unknown>> = fieldNames.map((field) => ({
            title: field,
            dataIndex: field,
            key: field,
            width: 180,
            render: (value: unknown) => (
              value && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')
            ),
          }))
          const errors = Array.isArray(sample.errors) ? sample.errors.map(String) : []
          return {
            key: String(sample.sample_id || sampleIndex),
            label: (
              <Space wrap>
                <Typography.Text strong>{String(sample.sample_id || `样本 ${sampleIndex + 1}`)}</Typography.Text>
                <Tag>{Number(sample.record_count || records.length)} 条记录</Tag>
                {errors.length > 0 && <Tag color="warning">{errors.length} 条诊断</Tag>}
              </Space>
            ),
            children: (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {errors.length > 0 && <Typography.Text type="warning">{errors.join('；')}</Typography.Text>}
                {records.length > 0 ? (
                  <Table
                    size="small"
                    rowKey={(_, index) => `${String(sample.sample_id || sampleIndex)}-${index}`}
                    pagination={{ pageSize: 20, hideOnSinglePage: true }}
                    scroll={{ x: Math.max(720, fieldNames.length * 180) }}
                    dataSource={records}
                    columns={columns}
                  />
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮未解析出记录，等待 LLM 判断原因" />
                )}
              </Space>
            ),
          }
        })}
      />
    </Space>
  )
}

const ParserAttemptHistory: React.FC<{
  attempts: ParserGenerationAttempt[]
  loading: boolean
  fallbackTemplateText?: string
  onUseTemplate?: (attempt: ParserGenerationAttempt) => void
  mode?: 'all' | 'template' | 'execution'
}> = ({ attempts, loading, fallbackTemplateText, onUseTemplate, mode = 'all' }) => {
  if (loading) return <div style={{ display: 'grid', minHeight: 180, placeItems: 'center' }}><Spin /></div>
  if (!attempts.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未产生生成轮次" />
  const latestAttemptNumber = Math.max(...attempts.map((attempt) => attempt.attempt_number))
  const executableTemplateText = (value?: string) => {
    const text = value?.trim()
    if (!text) return undefined
    const hasValueDefinition = /^\s*Value(?:\s+\w+)*\s+\w+\s+\(.+\)\s*$/m.test(text)
    const hasStartState = /^\s*Start\s*$/m.test(text)
    return hasValueDefinition && hasStartState ? text : undefined
  }
  const templateTextFor = (attempt: ParserGenerationAttempt) => {
    const attemptText = executableTemplateText(attempt.template_text)
    if (attemptText) return attemptText
    if (attempt.attempt_number !== latestAttemptNumber) return undefined
    return executableTemplateText(fallbackTemplateText)
  }
  const visibleAttempts = mode === 'template'
    ? attempts.filter((attempt) => Boolean(templateTextFor(attempt)))
    : attempts
  if (!visibleAttempts.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="尚未生成可执行的 TextFSM 模板"
      />
    )
  }
  return (
    <Collapse
      accordion
      items={visibleAttempts.map((attempt) => {
        const effectiveTemplateText = templateTextFor(attempt)
        const validation = asRecord(attempt.validation_report_json)
        const generation = asRecord(validation.generation)
        const acceptance = Object.keys(asRecord(validation.acceptance)).length
          ? asRecord(validation.acceptance)
          : undefined
        const review = asRecord(validation.ai_review)
        const reviewDiagnostics = Array.isArray(review.diagnostics) ? review.diagnostics.map(asRecord) : []
        return {
          key: attempt.id,
          label: (
            <Space wrap>
              <Typography.Text strong>候选记录 {attempt.attempt_number}</Typography.Text>
              <Typography.Text>{attemptActionLabel(attempt.action)}</Typography.Text>
              <Tag color={attempt.status === 'passed' ? 'success' : attempt.status === 'failed' ? 'error' : 'warning'}>
                {attempt.status === 'passed' ? '复核通过' : attempt.status === 'needs_review' ? '待人工确认' : '需修复'}
              </Tag>
              <Typography.Text type="secondary">
                {attempt.model_name || attempt.model_provider || '本地候选'}
              </Typography.Text>
            </Space>
          ),
          children: (
            <Space direction="vertical" size={14} style={{ width: '100%' }}>
              {mode !== 'template' && (
                <Descriptions bordered size="small" column={2}>
                  <Descriptions.Item label="候选摘要">{attempt.template_sha256 || '-'}</Descriptions.Item>
                  <Descriptions.Item label="耗时">{attempt.latency_ms ? `${attempt.latency_ms} ms` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="生成时间">{attempt.created_at ? new Date(attempt.created_at).toLocaleString() : '-'}</Descriptions.Item>
                  <Descriptions.Item label="失败签名">{attempt.failure_signature || '-'}</Descriptions.Item>
                </Descriptions>
              )}
              {mode === 'template' && effectiveTemplateText ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {onUseTemplate && (
                    <Button
                      icon={<CodeOutlined />}
                      onClick={() => onUseTemplate({
                        ...attempt,
                        template_text: effectiveTemplateText,
                      })}
                    >
                      使用本轮模板进行工程师修订
                    </Button>
                  )}
                  <pre style={{ ...codePanelStyle, maxHeight: 420, whiteSpace: 'pre-wrap' }}>
                    {effectiveTemplateText}
                  </pre>
                </Space>
              ) : (
                <Collapse
                  ghost
                  defaultActiveKey={['execution', 'review']}
                  items={[
                    ...(mode === 'all' && effectiveTemplateText ? [{
                    key: 'template',
                    label: <Space><CodeOutlined /><span>本轮 TextFSM 候选</span></Space>,
                    children: (
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        {onUseTemplate && (
                          <Button
                            icon={<CodeOutlined />}
                            onClick={() => onUseTemplate({
                              ...attempt,
                              template_text: effectiveTemplateText,
                            })}
                          >
                            使用本轮模板进行工程师修订
                          </Button>
                        )}
                        <pre style={{ ...codePanelStyle, maxHeight: 420, whiteSpace: 'pre-wrap' }}>
                          {effectiveTemplateText}
                        </pre>
                      </Space>
                    ),
                    }] : []),
                    {
                      key: 'execution',
                      label: '本地执行结果',
                      children: <AttemptExecutionEvidence report={generation} />,
                    },
                    ...(acceptance ? [{
                      key: 'acceptance',
                      label: '额外复核样本执行结果',
                      children: <AttemptExecutionEvidence report={acceptance} />,
                    }] : []),
                    {
                      key: 'review',
                      label: 'LLM 独立复核',
                      children: Object.keys(review).length ? (
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          <Alert
                            type={review.passed === true ? 'success' : 'warning'}
                            showIcon
                            message={review.passed === true ? '本轮复核通过' : '本轮复核要求修复'}
                            description={String(review.summary || '未提供复核摘要')}
                          />
                          {reviewDiagnostics.map((diagnostic, index) => (
                            <Typography.Text key={`${attempt.id}-diagnostic-${index}`} type="secondary">
                              {String(diagnostic.code || 'review_finding')}：{String(diagnostic.message || '')}
                            </Typography.Text>
                          ))}
                        </Space>
                      ) : <Typography.Text type="secondary">本轮未进入独立复核。</Typography.Text>,
                    },
                  ]}
                />
              )}
            </Space>
          ),
        }
      })}
    />
  )
}

interface SemanticPlanRecordSet {
  name: string
  recordUnit: string
  primaryKeys: string[]
  expectedRecords?: number
  fields: Array<{
    name: string
    type: string
    required: boolean
    source: string
    example: string
    primaryKeyDefault?: string
  }>
}

const semanticPlanRecordSets = (
  value: unknown,
  fallbackExpectedRecords?: number,
): SemanticPlanRecordSet[] => {
  const spec = asRecord(value)
  const rawRecordSets = Array.isArray(spec.record_sets)
    ? spec.record_sets.map(asRecord)
    : Object.keys(spec).length
      ? [spec]
      : []
  return rawRecordSets.map((recordSet, index) => {
    const rawFields = asRecord(recordSet.fields)
    const primaryKeys = Array.isArray(recordSet.primary_keys)
      ? recordSet.primary_keys.map(String)
      : []
    return {
      name: String(recordSet.name || recordSet.entity || `record_set_${index + 1}`),
      recordUnit: String(recordSet.record_unit || recordSet.entity || recordSet.name || '一条业务记录'),
      primaryKeys,
      expectedRecords: Number(recordSet.expected_record_count || recordSet.record_count || (
        rawRecordSets.length === 1 ? fallbackExpectedRecords : 0
      )) || undefined,
      fields: Object.entries(rawFields).map(([name, definition]) => {
        const field = asRecord(definition)
        const examples = Array.isArray(field.examples) ? field.examples : []
        return {
          name,
          type: String(field.type || 'text'),
          required: field.required === true,
          source: String(field.source_column || field.source || field.cli_field || '待生成验证'),
          example: String(field.example ?? examples[0] ?? '待生成验证'),
          primaryKeyDefault: field.primary_key_default === undefined || field.primary_key_default === null
            ? undefined
            : String(field.primary_key_default),
        }
      }),
    }
  })
}

const SemanticPlanOverview: React.FC<{
  parserSpec?: Record<string, unknown>
  fallbackExpectedRecords?: number
}> = ({ parserSpec, fallbackExpectedRecords }) => {
  const recordSets = semanticPlanRecordSets(parserSpec, fallbackExpectedRecords)
  if (!recordSets.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="尚未形成语义方案。请先描述记录粒度、主键和需要保留的字段。"
      />
    )
  }
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {recordSets.map((recordSet) => (
        <section className="parser-generation-step" key={recordSet.name}>
          <div className="parser-generation-step__heading">
            <div>
              <Typography.Text strong>{recordSet.name}</Typography.Text>
              <br />
              <Typography.Text type="secondary">每条记录代表：{recordSet.recordUnit}</Typography.Text>
            </div>
            <Space size={6} wrap>
              <Tag>预计 {recordSet.expectedRecords ?? '待验证'} 条</Tag>
              <Tag>主键 {recordSet.primaryKeys.join(' + ') || '未定义'}</Tag>
            </Space>
          </div>
          <Table
            rowKey="name"
            size="small"
            pagination={false}
            dataSource={recordSet.fields}
            locale={{ emptyText: '该记录集尚未定义字段' }}
            columns={[
              { title: '字段', dataIndex: 'name', key: 'name' },
              { title: '类型', dataIndex: 'type', key: 'type', width: 140 },
              { title: 'CLI 来源', dataIndex: 'source', key: 'source', width: 160 },
              { title: '样例值', dataIndex: 'example', key: 'example', width: 160 },
              {
                title: '约束',
                key: 'constraint',
                width: 160,
                render: (_, field) => (
                  <Space size={4} wrap>
                    {recordSet.primaryKeys.includes(field.name) && <Tag>主键</Tag>}
                    {field.required && <Tag>必填</Tag>}
                    {field.primaryKeyDefault && <Tag color="blue">空值键默认 {field.primaryKeyDefault}</Tag>}
                    {!recordSet.primaryKeys.includes(field.name) && !field.required && <Typography.Text type="secondary">可选</Typography.Text>}
                  </Space>
                ),
              },
            ]}
          />
        </section>
      ))}
    </Space>
  )
}

const parserFailurePresentation = (job: ParserJobItem) => {
  const failureCode = String(job.failure_code || '').toLowerCase()
  const progress = job.agent_state_json?.generation_progress as ParserGenerationProgress | undefined
  const raw = [
    job.agent_state_json?.stop_reason,
    progress?.terminal_reason,
    progress?.details?.stop_reason,
    job.last_error,
  ].filter(Boolean).join(' ')
  const normalized = raw.toLowerCase()
  if (
    ['model_timeout', 'generation_deadline_exceeded', 'skill_execution_timeout', 'worker_timeout']
      .includes(failureCode)
    || normalized.includes('model_timeout')
    || normalized.includes('generation_deadline_exceeded')
    || normalized.includes('timed out')
  ) {
    return {
      title: '生成任务超过执行时限',
      description: '平台已保存完成的生成回合并关闭悬空调用。可直接重试；重试会沿用当前 ParserSpec、样本和已有诊断。',
      retryAllowed: true,
      severity: 'warning' as const,
    }
  }
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
  if (
    ['template_compile_failed', 'textfsm_compile_failed', 'compile_failed'].includes(failureCode)
    || normalized.includes('template_compile_failed')
    || normalized.includes('textfsm_compile_failed')
    || normalized.includes('compilation failed')
    || normalized.includes('dialect_error')
  ) {
    return {
      title: '生成的 TextFSM 未通过语法校验',
      description: '模型生成的候选不符合当前 TextFSM 方言。平台已保留诊断，可重新生成或在高级修订中约束字段与记录范围。',
      retryAllowed: true,
      severity: 'error' as const,
    }
  }
  if (
    failureCode === 'generation_validation_failed'
    || normalized.includes('generation_validation_failed')
    || normalized.includes('sample_replay_failed')
    || normalized.includes('duplicate_primary_key')
    || normalized.includes('primary_keys_valid')
    || normalized.includes('required_fields_complete')
  ) {
    return {
      title: '样本重放或记录校验未通过',
      description: 'TextFSM 已进入样本执行阶段，但部分记录未解析、必填值为空或主键不唯一。请查看逐样本诊断；若验收结果本身不符合原文，可先修订 ParserSpec 再重新生成。',
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
    queryClient.invalidateQueries(['parser-job-attempts-inline', jobId]),
  ])
}

export interface ParserProjectionAdapterDraft {
  checkItemId: string
  parserTemplateId: string
  sourceRecordSet: string
  adapterSpec: Record<string, unknown>
}

const ParserGenerationStep: React.FC<{
  jobId?: string | null
  lockedCheckItemId?: string | null
  onCreateAdapter?: (draft: ParserProjectionAdapterDraft) => void
}> = ({ jobId, lockedCheckItemId, onCreateAdapter }) => {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const isMobile = typeof window !== 'undefined'
    && Boolean(window.matchMedia?.('(max-width: 767px)')?.matches)
  const projectionRefreshKey = useRef<string>()

  const [specText, setSpecText] = useState('{}')
  const [specReason, setSpecReason] = useState('工程师定向修订语义方案')
  const [editingSampleId, setEditingSampleId] = useState<string>()
  const [sampleRole, setSampleRole] = useState<SampleRole>('unassigned')
  const [sampleRecordCount, setSampleRecordCount] = useState(0)
  const [sampleKeysText, setSampleKeysText] = useState('[]')
  const [sampleReason, setSampleReason] = useState('工程师修订样本角色或验收基准')
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [revisionMode, setRevisionMode] = useState(false)
  const [selectedPipelineStep, setSelectedPipelineStep] = useState<number | null>(null)
  const [advancedKeys, setAdvancedKeys] = useState<string[]>([])
  const [manualTemplateText, setManualTemplateText] = useState('')
  const [manualTemplateReason, setManualTemplateReason] = useState('')
  const [selectedCheckItemId, setSelectedCheckItemId] = useState('')
  const [projectionRecordSet, setProjectionRecordSet] = useState('')
  const [projectionText, setProjectionText] = useState('')
  const [projectionSampleIndex, setProjectionSampleIndex] = useState(0)
  const [projectionPreview, setProjectionPreview] = useState<AdapterProjectionPreviewResult>()

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
  const associatedCheckItemId = job?.generation_readiness?.check_item_binding?.check_item_id || ''
  const checkItemsQuery = useQuery(
    ['parser-check-items-for-generation'],
    () => parserApi.checkItems({ limit: 200 }),
    { enabled: Boolean(jobId) },
  )
  const attemptsQuery = useQuery(
    ['parser-job-attempts-inline', jobId],
    () => parserApi.jobAttempts(jobId || '', true),
    {
      enabled: Boolean(jobId),
      refetchInterval: ['pending', 'running', 'validating'].includes(job?.status || '')
        ? 2500
        : false,
    },
  )
  const suggestion = (job?.agent_state_json?.parser_spec_suggestion || {}) as SuggestionState
  const preferredValueNames = suggestion.result?.field_naming_context?.preferred_value_names || []
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
  const evidenceTemplate = candidateTemplate || activeTemplate
  const aiReview = candidateTemplate?.validation_json?.ai_review as {
    passed?: boolean
    summary?: string
  } | undefined
  const humanDirectReviewRequired = (
    candidateTemplate?.validation_json?.human_direct_review_required === true
  )

  const comparisonQuery = useQuery(
    ['parser-template-comparison-inline', evidenceTemplate?.id],
    () => parserApi.comparison(evidenceTemplate?.id || ''),
    { enabled: Boolean(evidenceTemplate?.id) },
  )

  useEffect(() => {
    setSpecText(JSON.stringify(job?.parser_spec_json || {}, null, 2))
  }, [job?.id, job?.parser_spec_json])

  const [parserRevisionInstruction, setParserRevisionInstruction] = useState('')

  useEffect(() => {
    setSelectedPipelineStep(null)
    setAdvancedKeys([])
    setManualTemplateText('')
    setManualTemplateReason('')
    setParserRevisionInstruction('')
    setRevisionMode(false)
    setProjectionRecordSet('')
    setProjectionText('')
    setProjectionSampleIndex(0)
    setProjectionPreview(undefined)
  }, [jobId, lockedCheckItemId])

  const structuredCheckItems = useMemo(
    () => (checkItemsQuery.data?.items || []).filter((item) => item.result_mode === 'structured'),
    [checkItemsQuery.data?.items],
  )
  const selectedCheckItem = structuredCheckItems.find(
    (item) => item.check_item_id === selectedCheckItemId,
  )

  useEffect(() => {
    setSelectedCheckItemId(associatedCheckItemId)
  }, [associatedCheckItemId])

  const parserRecordSetNames = useMemo(
    () => {
      const executedNames = Array.from(new Set(
        (comparisonQuery.data?.rows || []).flatMap((row) => (
          (row.proposed.record_sets || []).map((recordSet) => recordSet.name)
        )),
      )).filter(Boolean)
      return executedNames.length
        ? executedNames
        : semanticPlanRecordSets(job?.parser_spec_json).map((item) => item.name)
    },
    [comparisonQuery.data?.rows, job?.parser_spec_json],
  )

  useEffect(() => {
    if (parserRecordSetNames.length && !parserRecordSetNames.includes(projectionRecordSet)) {
      setProjectionRecordSet(parserRecordSetNames[0])
      setProjectionText('')
      setProjectionPreview(undefined)
    }
  }, [parserRecordSetNames, projectionRecordSet])

  const suggestSpec = useMutation(
    () => parserApi.suggestJobSpec(
      jobId || '',
      parserRevisionInstruction.trim(),
      selectedCheckItemId,
    ),
    {
      onSuccess: async () => {
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '语义方案生成失败'),
    },
  )
  const requestSpecSuggestion = suggestSpec.mutate
  const specSuggestionLoading = suggestSpec.isLoading

  const comparisonRows = comparisonQuery.data?.rows || []
  const selectedComparisonRow = comparisonRows[Math.min(
    projectionSampleIndex,
    Math.max(0, comparisonRows.length - 1),
  )]

  const generateProjection = useMutation(
    async () => {
      if (!selectedCheckItemId) throw new Error('请先选择目标 CheckItem')
      if (!evidenceTemplate?.id) throw new Error('请先生成可执行的 TextFSM 候选')
      if (!projectionRecordSet) throw new Error('请选择 Parser record set')
      return parserApi.generateAdapterProjectionCandidate({
        check_item_id: selectedCheckItemId,
        parser_template_id: evidenceTemplate.id,
        source_record_set: projectionRecordSet,
        generation_provider: 'llm',
        // Projection follows the same bounded repair loop as TextFSM.  A
        // first draft frequently needs the deterministic validator to point
        // out a missing dimension or an invalid source field.
        max_agent_attempts: 3,
      })
    },
    {
      onSuccess: (result) => {
        const proposal = result.proposal || result.validation.adapter_spec
        if (!proposal) {
          message.error(result.validation.errors.join('；') || 'LLM 未返回 Projection')
          return
        }
        setProjectionText(JSON.stringify(proposal, null, 2))
        setProjectionPreview(undefined)
        if (result.validation.success) message.success('Projection 已生成，可执行本地预览')
        else message.warning(result.validation.errors.join('；') || 'Projection 已生成，请人工调整')
      },
      onError: (error: Error) => message.error(error.message || 'Projection 生成失败'),
    },
  )

  const previewProjection = useMutation(
    async () => {
      if (!selectedComparisonRow) throw new Error('没有可预览的 Parser 执行结果')
      let adapterSpec: Record<string, unknown>
      try {
        const parsed = JSON.parse(projectionText)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
        adapterSpec = parsed as Record<string, unknown>
      } catch {
        throw new Error('Projection 必须是 JSON 对象')
      }
      const recordSets = Object.fromEntries(
        (selectedComparisonRow.proposed.record_sets || []).map((recordSet) => [
          recordSet.name,
          recordSet.records,
        ]),
      )
      return parserApi.previewCommandAdapter({
        adapter_spec: adapterSpec,
        record_sets: recordSets,
        device_id: selectedComparisonRow.device_id || 'preview-device',
        adapter_version_id: 'parser-workflow-preview',
      })
    },
    {
      onSuccess: (result) => {
        setProjectionPreview(result)
        if (result.success) message.success('标准 Observation 预览完成')
        else message.warning('预览完成，但存在 Projection 错误')
      },
      onError: (error: Error) => message.error(error.message || '本地 Projection 预览失败'),
    },
  )

  const createAdapterFromProjection = () => {
    if (!onCreateAdapter || !selectedCheckItemId || !evidenceTemplate?.id || !projectionRecordSet) return
    try {
      const parsed = JSON.parse(projectionText)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
      onCreateAdapter({
        checkItemId: selectedCheckItemId,
        parserTemplateId: evidenceTemplate.id,
        sourceRecordSet: projectionRecordSet,
        adapterSpec: parsed as Record<string, unknown>,
      })
    } catch {
      message.error('请先生成有效的 Projection JSON')
    }
  }

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
      onSuccess: async (result) => {
        const revision = Number(result?.job?.parser_spec_revision || 0)
        message.success(revision > 0 ? `语义方案修订 R${revision} 已保存` : '语义方案修订已保存')
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
        const parsed = JSON.parse(sampleKeysText) as unknown
        if (!Array.isArray(parsed) || parsed.some(
          (key) => !Array.isArray(key) || key.some((value) => !String(value).trim()),
        )) {
          throw new Error('invalid primary key baseline')
        }
        primaryKeys = parsed.map((key) => key.map((value) => String(value).trim()))
      } catch {
        throw new Error('主键基准必须是由非空字段值数组组成的 JSON 列表')
      }
      if (primaryKeys.length > 0 && primaryKeys.length !== sampleRecordCount) {
        throw new Error(
          `主键基准数量（${primaryKeys.length}）必须与记录数基准（${sampleRecordCount}）一致；仅校验记录数时请保留 []。`,
        )
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
      return parserApi.retryAndDispatchJob(jobId)
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

  const cancelGeneration = useMutation(
    () => parserApi.cancelJob(jobId || ''),
    {
      onSuccess: async () => {
        message.success('Parser 生成已取消，可调整后重新生成')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '取消 Parser 生成失败'),
    },
  )

  const submitManualCandidate = useMutation(
    async () => {
      if (!jobId) throw new Error('缺少 Parser 任务 ID')
      if (!manualTemplateText.trim()) throw new Error('请先填写或载入 TextFSM 模板')
      return parserApi.submitManualCandidate(jobId, {
        template_text: manualTemplateText,
        reason: manualTemplateReason.trim() || undefined,
      })
    },
    {
      onSuccess: async () => {
        message.success('工程师修订已通过全样本执行，等待人工语义确认')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: async (error: Error) => {
        await refreshParserState(queryClient, jobId || '')
        message.error(error.message || '工程师修订校验失败')
      },
    },
  )

  const semanticReview = useMutation(
    (reason: string) => parserApi.semanticReview(
      candidateTemplate?.id || '',
      {
        reason,
        result_confirmed: true,
        primary_key_confirmed: true,
      },
    ),
    {
      onSuccess: async () => {
        message.success('人工语义复核已保存，等待独立发布审批')
        await refreshParserState(queryClient, jobId || '')
      },
      onError: (error: Error) => message.error(error.message || '保存语义复核失败'),
    },
  )

  const approve = useMutation(
    (reason: string) => parserApi.activate(
      candidateTemplate?.id || '',
      reason,
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
    navigate(businessHref(`/skills/parser?${params.toString()}`))
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
  const generationProgress = (
    job.agent_state_json?.generation_progress || {}
  ) as ParserGenerationProgress
  const progressDetails = generationProgress.details || {}
  const currentGenerationStage = generationStageLabel[generationProgress.stage || '']
    || (job.status === 'pending' ? generationStageLabel.queued : '生成任务执行中')
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
  const fallbackExpectedRecords = (job.samples || []).reduce((total, sample) => (
    total + Number(asRecord(sample).expected_record_count || 0)
  ), 0) || undefined
  const publishedReadOnly = Boolean(activeTemplate && !candidateTemplate && !revisionMode)
  const candidateReadOnly = Boolean(candidateTemplate && !revisionMode)
  const semanticReadOnly = publishedReadOnly || candidateReadOnly
  const pipelineCurrent = activeTemplate
    ? 3
    : candidateTemplate
      ? 3
      : job.status === 'succeeded'
        ? 2
        : jobActive
          ? 2
          : job.status === 'failed'
            ? parserSpecRevised ? 1 : 2
          : semanticReady
            ? 1
            : semanticAnalysisActive ? 1 : 0
  const visiblePipelineStep = Math.min(3, Math.max(0, selectedPipelineStep ?? pipelineCurrent))
  const generationAttempts = attemptsQuery.data || job.generation_attempts || []
  const reviewedAttempts = generationAttempts.filter((attempt) => (
    Object.keys(asRecord(asRecord(attempt.validation_report_json).ai_review)).length > 0
  ))

  const sampleColumns: ColumnsType<ParserJobSample> = [
    {
      title: '样本',
      width: 240,
      render: (_, sample) => {
        const identity = sampleIdentity(sample)
        const provenance = sampleProvenanceText(sample)
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{identity.device}</Typography.Text>
            <Typography.Text type="secondary">{identity.file}</Typography.Text>
            {provenance && <Typography.Text type="secondary">{provenance}</Typography.Text>}
          </Space>
        )
      },
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
        <Button
          size="small"
          disabled={sampleEditingLocked}
          onClick={() => {
            editSample(sample)
            setAdvancedKeys(['advanced'])
          }}
        >
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
            <Space wrap>
              <Typography.Text strong>ParserSpec 与记录语义方案</Typography.Text>
              {Number(job.parser_spec_revision || 0) > 0 && (
                <Tag>R{job.parser_spec_revision}</Tag>
              )}
            </Space>
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
                  <Typography.Text type="secondary">
                    记录数可单独作为验收基准；如填写主键，JSON 中的主键条目数必须与记录数一致。
                  </Typography.Text>
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

      <section className="parser-generation-step">
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>工程师修订 TextFSM</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              修订内容会在全部冻结样本上重新执行；通过后形成新的不可变候选，仍需人工确认解析结果和主键。
            </Typography.Text>
          </div>
          <Button
            icon={<CheckOutlined />}
            disabled={
              jobActive
              || Boolean(candidateTemplate)
              || (Boolean(activeTemplate) && !revisionMode)
            }
            loading={submitManualCandidate.isLoading}
            onClick={() => submitManualCandidate.mutate()}
          >
            校验并提交候选
          </Button>
        </div>
        {candidateTemplate && (
          <Alert
            type="warning"
            showIcon
            message="当前已有待审批候选"
            description="请先审查并接受或驳回当前候选，再提交新的工程师修订版本。"
          />
        )}
        <Input.TextArea
          aria-label="工程师修订 TextFSM"
          value={manualTemplateText}
          onChange={(event) => setManualTemplateText(event.target.value)}
          autoSize={{ minRows: 12, maxRows: 28 }}
          style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          placeholder="从任一生成轮次载入模板，或粘贴完整 TextFSM / ParserBundle"
        />
        <Input
          aria-label="工程师修订说明"
          value={manualTemplateReason}
          onChange={(event) => setManualTemplateReason(event.target.value)}
          placeholder="修订说明（可选）"
        />
      </section>
    </Space>
  )

  const pipelineStepContent = [
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>先确认 CLI 样本</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              先判断输出是否包含真实业务记录，再进入语义设计。空结果和错误输出不能单独用于生成 Parser。
            </Typography.Text>
          </div>
          <Tag>{job.sample_count} 份样本</Tag>
        </div>
        <Table
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: 820 }}
          dataSource={job.samples || []}
          columns={sampleColumns}
        />
        <Collapse
          ghost
          defaultActiveKey={(job.samples || []).slice(0, 1).map((sample) => sample.id)}
          items={(job.samples || []).map((sample) => ({
            key: sample.id,
            label: (() => {
              const identity = sampleIdentity(sample)
              const provenance = sampleProvenanceText(sample)
              return (
                <Space wrap>
                  <Typography.Text strong>{identity.device}</Typography.Text>
                  <Typography.Text type="secondary">{identity.file}</Typography.Text>
                  {provenance && <Typography.Text type="secondary">{provenance}</Typography.Text>}
                  <Tag>{roleLabel[sample.sample_role || 'unassigned']}</Tag>
                </Space>
              )
            })(),
            children: (
              <pre style={{ ...codePanelStyle, maxHeight: 420, whiteSpace: 'pre-wrap' }}>
                {sample.sanitized_text || sample.sanitized_preview || '无可展示的脱敏样本内容'}
              </pre>
            ),
          }))}
        />
        {needsPositiveSample ? (
          <Alert
            type="warning"
            showIcon
            message="当前样本没有可用于设计字段的真实记录"
            description="请将正常空输出标记为负样本，或补充一份包含真实业务记录的同命令输出。"
            action={<Button onClick={openSampleImport}>导入补充样本</Button>}
          />
        ) : (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              type="primary"
              disabled={!job.sample_count}
              onClick={() => setSelectedPipelineStep(1)}
            >
              样本已确认，进入语义设计 <ArrowRightOutlined />
            </Button>
          </div>
        )}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>设计并确认语义方案</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              明确每条记录代表什么、主键是什么、需要提取哪些字段，再让 LLM 生成 Parser。
            </Typography.Text>
          </div>
          {semanticReadOnly ? (
            <Tag>只读历史版本</Tag>
          ) : (
            <Button
              icon={<SettingOutlined />}
              onClick={() => setAdvancedKeys(['advanced'])}
            >
              专家模式
            </Button>
          )}
        </div>
        <div className="parser-generation-sequence">
          <section className="parser-generation-step">
            <Typography.Text strong>1. CLI 证据</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 12px' }}>
              语义方案中的记录和字段必须能在这些脱敏输出中找到依据。
            </Typography.Paragraph>
            <Collapse
              ghost
              defaultActiveKey={(job.samples || []).slice(0, 1).map((sample) => sample.id)}
              items={(job.samples || []).map((sample) => ({
                key: sample.id,
                label: (() => {
                  const identity = sampleIdentity(sample)
                  const provenance = sampleProvenanceText(sample)
                  return (
                    <Space wrap>
                      <Typography.Text strong>{identity.device}</Typography.Text>
                      {provenance && <Typography.Text type="secondary">{provenance}</Typography.Text>}
                      <Tag>{classificationLabel[sample.content_classification?.status || ''] || '待判定'}</Tag>
                    </Space>
                  )
                })(),
                children: (
                  <pre style={{ ...codePanelStyle, maxHeight: 560, whiteSpace: 'pre-wrap' }}>
                    {sample.sanitized_text || sample.sanitized_preview || '无可展示的脱敏样本内容'}
                  </pre>
                ),
              }))}
            />
          </section>
          <section className="parser-generation-step">
            <Typography.Text strong>2. 目标标准语义 B</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 12px' }}>
              {associatedCheckItemId
                ? '使用已关联检查项的语义和字段要求制作 TextFSM 与 Projection。'
                : '尚未关联有效检查项，请返回检查项目录完成关联后继续。'}
            </Typography.Paragraph>
            <Select
              aria-label="目标 CheckItem"
              value={selectedCheckItemId || undefined}
              disabled
              onChange={(value) => {
                setSelectedCheckItemId(value)
                setProjectionText('')
                setProjectionPreview(undefined)
              }}
              placeholder="请先在待归类检查项中完成关联"
              loading={checkItemsQuery.isLoading}
              showSearch
              optionFilterProp="label"
              options={structuredCheckItems.map((item) => ({
                value: item.check_item_id,
                label: `${item.display_name || item.name} (${item.check_item_id})`,
              }))}
              style={{ width: '100%' }}
            />
            {selectedCheckItem && (
              <pre style={{ ...codePanelStyle, maxHeight: 320, marginTop: 12, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(selectedCheckItem.canonical_contract, null, 2)}
              </pre>
            )}
          </section>
          <section className="parser-generation-step">
            <Typography.Text strong>3. 结构化语义方案</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 12px' }}>
              LLM 只负责提出方案；最终是否进入生成由你确认。
            </Typography.Paragraph>
            <SemanticPlanOverview
              parserSpec={job.parser_spec_json}
              fallbackExpectedRecords={fallbackExpectedRecords}
            />
            {!semanticReadOnly && <div style={{ marginTop: 16 }}>
              <Typography.Text strong>告诉 AI 如何设计或调整</Typography.Text>
              <Input.TextArea
                aria-label="AI ParserSpec 修订要求"
                value={parserRevisionInstruction}
                onChange={(event) => setParserRevisionInstruction(event.target.value)}
                placeholder="例如：每个物理电源一条记录；主键为 slot + power_supply；必须提取 State、Mode、电流、电压和输出功率。"
                autoSize={{ minRows: 4, maxRows: 8 }}
                maxLength={4000}
                showCount
                disabled={semanticAnalysisActive}
                style={{ marginTop: 8 }}
              />
              <Button
                icon={<RobotOutlined />}
                loading={specSuggestionLoading}
                disabled={
                  semanticAnalysisActive
                  || !selectedCheckItemId
                  || (semanticReady && !parserRevisionInstruction.trim())
                }
                onClick={() => requestSpecSuggestion()}
                style={{ marginTop: 10 }}
              >
                {semanticReady ? '按要求更新语义方案' : '让 LLM 提出语义方案'}
              </Button>
            </div>}
          </section>
        </div>
        {(preferredValueNames.length > 0 || suggestion.result?.rationale) && (
          <Collapse
            ghost
            items={[{
              key: 'technical-basis',
              label: '技术依据与字段命名建议',
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {preferredValueNames.length > 0 && (
                    <div>
                      <Space wrap size={[6, 6]}>
                        <Typography.Text strong>推荐 TextFSM Value</Typography.Text>
                        {preferredValueNames.map((name) => <Tag key={name}>{name}</Tag>)}
                      </Space>
                      <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                        与样本语义、单位和记录粒度一致时优先采用；存在真实差异时保留源字段。
                      </Typography.Paragraph>
                    </div>
                  )}
                  {suggestion.result?.rationale && (
                    <Alert
                      type="info"
                      showIcon
                      message="AI 方案依据（模型原文）"
                      description={suggestion.result.rationale}
                    />
                  )}
                </Space>
              ),
            }]}
          />
        )}
        {parserSpecRevised && (
          <Alert
            type="warning"
            showIcon
            message="ParserSpec 已更新，等待重新确认"
            description="旧候选与当前语义方案不再一致。请核对上方记录集、字段和主键后，再生成新的 Parser。"
          />
        )}
        {suggestion.status === 'failed' && (
          <Alert
            type="error"
            showIcon
            message="语义方案生成失败"
            description={suggestion.error}
            action={<Button loading={specSuggestionLoading} onClick={() => requestSpecSuggestion()}>重新分析</Button>}
          />
        )}
        {semanticAnalysisActive && (
          <section className="parser-generation-progress" aria-live="polite">
            <div className="parser-generation-progress__header">
              <Space wrap size={8}>
                <Spin size="small" />
                <Typography.Text strong>正在分析样本并生成语义方案</Typography.Text>
                <Tag>{suggestion.status === 'queued' ? '等待 AI Worker' : '模型分析中'}</Tag>
              </Space>
              <Typography.Text type="secondary">已运行 {formatElapsed(suggestion.elapsed_ms)}</Typography.Text>
            </div>
            <div className="parser-generation-progress__facts">
              <span>任务 {suggestion.task_id ? suggestion.task_id.slice(0, 8) : '待分配'}</span>
              <span>
                最近心跳 {suggestion.heartbeat_at
                  ? new Date(suggestion.heartbeat_at).toLocaleTimeString()
                  : '等待 Worker 接管'}
              </span>
              {suggestion.deadline_at && (
                <span>分析截止 {new Date(suggestion.deadline_at).toLocaleTimeString()}</span>
              )}
            </div>
          </section>
        )}
        {publishedReadOnly && (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              type="primary"
              onClick={() => {
                setRevisionMode(true)
                setParserRevisionInstruction('')
              }}
            >
              基于此版本创建修订
            </Button>
          </div>
        )}
        {candidateReadOnly && (
          <Alert
            type="info"
            showIcon
            message="候选版本正在等待人工确认"
            description="当前语义方案只读；如需修改，请先在人工确认阶段驳回候选。"
            action={<Button onClick={() => setSelectedPipelineStep(3)}>前往人工确认</Button>}
          />
        )}
        {semanticReady && !semanticReadOnly && (
          <div
            style={{
              alignItems: 'center',
              borderTop: '1px solid var(--border-subtle, #e5e7eb)',
              display: 'flex',
              justifyContent: 'space-between',
              paddingTop: 14,
            }}
          >
            <Typography.Text type="secondary">确认后才会调用 LLM 生成 TextFSM，并自动执行样本校验。</Typography.Text>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={
                !readiness?.ready
                || semanticAnalysisActive
                || (Boolean(activeTemplate) && !parserSpecRevised)
              }
              loading={dispatchLoading || retryAndDispatch.isLoading}
              onClick={() => (
                // A confirmed/revised ParserSpec leaves a new job in
                // ``collecting``.  That state is directly dispatchable; it
                // is not a failed run and must never be sent through Retry.
                // Retry is only for a terminal/non-dispatchable prior run.
                ['collecting', 'pending'].includes(job.status)
                  ? dispatchGeneration()
                  : retryAndDispatch.mutate()
              )}
            >
              {activeTemplate ? '确认修订方案并生成新版本' : '确认语义方案并生成 Parser'}
            </Button>
          </div>
        )}
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>TextFSM 生成轮次</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              先核对 CLI 原始输出，再逐轮查看模板、实际解析结果和诊断；LLM 异议会进入下一轮，最多三轮。
            </Typography.Text>
          </div>
          {!jobActive && readiness?.ready && !candidateTemplate && (
            <Button
              icon={<PlayCircleOutlined />}
              loading={retryAndDispatch.isLoading}
              onClick={() => retryAndDispatch.mutate()}
            >
              从当前方案重新生成
            </Button>
          )}
        </div>
        <div className="parser-generation-sequence">
          <section className="parser-generation-step">
            <Typography.Text strong>1. CLI 验证证据</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 12px' }}>
              对照原始输出判断是否漏行、错列或错误合并记录。
            </Typography.Paragraph>
            <Collapse
              ghost
              defaultActiveKey={(job.samples || []).slice(0, 1).map((sample) => sample.id)}
              items={(job.samples || []).map((sample) => ({
                key: sample.id,
                label: (() => {
                  const identity = sampleIdentity(sample)
                  const provenance = sampleProvenanceText(sample)
                  return provenance ? `${identity.device} · ${provenance}` : identity.device
                })(),
                children: (
                  <pre style={{ ...codePanelStyle, maxHeight: 560, whiteSpace: 'pre-wrap' }}>
                    {sample.sanitized_text || sample.sanitized_preview || '无可展示的脱敏样本内容'}
                  </pre>
                ),
              }))}
            />
          </section>
          <section className="parser-generation-step">
            <Typography.Text strong>2. 模板与实际解析结果</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 12px' }}>
              按生成轮次核对 TextFSM 模板、实际记录、字段、样本覆盖和复核诊断；没有模板正文时不显示候选模板区。
            </Typography.Paragraph>
            <ParserAttemptHistory
              attempts={generationAttempts}
              loading={attemptsQuery.isLoading}
              fallbackTemplateText={comparisonQuery.data?.candidate_template.content}
              mode="all"
              onUseTemplate={(attempt) => {
                setManualTemplateText(attempt.template_text || '')
                setManualTemplateReason(`基于候选记录 ${attempt.attempt_number} 修订`)
                setAdvancedKeys(['advanced'])
              }}
            />
          </section>
        </div>
        <section className="parser-generation-step">
          <div className="parser-generation-step__heading">
            <div>
              <Typography.Text strong>{selectedCheckItem?.canonical_contract?.value_contract === 'same-name/v1' ? '3. 同名 Value 校验与检查项数据' : '3. A→B Projection 与标准 Observation'}</Typography.Text>
              <br />
              <Typography.Text type="secondary">
                {selectedCheckItem?.canonical_contract?.value_contract === 'same-name/v1' ? 'TextFSM Value 直接对应检查项字段；本地校验名称、语义和实际样本，不再生成字段映射。' : '使用同一个 CheckItem B 生成 Projection，并用当前 Parser 的实际记录做本地预览。'}
              </Typography.Text>
            </div>
            <Space wrap>
              <Button
                icon={<RobotOutlined />}
                disabled={!selectedCheckItemId || !evidenceTemplate || !projectionRecordSet}
                loading={generateProjection.isLoading}
                onClick={() => generateProjection.mutate()}
              >
                {selectedCheckItem?.canonical_contract?.value_contract === 'same-name/v1' ? '校验同名字段' : 'LLM 生成 Projection'}
              </Button>
              <Button
                icon={<PlayCircleOutlined />}
                disabled={!projectionText.trim() || !selectedComparisonRow}
                loading={previewProjection.isLoading}
                onClick={() => previewProjection.mutate()}
              >
                本地执行
              </Button>
            </Space>
          </div>
          {!evidenceTemplate ? (
            <Alert type="info" showIcon message="TextFSM 候选生成后即可继续 A→B 预览" />
          ) : (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap style={{ width: '100%' }}>
                <Select
                  aria-label="Projection 目标 CheckItem"
                  value={selectedCheckItemId || undefined}
                  disabled
                  onChange={(value) => {
                    setSelectedCheckItemId(value)
                    setProjectionText('')
                    setProjectionPreview(undefined)
                  }}
                  placeholder="请先在待归类检查项中完成关联"
                  options={structuredCheckItems.map((item) => ({
                    value: item.check_item_id,
                    label: `${item.display_name || item.name} (${item.check_item_id})`,
                  }))}
                  style={{ minWidth: 320 }}
                />
                <Select
                  aria-label="Parser record set"
                  value={projectionRecordSet || undefined}
                  onChange={(value) => {
                    setProjectionRecordSet(value)
                    setProjectionText('')
                    setProjectionPreview(undefined)
                  }}
                  placeholder="选择 record set"
                  options={parserRecordSetNames.map((name) => ({ value: name, label: name }))}
                  style={{ minWidth: 220 }}
                />
                <Select
                  aria-label="Projection 预览样本"
                  value={projectionSampleIndex}
                  onChange={(value) => {
                    setProjectionSampleIndex(value)
                    setProjectionPreview(undefined)
                  }}
                  loading={comparisonQuery.isLoading}
                  options={comparisonRows.map((row, index) => ({
                    value: index,
                    label: `${row.device_id} · ${row.dataset_id || `样本 ${index + 1}`}`,
                  }))}
                  style={{ minWidth: 280 }}
                />
              </Space>
              {selectedCheckItem?.canonical_contract?.value_contract !== 'same-name/v1' && <details><summary>编辑字段转换规则（高级）</summary>
              <Input.TextArea
                aria-label="Adapter Projection JSON"
                value={projectionText}
                onChange={(event) => {
                  setProjectionText(event.target.value)
                  setProjectionPreview(undefined)
                }}
                placeholder="点击“LLM 生成 Projection”，或粘贴已有 AdapterSpecV2 JSON"
                autoSize={{ minRows: 8, maxRows: 20 }}
                style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              />
              </details>}
              {onCreateAdapter && (
                <Alert
                  type={projectionPreview?.success ? 'success' : 'info'}
                  showIcon
                  message="完成本地预览后创建命令适配器"
                  description={selectedCheckItem?.canonical_contract?.value_contract === 'same-name/v1'
                    ? '使用当前解析器和同名字段创建命令适配器草稿，通过验证与发布流程后生效。'
                    : '这会带着当前 Parser 和 Projection 打开命令适配器草稿；适配器仍需按正常流程验证和审批，未审批前不会进入生产。'}
                  action={(
                    <Button
                      type="primary"
                      disabled={!projectionPreview?.success}
                      onClick={createAdapterFromProjection}
                    >
                      {selectedCheckItem?.canonical_contract?.value_contract === 'same-name/v1' ? '使用同名字段创建适配器' : '使用 Projection 创建适配器'}
                    </Button>
                  )}
                />
              )}
              <div style={{
                display: 'grid',
                gap: 12,
                gridTemplateColumns: 'minmax(0, 1fr)',
              }}>
                <div>
                  <Typography.Text strong>CLI 原文</Typography.Text>
                  <pre style={{ ...codePanelStyle, maxHeight: 420, whiteSpace: 'pre-wrap' }}>
                    {selectedComparisonRow?.raw_text || '请选择样本'}
                  </pre>
                </div>
                <div>
                  <Typography.Text strong>生成的 TextFSM</Typography.Text>
                  <pre style={{ ...codePanelStyle, maxHeight: 420, whiteSpace: 'pre', overflow: 'auto' }}>
                    {comparisonQuery.data?.candidate_template.content || (comparisonQuery.isLoading ? '正在加载 TextFSM…' : '当前模板暂无可显示的 TextFSM 内容')}
                  </pre>
                </div>
                <div>
                  <Typography.Text strong>实际解析结果</Typography.Text>
                  {(selectedComparisonRow?.proposed.record_sets || []).map((recordSet, index) => <section key={index}>
                    <Typography.Paragraph type="secondary">{recordSet.name}</Typography.Paragraph>
                    <StandardResultTable records={recordSet.records || []} />
                  </section>)}
                </div>
                <div>
                  <Typography.Text strong>标准检查结果</Typography.Text>
                  {projectionPreview ? projectionPreview.batches.map((batch, index) => <section key={index}>
                    <Space><Tag>{batch.check_item_id}</Tag><Tag color={batch.errors.length ? 'orange' : 'blue'}>{batch.quality_status}</Tag></Space>
                    {batch.errors.map(error => <Alert key={error} type="warning" message={error} />)}
                    <StandardResultTable records={batch.records} />
                  </section>) : <Typography.Paragraph type="secondary">生成字段转换后点击“本地执行”，对照原文校验。</Typography.Paragraph>}
                  <details><summary>查看完整执行证据</summary><pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap' }}>{JSON.stringify(projectionPreview || {}, null, 2)}</pre></details>
                </div>
              </div>
            </Space>
          )}
        </section>
        <Collapse
          ghost
          items={[{
            key: 'ai-review-history',
            label: `AI 复核记录 (${reviewedAttempts.length})`,
            children: (
              <ParserAttemptHistory
                attempts={reviewedAttempts}
                loading={attemptsQuery.isLoading}
                mode="execution"
              />
            ),
          }]}
        />
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>LLM 独立复核历史</Typography.Text>
        <Typography.Text type="secondary">
          复核模型只评价当前轮实际解析结果，不继承上一轮结论；异议会原样回送生成模型修复。
        </Typography.Text>
        <ParserAttemptHistory attempts={reviewedAttempts} loading={attemptsQuery.isLoading} />
      </Space>
    ),
    (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text strong>人工最终确认</Typography.Text>
        {candidateTemplate ? (
          <Alert
            type={humanDirectReviewRequired ? 'warning' : 'success'}
            showIcon
            message={humanDirectReviewRequired ? 'LLM 仍有异议，等待工程师直接判断' : '候选已进入人工终审'}
            description={aiReview?.summary || '请核对样本、模板、逐样本解析结果和主键。'}
            action={<Button onClick={() => setApprovalOpen(true)}>审查并确认</Button>}
          />
        ) : activeTemplate ? (
          <Alert
            type="success"
            showIcon
            message="Parser 已发布"
            description={`版本 ${activeTemplate.version} 已生效，审批证据和解析结果已冻结。`}
            action={<Button onClick={() => setApprovalOpen(true)}>查看完整审批证据</Button>}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未形成可供人工确认的候选" />
        )}
      </Space>
    ),
  ][[0, 1, 2, 4][visiblePipelineStep]]

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
          <Tag color={candidateTemplate ? 'gold' : activeTemplate ? 'green' : parserSpecRevised ? 'gold' : undefined}>
            {candidateTemplate
              ? '待人工确认'
              : activeTemplate
              ? '已发布'
              : parserSpecRevised
                ? '待重新生成'
                : jobStatusLabel[job.status] || job.status}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      {publishedReadOnly && (
        <Alert
          type="info"
          showIcon
          message="正在查看已发布版本"
          description="当前内容只读，不会修改生产 Parser。需要调整时，请先创建修订版本。"
        />
      )}
      {revisionMode && activeTemplate && !candidateTemplate && (
        <Alert
          type="warning"
          showIcon
          message="正在创建修订版本"
          description={`生产版本 ${activeTemplate.version} 保持生效；只有新候选通过验证并再次审批后才会替换。`}
        />
      )}

      <section className="parser-generation-step">
        <div className="parser-generation-step__heading">
          <div>
            <Typography.Text strong>Parser 生产工作流</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              先核对 CLI 证据和语义方案，确认后再生成、验证并发布。
            </Typography.Text>
          </div>
          {jobActive && (
            <Space size={8} wrap>
              <Popconfirm
                title="取消本次 Parser 生成？"
                description="当前模型调用会停止，已保存的样本、ParserSpec 和历史轮次不会丢失。"
                okText="确认取消"
                cancelText="继续等待"
                onConfirm={() => cancelGeneration.mutate()}
              >
                <Button
                  danger
                  icon={<StopOutlined />}
                  loading={cancelGeneration.isLoading}
                >
                  取消生成
                </Button>
              </Popconfirm>
              <Button loading={recover.isLoading} onClick={() => recover.mutate()}>
                恢复无进展任务
              </Button>
            </Space>
          )}
        </div>
        <Steps
          current={visiblePipelineStep}
          onChange={(step) => {
            if (step <= pipelineCurrent) setSelectedPipelineStep(step)
          }}
          responsive
          items={[
            {
              title: '样本证据',
              status: job.sample_count > 0 ? 'finish' : 'error',
              description: '查看 CLI 并判断内容',
            },
            {
              title: '语义设计',
              status: suggestion.status === 'failed'
                ? 'error'
                : semanticReady
                  ? 'finish'
                  : ['queued', 'running'].includes(suggestion.status || '')
                    ? 'process'
                    : 'wait',
              description: semanticReady ? '等待人工确认' : '定义记录、字段与主键',
            },
            {
              title: '生成与验证',
              status: parserSpecRevised
                ? 'wait'
                : job.status === 'failed'
                ? 'error'
                : ['pending', 'running', 'validating'].includes(job.status)
                  ? 'process'
                  : ['succeeded'].includes(job.status)
                    ? 'finish'
                    : 'wait',
              description: humanDirectReviewRequired
                ? '本地通过，AI 有异议'
                : reviewPassed
                  ? '本地执行与 AI 复核通过'
                  : '本地执行、结果取证与 AI 复核',
            },
            {
              title: '发布确认',
              status: candidateTemplate ? 'process' : activeTemplate ? 'finish' : 'wait',
              description: candidateTemplate ? '确认结果与主键' : activeTemplate ? '已发布' : '等待候选',
            },
          ]}
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
              正在查看：{['样本证据', '语义设计', '生成与验证', '发布确认'][visiblePipelineStep]}
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
            {visiblePipelineStep !== pipelineCurrent && (
              <Button onClick={() => setSelectedPipelineStep(null)}>回到当前进度</Button>
            )}
          </Space>
        </div>
        <div style={{ marginTop: 16 }}>
          {pipelineStepContent}
        </div>
      </section>

      {jobActive && visiblePipelineStep === 2 && (
        <section className="parser-generation-progress" aria-live="polite">
          <div className="parser-generation-progress__header">
            <Space wrap size={8}>
              <Spin size="small" />
              <Typography.Text strong>{currentGenerationStage}</Typography.Text>
              <Tag>{`第 ${generationProgress.attempt_number || 1} 轮`}</Tag>
            </Space>
            <Typography.Text type="secondary">
              已运行 {formatElapsed(generationProgress.elapsed_ms)}
            </Typography.Text>
          </div>
          <div className="parser-generation-progress__facts">
            <span>
              模型调用 {Number(progressDetails.model_calls_used ?? job.model_calls_used ?? 0)}
              /{Number(progressDetails.model_call_limit ?? 3)}
            </span>
            <span>
              最近心跳 {generationProgress.heartbeat_at
                ? new Date(generationProgress.heartbeat_at).toLocaleTimeString()
                : '等待 Worker 接管'}
            </span>
            {generationProgress.deadline_at && (
              <span>任务截止 {new Date(generationProgress.deadline_at).toLocaleTimeString()}</span>
            )}
            {progressDetails.diagnostic_code && (
              <span>最近诊断 {String(progressDetails.diagnostic_code)}</span>
            )}
          </div>
        </section>
      )}

      {job.status === 'failed' && visiblePipelineStep === 2 && (
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

      {!activeTemplate && candidateTemplate && visiblePipelineStep === 2 && (
        <Alert
          type={humanDirectReviewRequired ? 'warning' : 'success'}
          showIcon
          message="候选正在等待人工确认"
          description="当前正在回看生成过程；请返回人工确认步骤核对原始输出、解析结果、主键和模板。"
          action={<Button onClick={() => setSelectedPipelineStep(3)}>前往人工确认</Button>}
        />
      )}

      {activeTemplate && !candidateTemplate && visiblePipelineStep === 2 && (
        <Alert
          type="success"
          showIcon
          message="Parser 已发布"
          description={`版本 ${activeTemplate.version} 已进入运行时；审批时确认的解析结果已冻结为回归基线。`}
          action={(
            <Space wrap>
              <Button onClick={() => setApprovalOpen(true)}>
                查看审批证据
              </Button>
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
                navigate(businessHref(`/skills/parser?${params.toString()}`))
                }}
              >
                回填历史样本
              </Button>
            </Space>
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
              aria-label="重新生成"
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

      {!semanticReadOnly && (
        <Collapse
          ghost
          activeKey={advancedKeys}
          onChange={(keys) => setAdvancedKeys(Array.isArray(keys) ? keys.map(String) : [String(keys)])}
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
      )}

      <ParserTemplateApprovalDrawer
        open={approvalOpen}
        onClose={() => setApprovalOpen(false)}
        isMobile={isMobile}
        loading={comparisonQuery.isLoading}
        comparison={comparisonQuery.data}
        readOnly={!candidateTemplate && Boolean(activeTemplate)}
        reviewing={semanticReview.isLoading}
        approving={approve.isLoading}
        rejecting={reject.isLoading}
        onReview={(reason) => semanticReview.mutate(reason)}
        onApprove={(reason) => approve.mutate(reason)}
        onReject={() => reject.mutate()}
      />
    </Space>
  )
}

export default ParserGenerationStep
