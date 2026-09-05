import React, { useMemo, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  CheckCircleOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'

import {
  parserApi,
  type OfflineCommandAssetUnitDetail,
} from '../../../services/api'

type Props = {
  unit: OfflineCommandAssetUnitDetail
  onChanged?: () => void
  onOpenEvidence?: () => void
}

const valueText = (value: unknown) => {
  if (value == null || value === '') return '-'
  if (Array.isArray(value)) return value.map(String).join(', ') || '-'
  return String(value)
}

const ContractSummary: React.FC<{ value: Record<string, unknown> | null }> = ({ value }) => {
  if (!value) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Contract 候选尚未生成" />
  const recordSets = Array.isArray(value.record_sets) ? value.record_sets : []
  return (
    <Descriptions bordered size="small" column={1}>
      <Descriptions.Item label="实体类型">{valueText(value.entity_type)}</Descriptions.Item>
      <Descriptions.Item label="主键">
        {valueText(value.primary_keys || value.entity_identity_fields)}
      </Descriptions.Item>
      <Descriptions.Item label="必需字段">{valueText(value.required_fields)}</Descriptions.Item>
      <Descriptions.Item label="可选字段">{valueText(value.optional_fields)}</Descriptions.Item>
      <Descriptions.Item label="记录集">
        {recordSets.length ? recordSets.map((item) => {
          const record = item as Record<string, unknown>
          return valueText(record.name || record.entity_type)
        }).join(', ') : '-'}
      </Descriptions.Item>
    </Descriptions>
  )
}

const PolicySummary: React.FC<{ values: Array<Record<string, unknown>> }> = ({ values }) => {
  if (!values.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Policy 候选尚未生成" />
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {values.map((value, index) => (
        <Descriptions key={`${valueText(value.observation_type)}-${index}`} bordered size="small" column={1}>
          <Descriptions.Item label="观测类型">{valueText(value.observation_type)}</Descriptions.Item>
          <Descriptions.Item label="语义类型">{valueText(value.semantic_type)}</Descriptions.Item>
          <Descriptions.Item label="比较模式">{valueText(value.compare_mode)}</Descriptions.Item>
          <Descriptions.Item label="策略来源">{valueText(value.source)}</Descriptions.Item>
        </Descriptions>
      ))}
    </Space>
  )
}

const EventSummary: React.FC<{ values: Array<Record<string, unknown>> }> = ({ values }) => {
  if (!values.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Event 候选尚未生成" />
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {values.map((value, index) => {
        const condition = value.condition as Record<string, unknown> | undefined
        const recovery = value.recovery as Record<string, unknown> | undefined
        return (
          <Descriptions key={`${valueText(value.name)}-${index}`} bordered size="small" column={1}>
            <Descriptions.Item label="规则名称">{valueText(value.name)}</Descriptions.Item>
            <Descriptions.Item label="事件类型">{valueText(value.event_type)}</Descriptions.Item>
            <Descriptions.Item label="级别">{valueText(value.severity)}</Descriptions.Item>
            <Descriptions.Item label="条件类型">{valueText(condition?.kind)}</Descriptions.Item>
            <Descriptions.Item label="恢复关系">{valueText(recovery?.resolves)}</Descriptions.Item>
          </Descriptions>
        )
      })}
    </Space>
  )
}

const RuleAssetProductionStep: React.FC<Props> = ({ unit, onChanged, onOpenEvidence }) => {
  const { message } = App.useApp()
  const [localContextId, setLocalContextId] = useState<string>()
  const [generationStarted, setGenerationStarted] = useState(false)
  const [reason, setReason] = useState('已核对证据、记录粒度、主键和字段语义')
  const seedJobId = unit.rule_seed_job_id || undefined
  const contextId = localContextId || unit.context_id || undefined
  const datasetIds = useMemo(() => Array.from(new Set(
    unit.evidence
      .map((item) => item.period?.dataset_id)
      .filter((item): item is string => Boolean(item)),
  )), [unit.evidence])

  const semanticQuery = useQuery(
    ['rule-asset-semantic-plan', seedJobId, contextId],
    () => parserApi.previewRuleGenerationContext(seedJobId || '', { context_id: contextId }),
    { enabled: Boolean(seedJobId && contextId), retry: false },
  )
  const bundleListQuery = useQuery(
    ['rule-asset-production-bundles', unit.id, contextId],
    () => parserApi.ruleAssetBundles({ search: unit.canonical_command, limit: 30 }),
    {
      enabled: Boolean(seedJobId && contextId),
      refetchInterval: generationStarted && !unit.bundle_id ? 5000 : false,
    },
  )
  const discoveredBundleId = unit.bundle_id || bundleListQuery.data?.items.find((item) => (
    item.context_id === contextId
    || (item.command === unit.canonical_command && item.offline_job_id === unit.offline_job_id)
  ))?.id
  const bundleQuery = useQuery(
    ['rule-asset-production-bundle', discoveredBundleId],
    () => parserApi.ruleAssetBundle(discoveredBundleId || ''),
    {
      enabled: Boolean(discoveredBundleId),
      refetchInterval: (data) => data && ['active', 'rejected', 'deprecated'].includes(data.status)
        ? false
        : 5000,
    },
  )
  const bundle = bundleQuery.data
  const engineeringDraftQuery = useQuery(
    ['rule-asset-engineering-draft', discoveredBundleId],
    () => parserApi.ruleAssetEngineeringDraft(discoveredBundleId || ''),
    { enabled: Boolean(discoveredBundleId) },
  )
  const engineeringDraft = engineeringDraftQuery.data
  const semanticContext = semanticQuery.data?.context || {}
  const requirements = (
    semanticContext.rule_asset_requirements as Record<string, unknown> | undefined
  ) || {}
  const recordGroups = Array.isArray(semanticContext.record_groups)
    ? semanticContext.record_groups as Array<Record<string, unknown>>
    : []
  const identityConstraint = (
    semanticContext.canonical_identity_constraint as Record<string, unknown> | undefined
  ) || {}
  const structuredRecords = recordGroups.flatMap((item) => (
    Array.isArray(item.records) ? item.records as Array<Record<string, unknown>> : []
  ))
  const recordSetNames = Array.from(new Set(
    structuredRecords.map((item) => String(item.__record_set__ || 'default')),
  ))
  const structuredFields = Array.from(new Set(
    structuredRecords.flatMap((item) => Object.keys(item).filter((key) => key !== '__record_set__')),
  ))
  const observationTargets = Array.isArray(semanticContext.observation_targets)
    ? semanticContext.observation_targets as Array<Record<string, unknown>>
    : []
  const observationTypes = Array.from(new Set(
    observationTargets
      .map((item) => String(item.observation_type || ''))
      .filter(Boolean),
  ))
  const requiresContract = requirements.requires_observation_contract !== false
  const requiresPolicy = requirements.requires_change_policy === true
  const requiresEvent = requirements.requires_event_rule_assets === true

  const buildContext = useMutation(
    () => parserApi.autoBuildRuleGenerationContext(seedJobId || '', datasetIds),
    {
      onSuccess: (result: Record<string, unknown>) => {
        setLocalContextId(String(result.context_id || ''))
        message.success('语义方案已从不可变样本上下文生成')
      },
      onError: (error: Error) => message.error(error.message),
    },
  )
  const startGeneration = useMutation(
    () => parserApi.startRuleAssetGeneration(seedJobId || '', {
      context_id: contextId,
    }),
    {
      onSuccess: () => {
        setGenerationStarted(true)
        message.success('Contract → Policy → Event 生产流程已启动')
        bundleListQuery.refetch()
        onChanged?.()
      },
      onError: (error: Error) => message.error(error.message),
    },
  )
  const reviewBundle = useMutation(
    () => parserApi.reviewRuleAssetBundle(discoveredBundleId || '', {
      decision: 'accepted',
      reason,
    }),
    {
      onSuccess: () => {
        message.success('工程复核已通过')
        bundleQuery.refetch()
        onChanged?.()
      },
      onError: (error: Error) => message.error(error.message),
    },
  )
  const publishBundle = useMutation(
    () => parserApi.approveRuleGenerationJob(discoveredBundleId || '', { reason }),
    {
      onSuccess: () => {
        message.success('规则资产包已原子发布')
        bundleQuery.refetch()
        onChanged?.()
      },
      onError: (error: Error) => message.error(error.message),
    },
  )

  const semanticReviewed = generationStarted || Boolean(bundle)
  const reviewAccepted = bundle?.engineering_review?.decision === 'accepted'
  const validationSteps = bundle?.steps?.filter((step) => ['synthetic', 'replay'].includes(step.key)) || []
  const validationPassed = validationSteps.length > 0
    && validationSteps.every((step) => ['passed', 'skipped'].includes(step.status))
  if (!seedJobId) {
    return <Alert type="info" showIcon message="等待 Parser 发布" description="Parser 发布后会自动创建规则资产生产种子。" />
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div>
        <Typography.Title level={5} style={{ margin: 0 }}>规则资产生产</Typography.Title>
        <Typography.Text type="secondary">先核对证据和语义，再生成、验证并整包发布。</Typography.Text>
      </div>

      <Card size="small" title="1. CLI 证据">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text>
            当前命令包含 {unit.evidence_pagination.total} 份 CLI 输出，已形成 {unit.sample_count} 份可用样本。
          </Typography.Text>
          <Typography.Text type="secondary">
            生成上下文只使用已验证结构化记录，原始 CLI 仅供人工核对，不发送给 LLM。
          </Typography.Text>
          {onOpenEvidence && <Button onClick={onOpenEvidence}>查看 CLI 证据</Button>}
        </Space>
      </Card>

      <Card
        size="small"
        title="2. 结构化语义方案"
        extra={semanticReviewed ? <Tag color="success">已确认</Tag> : <Tag>待确认</Tag>}
      >
        {!contextId ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              从当前命令的不可变样本生成记录集、主键、字段语义和资产范围，不写死厂家或命令字段。
            </Typography.Text>
            <Button
              type="primary"
              loading={buildContext.isLoading}
              disabled={!datasetIds.length}
              onClick={() => buildContext.mutate()}
            >生成语义方案</Button>
            {!datasetIds.length && <Alert type="warning" showIcon message="当前证据缺少数据集标识" />}
          </Space>
        ) : semanticQuery.isLoading ? (
          <Typography.Text type="secondary">正在加载语义方案...</Typography.Text>
        ) : semanticQuery.data ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="观测范围">{valueText(observationTypes)}</Descriptions.Item>
              <Descriptions.Item label="记录类型">
                {recordSetNames.map((name) => `${name} (${structuredRecords.filter((item) => String(item.__record_set__ || 'default') === name).length})`).join(', ') || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="主键">
                {valueText(identityConstraint.identity_dimensions)}
              </Descriptions.Item>
              <Descriptions.Item label="结构化字段">{valueText(structuredFields)}</Descriptions.Item>
              <Descriptions.Item label="生成顺序">
                {[
                  requirements.requires_observation_contract !== false ? 'Observation Contract' : null,
                  requirements.requires_change_policy === true ? 'Change Policy' : null,
                  requirements.requires_event_rule_assets === true ? 'Event Rule' : null,
                ].filter(Boolean).join(' → ') || '-'}
              </Descriptions.Item>
            </Descriptions>
            {!semanticQuery.data.ready && (
              <Alert type="warning" showIcon message="语义上下文尚不完整" />
            )}
          </Space>
        ) : <Alert type="error" showIcon message="语义方案加载失败" />}
      </Card>

      {contextId && semanticQuery.data?.ready && !bundle && (
        <Card size="small" title="3. 确认并生成 Contract → Policy → Event">
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              生成器只接收已确认的语义方案；后续资产必须通过依赖关系、组合完整性和历史回放校验。
            </Typography.Text>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={startGeneration.isLoading}
              disabled={generationStarted}
              onClick={() => startGeneration.mutate()}
            >{generationStarted ? '生成任务已提交' : '确认语义方案并生成规则资产'}</Button>
          </Space>
        </Card>
      )}

      {bundle && (
        <>
          <Card size="small" title="3. Observation Contract">
            {requiresContract
              ? <ContractSummary value={engineeringDraft?.contract || null} />
              : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前语义方案不需要 Contract" />}
          </Card>
          <Card size="small" title="4. Change Policy">
            {requiresPolicy
              ? <PolicySummary values={engineeringDraft?.change_policies || []} />
              : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前语义方案不需要 Policy" />}
          </Card>
          <Card size="small" title="5. Event Rule">
            {requiresEvent
              ? <EventSummary values={engineeringDraft?.event_rules || []} />
              : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前语义方案不需要 Event" />}
          </Card>
          <Card size="small" title="6. 验证、复核与发布" extra={<Tag>{bundle.status}</Tag>}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {bundle.blockers.map((blocker) => <Alert key={blocker} type="error" showIcon message={blocker} />)}
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="确定性验证">{validationPassed ? '通过' : '待通过'}</Descriptions.Item>
                <Descriptions.Item label="工程复核">{reviewAccepted ? '已接受' : '待复核'}</Descriptions.Item>
                <Descriptions.Item label="发布状态">
                  {bundle.status === 'active' ? '已发布' : bundle.release_ready ? '可以发布' : '暂不可发布'}
                </Descriptions.Item>
              </Descriptions>
              {bundle.status !== 'active' && (
                <Input.TextArea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  placeholder="填写复核或发布说明"
                />
              )}
              {!reviewAccepted && bundle.status === 'pending_approval' && (
                <Button
                  icon={<SafetyCertificateOutlined />}
                  loading={reviewBundle.isLoading}
                  disabled={!validationPassed || !reason.trim()}
                  onClick={() => reviewBundle.mutate()}
                >通过工程复核</Button>
              )}
              {reviewAccepted && bundle.status !== 'active' && (
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={publishBundle.isLoading}
                  disabled={!bundle.release_ready || !reason.trim()}
                  onClick={() => publishBundle.mutate()}
                >原子发布规则资产包</Button>
              )}
              {bundle.status === 'active' && (
                <Alert type="success" showIcon message="整包已发布" description="Contract、Policy、Event 已同批生效。" />
              )}
            </Space>
          </Card>
        </>
      )}
    </Space>
  )
}

export default RuleAssetProductionStep
