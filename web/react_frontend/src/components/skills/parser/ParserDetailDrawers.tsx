import React, { useEffect, useState } from 'react'
import { EyeOutlined, RobotOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import type {
  ParserGenerationAttempt,
  ParserJobItem,
  ParserRuleGenerationJobPreview,
  ParserTemplatePreview,
} from '../../../services/api'
import {
  codePanelStyle,
  governanceStatusLabel,
  shortDigest,
  statusColor,
  successTag,
} from './parserUi'
import StandardTableShell from './StandardTableShell'

const loadingPanel = (
  <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}>
    <Spin />
  </div>
)

interface CommonDrawerProps {
  open: boolean
  onClose: () => void
  isMobile: boolean
  loading: boolean
}

const attemptActionLabel = (value: string) => ({
  propose_contract: '提出 Contract',
  revise_contract: '修订 Contract',
  generate_template: '生成模板',
  repair_syntax: '修复语法',
  repair_entity_anchor: '修复实体锚点',
  repair_record_boundary: '修复记录边界',
  repair_field_capture: '修复字段捕获',
  repair_key_integrity: '修复主键',
  tighten_noise_filter: '收紧噪声过滤',
  repair_template: '修订模板',
  request_contract_review: '重新审查 Contract',
  'deterministic:table': '表格编译器',
  'deterministic:key_value': '键值编译器',
  'deterministic:repeated_block': '重复块编译器',
  llm_generate: 'LLM 生成',
  llm_repair: 'LLM 修复',
  llm_tool_round_1: 'LLM 生成与本地校验',
  llm_tool_round_2: 'LLM 修复与本地校验',
  llm_tool_round_3: 'LLM 最终修复与本地校验',
}[value] || value)

const attemptStageLabel = (value: string) => ({
  contract: 'Contract',
  template: '模板生成',
  validation: '确定性验证',
  holdout: '留出集',
  regression: '历史回归',
}[value] || value)

const attemptTimelineItem = (attempt: ParserGenerationAttempt) => {
  const generationReport = attempt.validation_report_json?.generation as Record<string, unknown> | undefined
  const directDiagnostics = attempt.validation_report_json?.diagnostics
  const nestedDiagnostics = generationReport?.diagnostics
  const diagnostics = Array.isArray(directDiagnostics)
    ? directDiagnostics
    : Array.isArray(nestedDiagnostics) ? nestedDiagnostics : []
  const diagnostic = diagnostics[0]
  const tokenUsage = attempt.token_usage_json || {}
  const totalTokens = Number(
    tokenUsage.total_tokens
      || (Number(tokenUsage.input_tokens || 0) + Number(tokenUsage.output_tokens || 0)),
  )
  return {
    color: attempt.status === 'passed' ? 'green' : attempt.status === 'failed' ? 'red' : 'blue',
    children: (
      <Space direction="vertical" size={3} style={{ width: '100%' }}>
        <Space wrap>
          <Typography.Text strong>第 {attempt.attempt_number} 轮</Typography.Text>
          <Tag>{attemptStageLabel(attempt.stage)}</Tag>
          <Tag color={attempt.status === 'passed' ? 'success' : 'error'}>{attempt.status}</Tag>
        </Space>
        <Typography.Text>{attemptActionLabel(attempt.action)}</Typography.Text>
        <Typography.Text type="secondary">
          {attempt.model_name || attempt.model_provider || '确定性工具'}
          {attempt.latency_ms ? ` · ${attempt.latency_ms} ms` : ''}
          {totalTokens > 0 ? ` · ${totalTokens} tokens` : ''}
          {attempt.created_at ? ` · ${attempt.created_at}` : ''}
        </Typography.Text>
        {diagnostic ? (
          <Typography.Text type="danger">
            {typeof diagnostic === 'string'
              ? diagnostic
              : `${String((diagnostic as Record<string, unknown>).code || 'validation_failed')}：${String((diagnostic as Record<string, unknown>).message || '')}`}
          </Typography.Text>
        ) : null}
      </Space>
    ),
  }
}

export const ParserTemplatePreviewDrawer: React.FC<
  CommonDrawerProps & { preview?: ParserTemplatePreview }
> = ({ open, onClose, isMobile, loading, preview }) => (
  <Drawer
    title="解析模板预览"
    open={open}
    onClose={onClose}
    width={isMobile ? '100vw' : 720}
    destroyOnClose
  >
    {loading ? loadingPanel : preview ? (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="厂商">{preview.vendor}</Descriptions.Item>
          <Descriptions.Item label="型号">{preview.profile}</Descriptions.Item>
          <Descriptions.Item label="命令">{preview.canonical_command}</Descriptions.Item>
          <Descriptions.Item label="类别">{preview.category}</Descriptions.Item>
          <Descriptions.Item label="版本">{preview.version}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={statusColor(preview.status)}>{preview.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="哈希校验" span={2}>
            <Tag color={preview.hash_valid ? 'green' : 'red'}>
              {preview.hash_valid ? '通过' : '失败'}
            </Tag>
            <Typography.Text copyable style={{ marginLeft: 8 }}>{preview.sha256}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
        <Typography.Title level={5} style={{ margin: 0 }}>TextFSM 模板</Typography.Title>
        <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 480 }}>
          {preview.content}
        </Typography.Paragraph>
        <Typography.Title level={5} style={{ margin: 0 }}>验证结果</Typography.Title>
        <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 280, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(preview.validation, null, 2)}
        </Typography.Paragraph>
      </Space>
    ) : null}
  </Drawer>
)

export const RuleJobPreviewDrawer: React.FC<
  CommonDrawerProps & { preview?: ParserRuleGenerationJobPreview }
> = ({ open, onClose, isMobile, loading, preview }) => (
  <Drawer
    title="规则资产审批前预览"
    open={open}
    onClose={onClose}
    width={isMobile ? '100vw' : 920}
    destroyOnClose
  >
    {loading ? loadingPanel : preview ? (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          showIcon
          type={preview.approval_readiness.can_approve ? 'success' : 'warning'}
          message={preview.approval_readiness.can_approve ? '候选资产满足审批前置条件' : '候选资产仍有审批阻断'}
          description={preview.approval_readiness.blockers.length
            ? preview.approval_readiness.blockers.join('；')
            : '静态验证、资产引用和历史回放摘要均可作为审批证据。'}
        />
        <Descriptions size="small" column={isMobile ? 1 : 3} bordered>
          <Descriptions.Item label="任务类型">{preview.job.job_type}</Descriptions.Item>
          <Descriptions.Item label="任务状态">
            <Tag color={statusColor(preview.job.status)}>{governanceStatusLabel(preview.job.status)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="资产类型">{String(preview.asset_summary.asset_type || '-')}</Descriptions.Item>
          <Descriptions.Item label="资产引用" span={isMobile ? 1 : 2}>
            <Typography.Text copyable={Boolean(preview.asset_summary.asset_ref)}>
              {String(preview.asset_summary.asset_ref || preview.asset_summary.asset_version_id || '-')}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="资产摘要">
            <Typography.Text copyable={Boolean(preview.asset_summary.asset_digest)}>
              {shortDigest(String(preview.asset_summary.asset_digest || ''))}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="验证">{successTag(preview.validation_summary.success)}</Descriptions.Item>
          <Descriptions.Item label="生成器">{String(preview.validation_summary.generator || '-')}</Descriptions.Item>
          <Descriptions.Item label="测试用例">{String(preview.validation_summary.case_count || '-')}</Descriptions.Item>
          <Descriptions.Item label="历史回放">
            {preview.historical_replay ? successTag(preview.historical_replay.success) : <Tag>未执行</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="回放命中">{String(preview.historical_replay?.matched_change_count ?? '-')}</Descriptions.Item>
          <Descriptions.Item label="回放冲突">{String(preview.historical_replay?.collision_count ?? '-')}</Descriptions.Item>
          <Descriptions.Item label="Agent Loop">
            {preview.agent_loop
              ? preview.agent_loop.max_attempts_per_target
                ? `${preview.agent_loop.target_count || 1} 个生成目标 · ${preview.agent_loop.total_attempt_count || preview.agent_loop.attempt_count || 0} 次模型尝试 · 单目标最多 ${preview.agent_loop.max_attempts_per_target} 轮`
                : `${preview.agent_loop.attempt_count || 0} 次历史模型尝试（旧版记录未按目标去重）`
              : '-'}
          </Descriptions.Item>
        </Descriptions>
        {[
          ['候选资产摘要', preview.candidate_preview, 320],
          ['输入摘要', preview.payload_summary, 240],
          ['验证报告', preview.validation_report, 360],
        ].map(([title, value, maxHeight]) => (
          <React.Fragment key={String(title)}>
            <Typography.Title level={5} style={{ margin: 0 }}>{String(title)}</Typography.Title>
            <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: Number(maxHeight), whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(value || {}, null, 2)}
            </Typography.Paragraph>
          </React.Fragment>
        ))}
      </Space>
    ) : null}
  </Drawer>
)

export const ParserJobDetailDrawer: React.FC<
  CommonDrawerProps & {
    job?: ParserJobItem
    configuring?: boolean
    onSaveSpec?: (
      jobId: string,
      parserSpec: Record<string, unknown>,
      confirmed: boolean,
      reason: string,
    ) => Promise<unknown>
    onSaveSampleRole?: (
      jobId: string,
      sampleId: string,
      role: 'unassigned' | 'generation' | 'acceptance' | 'negative',
      expectation: { record_count: number; primary_keys: string[][] } | undefined,
      reason: string,
    ) => Promise<unknown>
    onSuggestSpec?: (jobId: string) => Promise<unknown>
    onDispatch?: (jobId: string) => Promise<unknown>
  }
> = ({
  open,
  onClose,
  isMobile,
  loading,
  job,
  configuring = false,
  onSaveSpec,
  onSaveSampleRole,
  onSuggestSpec,
  onDispatch,
}) => {
  const [specText, setSpecText] = useState('{}')
  const [specReason, setSpecReason] = useState('')
  const [formError, setFormError] = useState('')
  const [editingSampleId, setEditingSampleId] = useState<string | null>(null)
  const [sampleRole, setSampleRole] = useState<'unassigned' | 'generation' | 'acceptance' | 'negative'>('unassigned')
  const [sampleRecordCount, setSampleRecordCount] = useState(0)
  const [sampleKeysText, setSampleKeysText] = useState('[]')
  const [sampleReason, setSampleReason] = useState('')
  const [previewSampleId, setPreviewSampleId] = useState<string | null>(null)

  useEffect(() => {
    setSpecText(JSON.stringify(job?.parser_spec_json || {}, null, 2))
    setSpecReason('')
    setFormError('')
    setEditingSampleId(null)
    setPreviewSampleId(null)
  }, [job?.id, job?.parser_spec_json])

  const locked = Boolean(job && ['pending', 'running', 'validating', 'succeeded', 'superseded'].includes(job.status))
  const readiness = job?.generation_readiness
  const suggestionState = (job?.agent_state_json?.parser_spec_suggestion || {}) as {
    status?: 'queued' | 'running' | 'ready' | 'failed'
    error?: string
    result?: {
      classification?: string
      confidence?: number
      rationale?: string
      model_called?: boolean
      sample_suggestions?: Array<{
        sample_id: string
        recommended_role: 'generation' | 'negative' | 'unassigned'
        classification?: string
        expectation?: { record_count: number; primary_keys: string[][] }
      }>
    }
  }
  const sampleSuggestionById = new Map(
    (suggestionState.result?.sample_suggestions || []).map((item) => [item.sample_id, item]),
  )
  const previewSample = (job?.samples || []).find((sample) => sample.id === previewSampleId)
  const modelCallCount = job?.model_calls_used ?? (job?.generation_attempts || [])
    .filter((attempt) => (
      attempt.action === 'llm_generate'
      || attempt.action === 'llm_repair'
      || attempt.action.startsWith('llm_tool_round_')
    ))
    .length
  const blockerLabels: Record<string, string> = {
    legacy_pipeline: '尚未切换 Simple Parser v2',
    parser_spec_missing: 'ParserSpec 尚未生成',
    parser_spec_not_confirmed: 'ParserSpec 尚未确认',
    parser_spec_digest_mismatch: 'ParserSpec 摘要不一致',
    samples_missing: '缺少样本',
    samples_not_sanitized: '存在未脱敏样本',
    sample_roles_unassigned: '存在未分配角色的样本',
    generation_sample_missing: '缺少生成样本',
  }
  const generationOnly = readiness?.validation_mode === 'generation_sample_only'
  const suggestingSpec = ['queued', 'running'].includes(suggestionState.status || '')

  const saveSpec = async (confirmed: boolean) => {
    if (!job || !onSaveSpec) return
    setFormError('')
    if (!specReason.trim()) {
      setFormError('请填写 ParserSpec 变更原因')
      return
    }
    try {
      const parserSpec = JSON.parse(specText) as Record<string, unknown>
      await onSaveSpec(job.id, parserSpec, confirmed, specReason.trim())
      setSpecReason('')
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'ParserSpec JSON 无效')
    }
  }

  const openSampleEditor = (sample: NonNullable<ParserJobItem['samples']>[number]) => {
    const suggested = sampleSuggestionById.get(sample.id)
    setPreviewSampleId(null)
    setEditingSampleId(sample.id)
    setSampleRole(
      sample.sample_role && sample.sample_role !== 'unassigned'
        ? sample.sample_role
        : suggested?.recommended_role || sample.content_classification?.recommended_role || 'unassigned',
    )
    setSampleRecordCount(
      sample.expectation_json?.record_count ?? suggested?.expectation?.record_count ?? 0,
    )
    setSampleKeysText(JSON.stringify(
      sample.expectation_json?.primary_keys || suggested?.expectation?.primary_keys || [],
      null,
      2,
    ))
    setSampleReason('')
    setFormError('')
  }

  const openSamplePreview = (sampleId: string) => {
    setEditingSampleId(null)
    setPreviewSampleId(sampleId)
    setFormError('')
  }

  const closeJobDrawer = () => {
    setEditingSampleId(null)
    setPreviewSampleId(null)
    onClose()
  }

  const saveSample = async () => {
    if (!job || !editingSampleId || !onSaveSampleRole) return
    setFormError('')
    if (!sampleReason.trim()) {
      setFormError('请填写样本角色变更原因')
      return
    }
    try {
      const primaryKeys = JSON.parse(sampleKeysText) as string[][]
      if (!Array.isArray(primaryKeys)) throw new Error('预期主键必须是 JSON 数组')
      const expectation = ['generation', 'acceptance'].includes(sampleRole)
        ? { record_count: sampleRecordCount, primary_keys: primaryKeys }
        : undefined
      await onSaveSampleRole(
        job.id,
        editingSampleId,
        sampleRole,
        expectation,
        sampleReason.trim(),
      )
      setEditingSampleId(null)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '样本配置无效')
    }
  }

  const overview = job ? (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="任务类型">{job.job_type}</Descriptions.Item>
        <Descriptions.Item label="状态"><Tag color={statusColor(job.status)}>{job.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="流水线">
          <Tag color={job.pipeline_version === 'simple_v2' ? 'blue' : 'default'}>
            {job.pipeline_version || 'legacy'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Spec 状态">
          <Tag color={job.parser_spec_status === 'confirmed' ? 'green' : 'orange'}>
            {job.parser_spec_status || 'missing'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="厂商">{job.vendor}</Descriptions.Item>
        <Descriptions.Item label="兼容域">{job.profile}</Descriptions.Item>
        <Descriptions.Item label="作用域" span={2}>{job.compatibility_scope === 'family' ? '设备族' : '精确型号'}</Descriptions.Item>
        <Descriptions.Item label="命令" span={2}>{job.canonical_command}</Descriptions.Item>
        <Descriptions.Item label="样本数">{job.sample_count}</Descriptions.Item>
        <Descriptions.Item label="结构变体">{job.structure_variant_count ?? 0}</Descriptions.Item>
        <Descriptions.Item label="生成样本">{readiness?.role_counts.generation || 0}</Descriptions.Item>
        <Descriptions.Item label="验收样本">{readiness?.role_counts.acceptance || 0}</Descriptions.Item>
        <Descriptions.Item label="任务执行次数">{job.attempts}</Descriptions.Item>
        <Descriptions.Item label="模板尝试">{job.attempt_summary?.template_attempts || 0}</Descriptions.Item>
        {job.pipeline_version === 'simple_v2' ? (
          <Descriptions.Item label="LLM 调用" span={2}>{modelCallCount} / 3</Descriptions.Item>
        ) : null}
        {job.pipeline_version !== 'simple_v2' ? (
          <>
            <Descriptions.Item label="Token 使用">
              {String(job.agent_state_json?.token_usage_total ?? 0)} / {String(job.agent_budget_json?.token_budget ?? '-')}
            </Descriptions.Item>
            <Descriptions.Item label="时间预算">
              {job.agent_budget_json?.time_budget_seconds
                ? `${String(job.agent_budget_json.time_budget_seconds)} 秒`
                : '-'}
            </Descriptions.Item>
          </>
        ) : null}
        <Descriptions.Item label="Spec 摘要" span={2}>
          <Typography.Text copyable={Boolean(job.parser_spec_sha256)}>
            {shortDigest(job.parser_spec_sha256 || '') || '-'}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Celery Task" span={2}>{job.celery_task_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="失败代码">{job.failure_code || '-'}</Descriptions.Item>
        <Descriptions.Item label="错误">{job.last_error || '-'}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{job.created_at || '-'}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{job.completed_at || '-'}</Descriptions.Item>
      </Descriptions>
      <Alert
        type={readiness?.ready && !generationOnly ? 'success' : 'warning'}
        showIcon
        message={readiness?.ready
          ? generationOnly
            ? '单样本候选生成已就绪'
            : '候选生成已就绪'
          : '候选生成尚未就绪'}
        description={readiness?.ready && generationOnly
          ? '允许生成候选并进入人工审批；当前没有独立验收样本，审批时必须核对原始输出、TextFSM 模板和解析结果。'
          : (readiness?.blockers || ['legacy_pipeline'])
            .map((blocker) => blockerLabels[blocker] || blocker)
            .join('；')}
      />
      {job.status === 'failed' && (job.last_error || job.agent_state_json?.stop_reason) ? (
        <Alert
          type="warning"
          showIcon
          message={job.pipeline_version === 'simple_v2'
            ? 'Parser 生成失败，候选未进入审批'
            : '历史 Parser Agent 任务已终止'}
          description={String(job.agent_state_json?.stop_reason || job.last_error || '-')}
        />
      ) : null}
    </Space>
  ) : null

  const specPanel = job ? (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
        <div>
          <Typography.Text strong>字段与记录契约</Typography.Text>
          <br />
          <Typography.Text type="secondary">
            平台根据脱敏样本生成草稿，工程师核对字段、主键和样本期望后确认。
          </Typography.Text>
        </div>
        {onSuggestSpec ? (
          <Button
            icon={<RobotOutlined />}
            disabled={locked || suggestingSpec}
            loading={suggestingSpec}
            onClick={() => onSuggestSpec(job.id)}
          >
            {job.parser_spec_status === 'missing' ? '生成 ParserSpec 草稿' : '重新生成草稿'}
          </Button>
        ) : null}
      </Space>
      {suggestionState.status === 'failed' ? (
        <Alert
          type="error"
          showIcon
          message="ParserSpec 草稿生成失败"
          description={suggestionState.error || '请检查模型配置或样本内容后重试。'}
        />
      ) : suggestionState.status === 'ready' ? (
        <Alert
          type={suggestionState.result?.classification === 'no_positive_data' ? 'warning' : 'info'}
          showIcon
          message={suggestionState.result?.classification === 'no_positive_data'
            ? '当前样本没有可结构化的正向数据'
            : 'ParserSpec 草稿已生成，等待工程师确认'}
          description={suggestionState.result?.classification === 'no_positive_data'
            ? '平台识别到的内容是合法空输出或命令错误。请采纳负样本建议，并补充一份包含真实记录的输出。'
            : suggestionState.result?.rationale || '请核对下方字段、主键和样本期望，不会自动进入生产。'}
        />
      ) : null}
      <Input.TextArea
        aria-label="ParserSpec JSON"
        value={specText}
        onChange={(event) => setSpecText(event.target.value)}
        autoSize={{ minRows: 14, maxRows: 28 }}
        disabled={locked || suggestingSpec}
        style={{ fontFamily: 'Consolas, Monaco, monospace' }}
      />
      <Input.TextArea
        aria-label="ParserSpec 变更原因"
        value={specReason}
        onChange={(event) => setSpecReason(event.target.value)}
        autoSize={{ minRows: 2, maxRows: 4 }}
        disabled={locked || suggestingSpec}
        placeholder="变更原因"
      />
      <Space wrap>
        <Button disabled={locked || suggestingSpec} loading={configuring} onClick={() => saveSpec(false)}>
          保存草稿
        </Button>
        <Button type="primary" disabled={locked || suggestingSpec} loading={configuring} onClick={() => saveSpec(true)}>
          确认 ParserSpec
        </Button>
      </Space>
    </Space>
  ) : null

  const samplesPanel = job ? (
    <StandardTableShell
      rowKey="id"
      pagination={false}
      dataSource={job.samples || []}
      emptyText="暂无脱敏样本"
      scroll={{ x: 1120 }}
      columns={[
        {
          title: '样本设备',
          width: 190,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>
                {sample.source_context?.device_name || sample.device_id || '未知设备'}
              </Typography.Text>
              <Typography.Text type="secondary">
                {[sample.source_context?.management_ip, sample.source_context?.model].filter(Boolean).join(' · ') || '-'}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: '采集来源',
          width: 210,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => (
            <Space direction="vertical" size={0}>
              <Typography.Text>{sample.source_context?.source_file_name || '离线导入文件'}</Typography.Text>
              <Typography.Text type="secondary">
                {sample.source_context?.observed_at || sample.created_at}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: '输出判定',
          width: 130,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => {
            const status = sample.content_classification?.status || 'empty_unverified'
            const labels: Record<string, string> = {
              data: '包含数据',
              valid_empty: '合法空输出',
              unsupported_command: '命令不支持',
              command_error: '执行错误',
              empty_unverified: '待确认空白',
            }
            const colors: Record<string, string> = {
              data: 'blue', valid_empty: 'default', unsupported_command: 'orange',
              command_error: 'red', empty_unverified: 'gold',
            }
            return <Tag color={colors[status]}>{labels[status] || status}</Tag>
          },
        },
        {
          title: '脱敏内容',
          width: 260,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => (
            <Space size={6} style={{ maxWidth: '100%' }}>
              <Typography.Text ellipsis style={{ maxWidth: 205 }}>
                {sample.sanitized_preview || '无可展示内容'}
              </Typography.Text>
              <Button
                type="text"
                size="small"
                icon={<EyeOutlined />}
                aria-label="查看样本内容"
                onClick={() => openSamplePreview(sample.id)}
              />
            </Space>
          ),
        },
        { title: '样本角色', dataIndex: 'sample_role', width: 110,
          render: (value: string) => <Tag>{value || 'unassigned'}</Tag> },
        { title: '预期记录', width: 90,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => sample.expectation_json?.record_count ?? sampleSuggestionById.get(sample.id)?.expectation?.record_count ?? '-' },
        { title: '操作', width: 90,
          render: (_: unknown, sample: NonNullable<ParserJobItem['samples']>[number]) => (
            <Button size="small" disabled={locked} onClick={() => openSampleEditor(sample)}>确认</Button>
          ) },
      ]}
    />
  ) : null

  const attemptsPanel = job ? (
    (job.generation_attempts || []).length
      ? <Timeline items={(job.generation_attempts || []).map(attemptTimelineItem)} />
      : <Typography.Text type="secondary">任务尚未产生生成 Attempt。</Typography.Text>
  ) : null

  return (
    <Drawer
        title="Parser 生成任务"
        open={open}
        onClose={closeJobDrawer}
        width={isMobile ? '100vw' : 920}
        destroyOnClose
        extra={job && onDispatch ? (
          <Button
            type="primary"
            disabled={!readiness?.ready || !['collecting', 'pending'].includes(job.status)}
            loading={configuring}
            onClick={() => onDispatch(job.id)}
          >
            调度生成
          </Button>
        ) : null}
      >
        {loading ? loadingPanel : job ? (
          <>
            {formError ? <Alert type="error" showIcon message={formError} style={{ marginBottom: 12 }} /> : null}
            <Tabs items={[
              { key: 'overview', label: '概览', children: overview },
              { key: 'spec', label: 'ParserSpec', children: specPanel },
              { key: 'samples', label: `样本 (${job.samples?.length || 0})`, children: samplesPanel },
              { key: 'attempts', label: `生成记录 (${job.generation_attempts?.length || 0})`, children: attemptsPanel },
            ]} />
          </>
        ) : null}
      <Drawer
          title="配置样本角色"
          open={Boolean(editingSampleId)}
          onClose={() => setEditingSampleId(null)}
          width={isMobile ? '100vw' : 480}
          destroyOnClose
          extra={(
            <Space>
              <Button onClick={() => setEditingSampleId(null)}>取消</Button>
              <Button type="primary" loading={configuring} onClick={saveSample}>保存</Button>
            </Space>
          )}
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Select
              aria-label="样本角色"
              value={sampleRole}
              onChange={setSampleRole}
              style={{ width: '100%' }}
              options={[
                { value: 'generation', label: 'generation' },
                { value: 'acceptance', label: 'acceptance' },
                { value: 'negative', label: 'negative' },
                { value: 'unassigned', label: 'unassigned' },
              ]}
            />
            {['generation', 'acceptance'].includes(sampleRole) ? (
              <>
                <InputNumber
                  min={0}
                  precision={0}
                  value={sampleRecordCount}
                  onChange={(value) => setSampleRecordCount(Number(value || 0))}
                  style={{ width: '100%' }}
                  addonBefore="预期记录数"
                />
                <Input.TextArea
                  aria-label="预期主键 JSON"
                  value={sampleKeysText}
                  onChange={(event) => setSampleKeysText(event.target.value)}
                  autoSize={{ minRows: 5, maxRows: 12 }}
                  style={{ fontFamily: 'Consolas, Monaco, monospace' }}
                  placeholder='预期主键 JSON，例如 [["GE1/0/1"]]'
                />
              </>
            ) : null}
            <Input.TextArea
              aria-label="样本角色变更原因"
              value={sampleReason}
              onChange={(event) => setSampleReason(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 4 }}
              placeholder="变更原因"
            />
          </Space>
        </Drawer>
        <Drawer
          title="样本证据"
          open={Boolean(previewSample)}
          onClose={() => setPreviewSampleId(null)}
          width={isMobile ? '100vw' : 680}
          destroyOnClose
        >
          {previewSample ? (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Descriptions size="small" column={isMobile ? 1 : 2} bordered>
                <Descriptions.Item label="设备">
                  {previewSample.source_context?.device_name || previewSample.device_id}
                </Descriptions.Item>
                <Descriptions.Item label="管理地址">
                  {previewSample.source_context?.management_ip || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="厂商 / 型号">
                  {[previewSample.source_context?.vendor, previewSample.source_context?.model].filter(Boolean).join(' / ') || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="采集时间">
                  {previewSample.source_context?.observed_at || previewSample.created_at}
                </Descriptions.Item>
                <Descriptions.Item label="源文件" span={isMobile ? 1 : 2}>
                  {previewSample.source_context?.source_file_name || '离线导入文件'}
                </Descriptions.Item>
                <Descriptions.Item label="命令" span={isMobile ? 1 : 2}>
                  {previewSample.source_context?.command || job?.canonical_command || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="数据集 ID" span={isMobile ? 1 : 2}>
                  <Typography.Text copyable>{previewSample.dataset_id || '-'}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="Snapshot ID" span={isMobile ? 1 : 2}>
                  <Typography.Text copyable>{previewSample.snapshot_id}</Typography.Text>
                </Descriptions.Item>
              </Descriptions>
              <div>
                <Typography.Title level={5}>脱敏命令输出</Typography.Title>
                <Typography.Paragraph copyable style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                  {previewSample.sanitized_text || '无可展示内容'}
                </Typography.Paragraph>
              </div>
            </Space>
          ) : null}
        </Drawer>
    </Drawer>
  )
}
