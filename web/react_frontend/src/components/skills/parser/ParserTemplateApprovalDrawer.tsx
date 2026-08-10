import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Descriptions, Drawer, Input, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd'
import { CheckOutlined, CloseCircleOutlined } from '@ant-design/icons'

import type {
  ParserComparisonParseResult,
  ParserComparisonRecordSet,
  ParserTemplateComparison,
} from '../../../services/api'
import { codePanelStyle, statusColor } from './parserUi'

interface ParserTemplateApprovalDrawerProps {
  open: boolean
  onClose: () => void
  isMobile: boolean
  loading: boolean
  comparison?: ParserTemplateComparison | null
  reviewing?: boolean
  approving?: boolean
  rejecting?: boolean
  onReview: (reason: string) => void
  onApprove: (reason: string) => void
  onReject?: () => void
}

interface AiReview {
  passed?: boolean
  summary?: string
  diagnostics?: Array<{
    code?: string
    sample_id?: string
    target?: string
    message?: string
  }>
  primary_key_assessment?: {
    passed?: boolean
    reason?: string
  }
  completeness_assessment?: {
    passed?: boolean
    reason?: string
  }
  model_name?: string
  latency_ms?: number
}

interface CandidateProvenance {
  provider?: string
  package_version?: string
  source_template?: string
  source_template_text?: string
  source_content_sha256?: string
  content_sha256?: string
  license?: string
  platform?: string
  platform_mapping_version?: number
  transformation_version?: string | null
  transformations?: Array<{
    kind?: string
    line_number?: number
    source?: string
    derived?: string
  }>
}

const recordCount = (value: unknown) => (Array.isArray(value) ? value.length : 0)

const resultRecordSets = (
  result: ParserComparisonParseResult | undefined,
  fallbackName: string,
  primaryKeys: string[],
): ParserComparisonRecordSet[] => {
  if (result?.record_sets?.length) return result.record_sets
  return [{
    name: fallbackName || 'records',
    primary_keys: primaryKeys,
    records: Array.isArray(result?.records)
      ? result.records.filter((item): item is Record<string, unknown> => (
          typeof item === 'object' && item !== null && !Array.isArray(item)
        ))
      : [],
  }]
}

const parseBundleTemplates = (content: string) => {
  try {
    const payload = JSON.parse(content) as {
      format?: string
      record_sets?: Array<{ name?: string; template_text?: string }>
    }
    if (payload.format !== 'netops.parser_bundle/v1' || !Array.isArray(payload.record_sets)) {
      return []
    }
    return payload.record_sets
      .filter((item) => item.name && item.template_text)
      .map((item) => ({ name: String(item.name), template_text: String(item.template_text) }))
  } catch {
    return []
  }
}

const JsonEvidence: React.FC<{ value: unknown; emptyText?: string }> = ({ value, emptyText = '暂无数据' }) => (
  <pre className="parser-approval-evidence__code">{value == null ? emptyText : JSON.stringify(value, null, 2)}</pre>
)

const ParserTemplateApprovalDrawer: React.FC<ParserTemplateApprovalDrawerProps> = ({
  open,
  onClose,
  isMobile,
  loading,
  comparison,
  reviewing = false,
  approving = false,
  rejecting = false,
  onReview,
  onApprove,
  onReject,
}) => {
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>()
  const [selectedRecordSetName, setSelectedRecordSetName] = useState<string>()
  const [resultConfirmed, setResultConfirmed] = useState(false)
  const [primaryKeyConfirmed, setPrimaryKeyConfirmed] = useState(false)
  const [directReviewConfirmed, setDirectReviewConfirmed] = useState(false)
  const [reviewReason, setReviewReason] = useState('')
  const [approvalReason, setApprovalReason] = useState('')

  useEffect(() => {
    if (!open) return
    setSelectedSnapshotId(comparison?.rows[0]?.snapshot_id)
    setSelectedRecordSetName(undefined)
    setResultConfirmed(false)
    setPrimaryKeyConfirmed(false)
    setDirectReviewConfirmed(false)
    setReviewReason('')
    setApprovalReason('')
  }, [comparison, open])

  const selectedSample = useMemo(
    () => comparison?.rows.find((row) => row.snapshot_id === selectedSnapshotId) || comparison?.rows[0],
    [comparison, selectedSnapshotId],
  )
  const legacyReadiness = comparison?.approval_readiness || {
    can_approve: false,
    blockers: ['审批证据尚未加载'],
  }
  const reviewReadiness = comparison?.review_readiness || {
    can_review: legacyReadiness.can_approve,
    blockers: legacyReadiness.blockers,
  }
  const activationReadiness = comparison?.activation_readiness || {
    can_activate: legacyReadiness.can_approve,
    blockers: legacyReadiness.blockers,
  }
  const semanticReview = comparison?.semantic_review
  const releaseActor = comparison?.release_actor
  const candidateContract = comparison?.candidate_contract || {}
  const simpleParser = candidateContract.simple_parser as
    | {
        hard_gates?: Record<string, boolean>
        validation_mode?: string
      }
    | undefined
  const parserAgent = candidateContract.parser_agent as
    | {
        hard_gates?: Record<string, boolean>
      }
    | undefined
  const hardGates = simpleParser?.hard_gates || parserAgent?.hard_gates || {}
  const generationOnly = simpleParser?.validation_mode === 'generation_sample_only'
  const primaryKeys = (candidateContract.primary_keys || []) as string[]
  const recordSetContracts = (
    Array.isArray(candidateContract.record_sets)
      ? candidateContract.record_sets
      : []
  ) as Array<{
    name?: string
    entity_type?: string
    primary_keys?: string[]
    required_fields?: string[]
  }>
  const proposedRecordSets = useMemo(
    () => resultRecordSets(
      selectedSample?.proposed,
      comparison?.candidate_template.category || 'records',
      primaryKeys,
    ),
    [comparison?.candidate_template.category, primaryKeys, selectedSample?.proposed],
  )
  const selectedRecordSet = proposedRecordSets.find(
    (item) => item.name === selectedRecordSetName,
  ) || proposedRecordSets[0]
  const currentRecordSets = useMemo(
    () => resultRecordSets(
      selectedSample?.current,
      String(
        comparison?.active_contract?.entity_type
        || comparison?.candidate_template.category
        || 'records',
      ),
      (comparison?.active_contract?.primary_keys || []) as string[],
    ),
    [comparison?.active_contract, comparison?.candidate_template.category, selectedSample?.current],
  )
  const currentRecordSet = currentRecordSets.find(
    (item) => item.name === selectedRecordSet?.name,
  )
  const recordTableColumns = useMemo(() => {
    const keys = Array.from(new Set(
      (selectedRecordSet?.records || []).flatMap((record) => Object.keys(record)),
    ))
    return keys.map((key) => ({
      title: key,
      dataIndex: key,
      key,
      ellipsis: true,
      render: (value: unknown) => (
        typeof value === 'object' && value !== null
          ? JSON.stringify(value)
          : String(value ?? '')
      ),
    }))
  }, [selectedRecordSet])
  const recordTableData = useMemo(
    () => (selectedRecordSet?.records || []).map((record, index) => ({
      ...record,
      __approval_row_key: `${selectedRecordSet?.name || 'record'}-${index}`,
    })),
    [selectedRecordSet],
  )
  const bundleTemplates = useMemo(
    () => parseBundleTemplates(comparison?.candidate_template.content || ''),
    [comparison?.candidate_template.content],
  )
  const selectedBundleTemplate = bundleTemplates.find(
    (item) => item.name === selectedRecordSet?.name,
  ) || bundleTemplates[0]
  const primaryKeySummary = recordSetContracts.length
    ? recordSetContracts.map((item) => (
        `${item.name}: ${(item.primary_keys || []).join(' + ') || '无主键（单例）'}`
      )).join('；')
    : primaryKeys.join(' + ') || '无主键（单例）'
  const aiReview = candidateContract.ai_review as AiReview | undefined
  const candidateProvenance = (candidateContract.candidate_provenance || {}) as CandidateProvenance
  const isNtcCandidate = candidateProvenance.provider === 'ntc_templates'
  const humanDirectReviewRequired = candidateContract.human_direct_review_required === true
  const checkItemCapabilities = comparison?.check_item_capabilities || []
  const parserFieldContract = comparison?.parser_field_contract
  const reviewAccepted = semanticReview?.status === 'accepted'
  const canSubmitReview = Boolean(
    reviewReadiness.can_review &&
    resultConfirmed &&
    primaryKeyConfirmed &&
    (!humanDirectReviewRequired || directReviewConfirmed),
  )
  const canApprove = Boolean(
    reviewAccepted &&
    activationReadiness.can_activate,
  )

  useEffect(() => {
    if (!proposedRecordSets.length) return
    if (!proposedRecordSets.some((item) => item.name === selectedRecordSetName)) {
      setSelectedRecordSetName(proposedRecordSets[0].name)
    }
  }, [proposedRecordSets, selectedRecordSetName])

  return (
    <Drawer
      title="确认 Parser 最终结果"
      open={open}
      onClose={onClose}
      width={isMobile ? '100vw' : 1180}
      destroyOnClose
      extra={
        comparison ? (
          <Space>
            {onReject && (
              <Button danger icon={<CloseCircleOutlined />} loading={rejecting} onClick={onReject}>
                驳回并修订
              </Button>
            )}
            {reviewAccepted ? (
              <Button
                icon={<CheckOutlined />}
                disabled={!canApprove}
                loading={approving}
                onClick={() => onApprove(approvalReason.trim())}
              >
                批准发布
              </Button>
            ) : (
              <Button
                icon={<CheckOutlined />}
                disabled={!canSubmitReview}
                loading={reviewing}
                onClick={() => onReview(reviewReason.trim())}
              >
                保存人工复核
              </Button>
            )}
          </Space>
        ) : null
      }
    >
      {loading ? (
        <div style={{ display: 'grid', minHeight: 280, placeItems: 'center' }}>
          <Spin />
        </div>
      ) : comparison ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={reviewAccepted ? 'success' : reviewReadiness.can_review ? 'warning' : 'error'}
            message={
              reviewAccepted
                ? '人工语义复核已保存，等待独立发布审批'
                : reviewReadiness.can_review
                  ? humanDirectReviewRequired
                    ? '候选已通过客观执行校验，需要工程师直接复核'
                    : '候选已通过自动校验，等待工程师复核最终结果'
                  : '候选模板暂不可复核'
            }
            description={
              reviewReadiness.blockers.length
                ? reviewReadiness.blockers.join('；')
                : reviewAccepted
                  ? releaseActor?.admin_override_applies
                    ? '当前管理员可以代行发布审批；本次职责分离豁免将写入审计记录。'
                    : '复核结论不会激活 Parser；发布仍须由另一名审批人单独执行。'
                : generationOnly
                  ? `当前使用 ${comparison.sample_count} 份生成样本完成校验；请逐项确认解析结果和主键。`
                  : `全部 ${comparison.sample_count} 份样本均已通过独立验收。`
            }
          />

          <Alert
            showIcon
            type="warning"
            message="Parser 可审批不等于检查项已具备生产能力"
            description={
              checkItemCapabilities.length
                ? checkItemCapabilities.map((item) => {
                    const missingRoles = item.shape_assessment?.missing_required_roles || []
                    const suffix = missingRoles.length
                      ? `；当前输出缺少 ${missingRoles.join('、')} 类型字段`
                      : '；字段形态可承载目标语义，但仍需 Observation Contract 完成标准映射'
                    return `${item.check_item_id}：${item.status}${suffix}`
                  }).join('。')
                : '当前 Parser 尚未通过检查项目录归类；可复核结构化结果，但不能据此声明检查项或 RuntimeBundle 就绪。'
            }
          />

          <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
            <Descriptions.Item label="厂商">{comparison.candidate_template.vendor}</Descriptions.Item>
            <Descriptions.Item label="兼容范围">{comparison.candidate_template.profile}</Descriptions.Item>
            <Descriptions.Item label="命令" span={isMobile ? 1 : 2}>
              {comparison.candidate_template.canonical_command}
            </Descriptions.Item>
            <Descriptions.Item label="解析记录集">
              {recordSetContracts.length ? (
                <Space wrap>
                  {recordSetContracts.map((item) => (
                    <Tag key={item.name}>{item.name}</Tag>
                  ))}
                </Space>
              ) : comparison.candidate_template.category}
            </Descriptions.Item>
            <Descriptions.Item label="主键">
              <Typography.Text>{primaryKeySummary}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="样本通过">
              {comparison.passed_samples}/{comparison.sample_count}
            </Descriptions.Item>
            <Descriptions.Item label="模板摘要">
              <Tag color={comparison.candidate_template.hash_valid ? 'green' : 'red'}>
                {comparison.candidate_template.hash_valid ? '一致' : '不一致'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="模板来源" span={isMobile ? 1 : 4}>
              {isNtcCandidate ? (
                <Space wrap>
                  <Tag color="green">NTC 公共模板</Tag>
                  <Typography.Text>{candidateProvenance.source_template || '来源文件未记录'}</Typography.Text>
                  {candidateProvenance.package_version ? (
                    <Typography.Text type="secondary">
                      ntc_templates {candidateProvenance.package_version}
                    </Typography.Text>
                  ) : null}
                </Space>
              ) : (
                <Space wrap>
                  <Tag>平台生成</Tag>
                  <Typography.Text type="secondary">
                    {candidateProvenance.provider || '生成器来源未记录'}
                  </Typography.Text>
                </Space>
              )}
            </Descriptions.Item>
          </Descriptions>

          <Tabs
            defaultActiveKey="result"
            items={[
              {
                key: 'result',
                label: `最终结果 (${comparison.sample_count})`,
                children: (
                  <Space direction="vertical" size={14} style={{ width: '100%' }}>
                    <Select
                      value={selectedSample?.snapshot_id}
                      style={{ width: isMobile ? '100%' : 560 }}
                      placeholder="选择命令样本"
                      onChange={setSelectedSnapshotId}
                      options={comparison.rows.map((row) => ({
                        value: row.snapshot_id,
                        label: `${row.device_id} · ${row.dataset_id} · ${row.proposed.record_count ?? recordCount(row.proposed.records)} 条记录`,
                      }))}
                    />
                    {selectedSample ? (
                      <>
                        {proposedRecordSets.length > 1 && (
                          <Select
                            aria-label="解析记录集"
                            value={selectedRecordSet?.name}
                            style={{ width: isMobile ? '100%' : 420 }}
                            onChange={setSelectedRecordSetName}
                            options={proposedRecordSets.map((item) => ({
                              value: item.name,
                              label: `${item.name} · ${item.records.length} 条`,
                            }))}
                          />
                        )}
                        <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
                          <Descriptions.Item label="设备">{selectedSample.device_id}</Descriptions.Item>
                          <Descriptions.Item label="数据集">{selectedSample.dataset_id}</Descriptions.Item>
                          <Descriptions.Item label="解析状态">
                            <Tag color={statusColor(selectedSample.proposed.status)}>
                              {selectedSample.proposed.status}
                            </Tag>
                          </Descriptions.Item>
                          <Descriptions.Item label="记录数">
                            {selectedRecordSet?.records.length || 0}
                          </Descriptions.Item>
                          <Descriptions.Item label="当前记录集">
                            {selectedRecordSet?.name || '-'}
                          </Descriptions.Item>
                          <Descriptions.Item label="记录集主键" span={isMobile ? 1 : 3}>
                            {(selectedRecordSet?.primary_keys || []).join(' + ') || '无主键（单例）'}
                          </Descriptions.Item>
                          <Descriptions.Item label="原始数据摘要" span={isMobile ? 1 : 4}>
                            <Typography.Text copyable>{selectedSample.raw_text_sha256}</Typography.Text>
                          </Descriptions.Item>
                        </Descriptions>
                        <div className="parser-approval-evidence">
                          <section>
                            <Typography.Title level={5}>命令样本原始数据</Typography.Title>
                            <pre className="parser-approval-evidence__code parser-approval-evidence__raw">
                              {selectedSample.raw_text || '原始输出为空'}
                            </pre>
                          </section>
                          <section>
                            <Typography.Title level={5}>候选模板解析结果</Typography.Title>
                            <Table
                              rowKey="__approval_row_key"
                              size="small"
                              pagination={false}
                              scroll={{ x: 'max-content', y: 460 }}
                              columns={recordTableColumns}
                              dataSource={recordTableData}
                              locale={{
                                emptyText: selectedSample.proposed.failure_code || '该记录集没有解析记录',
                              }}
                            />
                          </section>
                        </div>
                        {selectedSample.current ? (
                          <section className="parser-approval-evidence__current">
                            <Typography.Title level={5}>当前生产模板解析结果</Typography.Title>
                            <JsonEvidence
                              value={currentRecordSet?.records || selectedSample.current.failure_code}
                            />
                          </section>
                        ) : null}
                      </>
                    ) : (
                      <Alert type="warning" showIcon message="没有可核对的命令样本" />
                    )}
                  </Space>
                ),
              },
              {
                key: 'review',
                label: 'AI 复核与本地门禁',
                children: (
                  <Space direction="vertical" size={16} style={{ width: '100%' }}>
                    <Alert
                      showIcon
                      type={aiReview?.passed ? 'success' : 'warning'}
                      message={aiReview?.passed ? '独立 AI 复核通过' : '独立 AI 复核证据不完整'}
                      description={aiReview?.summary || '暂无独立复核摘要'}
                    />
                    <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                      <Descriptions.Item label="主键评估">
                        <Tag color={aiReview?.primary_key_assessment?.passed ? 'green' : 'red'}>
                          {aiReview?.primary_key_assessment?.passed ? '通过' : '待确认'}
                        </Tag>{' '}
                        {aiReview?.primary_key_assessment?.reason || '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="完整性评估">
                        <Tag color={aiReview?.completeness_assessment?.passed ? 'green' : 'red'}>
                          {aiReview?.completeness_assessment?.passed ? '通过' : '待确认'}
                        </Tag>{' '}
                        {aiReview?.completeness_assessment?.reason || '-'}
                      </Descriptions.Item>
                    </Descriptions>
                    {aiReview?.diagnostics?.length ? (
                      <Alert
                        type="warning"
                        showIcon
                        message="复核诊断"
                        description={aiReview.diagnostics.map((item) => item.message).join('；')}
                      />
                    ) : null}
                    <Table
                      rowKey="name"
                      size="small"
                      pagination={false}
                      dataSource={Object.entries(hardGates).map(([name, passed]) => ({ name, passed }))}
                      locale={{ emptyText: '暂无本地门禁明细' }}
                      columns={[
                        { title: '本地门禁', dataIndex: 'name' },
                        {
                          title: '结果',
                          dataIndex: 'passed',
                          width: 120,
                          render: (passed: boolean) => (
                            <Tag color={passed ? 'green' : 'red'}>{passed ? '通过' : '失败'}</Tag>
                          ),
                        },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: 'capability',
                label: `能力边界 (${checkItemCapabilities.length})`,
                children: (
                  <Space direction="vertical" size={16} style={{ width: '100%' }}>
                    <Alert
                      showIcon
                      type={parserFieldContract?.aligned ? 'success' : 'error'}
                      message={
                        parserFieldContract?.aligned
                          ? 'ParserSpec 与 TextFSM 输出字段一致'
                          : 'ParserSpec 与 TextFSM 输出字段尚未对齐'
                      }
                      description={
                        parserFieldContract?.aligned
                          ? '该结论只证明声明字段与可执行模板字段一致，不证明 canonical 映射或生产规则链已就绪。'
                          : JSON.stringify(
                              parserFieldContract?.differences?.length
                                ? parserFieldContract.differences
                                : parserFieldContract?.errors || ['缺少冻结的 ParserSpec 字段清单'],
                            )
                      }
                    />
                    <Typography.Text type="secondary">
                      {comparison.capability_claim || 'Parser、标准字段映射和生产规则链分别审批，前一阶段通过不能替代后一阶段证明。'}
                    </Typography.Text>
                    {checkItemCapabilities.length ? checkItemCapabilities.map((item) => (
                      <section key={item.check_item_id}>
                        <Space wrap style={{ marginBottom: 10 }}>
                          <Typography.Text strong>{item.check_item_id}</Typography.Text>
                          <Tag color={item.status === 'parser_shape_incomplete' ? 'red' : 'orange'}>
                            {item.status === 'parser_shape_incomplete' ? '字段能力不完整' : '待标准映射'}
                          </Tag>
                          <Tag>Parser 结构化证据</Tag>
                          <Tag color="orange">检查项未就绪</Tag>
                        </Space>
                        <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                          <Descriptions.Item label="目标标准观测" span={isMobile ? 1 : 2}>
                            <Space wrap>
                              {(item.target?.required_observation_types || []).map((value) => (
                                <Tag key={value}>{value}</Tag>
                              ))}
                            </Space>
                          </Descriptions.Item>
                          <Descriptions.Item label="Parser 已输出角色">
                            {(item.shape_assessment?.observed_roles || []).join('、') || '未声明'}
                          </Descriptions.Item>
                          <Descriptions.Item label="缺失字段角色">
                            {(item.shape_assessment?.missing_required_roles || []).join('、') || '无形态缺口'}
                          </Descriptions.Item>
                          <Descriptions.Item label="标准映射状态">
                            {item.canonical_mapping?.status || 'mapping_unverified'}
                          </Descriptions.Item>
                          <Descriptions.Item label="生产就绪">
                            <Tag color="orange">否</Tag>
                          </Descriptions.Item>
                          <Descriptions.Item label="阻断原因" span={isMobile ? 1 : 2}>
                            {(item.blockers || []).map((blocker) => (
                              <div key={blocker.code}>
                                <Typography.Text code>{blocker.code}</Typography.Text>
                                {' '}{blocker.message}
                              </div>
                            ))}
                          </Descriptions.Item>
                        </Descriptions>
                      </section>
                    )) : (
                      <Alert
                        showIcon
                        type="warning"
                        message="尚未归类到标准检查项"
                        description="请先在检查项目录中确认厂商、型号与命令的采集实现映射。"
                      />
                    )}
                  </Space>
                ),
              },
              {
                key: 'template',
                label: 'TextFSM 模板',
                children: (
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Typography.Text copyable>{comparison.candidate_template.sha256}</Typography.Text>
                    {isNtcCandidate ? (
                      <>
                        <Alert
                          showIcon
                          type="info"
                          message="该候选来自公共 NTC 模板"
                          description="平台已冻结 NTC 版本、原始模板和兼容转换记录；生产运行读取审批后的候选资产，不依赖本机 site-packages 的当前内容。"
                        />
                        <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                          <Descriptions.Item label="来源文件">
                            {candidateProvenance.source_template || '-'}
                          </Descriptions.Item>
                          <Descriptions.Item label="NTC 版本">
                            {candidateProvenance.package_version || '-'}
                          </Descriptions.Item>
                          <Descriptions.Item label="开源许可">{candidateProvenance.license || '-'}</Descriptions.Item>
                          <Descriptions.Item label="平台映射">
                            {candidateProvenance.platform || '-'}
                            {candidateProvenance.platform_mapping_version != null
                              ? ` · v${candidateProvenance.platform_mapping_version}`
                              : ''}
                          </Descriptions.Item>
                          <Descriptions.Item label="原始模板摘要" span={isMobile ? 1 : 2}>
                            <Typography.Text copyable>
                              {candidateProvenance.source_content_sha256 || '-'}
                            </Typography.Text>
                          </Descriptions.Item>
                          <Descriptions.Item label="兼容转换" span={isMobile ? 1 : 2}>
                            {candidateProvenance.transformation_version ? (
                              <Space direction="vertical" size={4}>
                                <Tag>{candidateProvenance.transformation_version}</Tag>
                                {(candidateProvenance.transformations || []).map((item, index) => (
                                  <Typography.Text key={`${item.line_number ?? 'line'}-${index}`} type="secondary">
                                    第 {item.line_number ?? '-'} 行：
                                    <Typography.Text code>{item.source || '-'}</Typography.Text>
                                    {' → '}
                                    <Typography.Text code>{item.derived || '-'}</Typography.Text>
                                  </Typography.Text>
                                ))}
                              </Space>
                            ) : (
                              <Typography.Text type="secondary">未执行兼容转换</Typography.Text>
                            )}
                          </Descriptions.Item>
                        </Descriptions>
                        <div className="parser-approval-evidence">
                          <section>
                            <Typography.Title level={5}>NTC 原始模板</Typography.Title>
                            <pre className="parser-approval-evidence__code">
                              {candidateProvenance.source_template_text || '原始模板内容未记录'}
                            </pre>
                          </section>
                          <section>
                            <Typography.Title level={5}>平台最终候选模板</Typography.Title>
                            <pre className="parser-approval-evidence__code">
                              {comparison.candidate_template.content}
                            </pre>
                          </section>
                        </div>
                      </>
                    ) : (
                      <Space direction="vertical" size={10} style={{ width: '100%' }}>
                        {bundleTemplates.length > 1 && (
                          <Select
                            aria-label="TextFSM 记录集"
                            value={selectedBundleTemplate?.name}
                            style={{ width: isMobile ? '100%' : 420 }}
                            onChange={setSelectedRecordSetName}
                            options={bundleTemplates.map((item) => ({
                              value: item.name,
                              label: item.name,
                            }))}
                          />
                        )}
                        <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 620 }}>
                          {selectedBundleTemplate?.template_text || comparison.candidate_template.content}
                        </Typography.Paragraph>
                      </Space>
                    )}
                  </Space>
                ),
              },
              {
                key: 'process',
                label: '过程资产',
                children: (
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">ParserSpec、记录集合同、样本清单和审计元数据。</Typography.Text>
                    <JsonEvidence value={comparison.candidate_contract} />
                  </Space>
                ),
              },
            ]}
          />

          <section className="parser-generation-step">
            <Typography.Text strong>{reviewAccepted ? '独立发布审批' : '人工语义复核'}</Typography.Text>
            {!reviewAccepted ? (
              <Space direction="vertical" size={10} style={{ width: '100%', marginTop: 10 }}>
                <Checkbox checked={resultConfirmed} onChange={(event) => setResultConfirmed(event.target.checked)}>
                  我已核对原始命令输出与最终解析结果，记录完整且字段对应正确
                </Checkbox>
                <Checkbox
                  checked={primaryKeyConfirmed}
                  onChange={(event) => setPrimaryKeyConfirmed(event.target.checked)}
                >
                  我已确认各记录集主键（{primaryKeySummary}）能稳定、唯一地标识记录
                </Checkbox>
                {humanDirectReviewRequired && (
                  <Checkbox
                    checked={directReviewConfirmed}
                    onChange={(event) => setDirectReviewConfirmed(event.target.checked)}
                  >
                    AI 复核未通过或不可用；我已阅读诊断，并以工程师身份直接确认该最终结果符合业务语义
                  </Checkbox>
                )}
                <Input.TextArea
                  rows={3}
                  value={reviewReason}
                  maxLength={500}
                  showCount
                  placeholder="复核说明（可选）；操作人、时间与冻结证据会自动写入审计记录"
                  onChange={(event) => setReviewReason(event.target.value)}
                />
              </Space>
            ) : (
              <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 10 }}>
                <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                  <Descriptions.Item label="复核人">{semanticReview.reviewed_by}</Descriptions.Item>
                  <Descriptions.Item label="复核方式">
                    {semanticReview.review_mode === 'human_direct' ? '工程师直接复核' : 'AI 初审后人工复核'}
                  </Descriptions.Item>
                  <Descriptions.Item label="复核时间">{semanticReview.reviewed_at}</Descriptions.Item>
                  <Descriptions.Item label="证据摘要">
                    <Typography.Text copyable>{semanticReview.evidence_digest}</Typography.Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="复核说明" span={isMobile ? 1 : 2}>
                    {semanticReview.reason}
                  </Descriptions.Item>
                </Descriptions>
                <Alert
                  showIcon
                  type={releaseActor?.admin_override_applies ? 'warning' : 'info'}
                  message={
                    releaseActor?.admin_override_applies
                      ? '管理员代行发布审批'
                      : releaseActor?.same_as_semantic_reviewer
                        ? '当前账号不能批准发布'
                        : '复核与发布职责分离'
                  }
                  description={
                    releaseActor?.admin_override_applies
                      ? '当前管理员与语义复核人为同一账号，可以批准发布；平台会记录管理员职责分离豁免和证据摘要。'
                      : releaseActor?.same_as_semantic_reviewer
                        ? '当前账号已完成语义复核，不能再次批准发布，请切换其他具备发布权限的账号。'
                        : '发布审批人必须与上方复核人不同；管理员可以代行发布，平台会自动留下审计记录。'
                  }
                />
                {activationReadiness.blockers.length ? (
                  <Alert
                    showIcon
                    type="warning"
                    message="当前仍不可发布"
                    description={activationReadiness.blockers.join('；')}
                  />
                ) : null}
                <Input.TextArea
                  rows={3}
                  value={approvalReason}
                  maxLength={500}
                  showCount
                  placeholder="发布说明（可选）；审批人、时间与证据摘要会自动留痕"
                  onChange={(event) => setApprovalReason(event.target.value)}
                />
              </Space>
            )}
          </section>
        </Space>
      ) : null}
    </Drawer>
  )
}

export default ParserTemplateApprovalDrawer
