import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Descriptions, Drawer, Empty, Input, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd'
import { CheckOutlined, CloseCircleOutlined } from '@ant-design/icons'

import type {
  ParserComparisonParseResult,
  ParserComparisonRecordSet,
  ParserTemplateComparison,
} from '@netops/services/api'
import { statusColor } from './parserUi'

interface ParserTemplateApprovalDrawerProps {
  open: boolean
  onClose: () => void
  isMobile: boolean
  loading: boolean
  comparison?: ParserTemplateComparison | null
  title?: React.ReactNode
  drawerWidth?: number | string
  expandedEvidence?: boolean
  evidenceOnly?: boolean
  readOnly?: boolean
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
  source?: string
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

const displayRecordValue = (value: unknown) => (
  typeof value === 'object' && value !== null
    ? JSON.stringify(value)
    : String(value ?? '')
)

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

const ParserRecordSetEvidence: React.FC<{
  recordSet: ParserComparisonRecordSet
  templateText: string
  templateName?: string
  isNtcCandidate: boolean
  currentRecords?: Array<Record<string, unknown>>
  showCurrentComparison: boolean
}> = ({
  recordSet,
  templateText,
  templateName,
  isNtcCandidate,
  currentRecords,
  showCurrentComparison,
}) => {
  const fields = Array.from(new Set(
    recordSet.records.flatMap((record) => Object.keys(record)),
  ))
  const primaryKeySet = new Set(recordSet.primary_keys || [])
  const columns = fields.map((key) => ({
    title: key,
    dataIndex: key,
    key,
    width: Math.max(140, Math.min(240, key.length * 9 + 48)),
    fixed: primaryKeySet.has(key) ? ('left' as const) : undefined,
    render: (value: unknown) => {
      const text = displayRecordValue(value)
      return <span className="parser-approval-result-table__value" title={text}>{text || '-'}</span>
    },
  }))
  const data = recordSet.records.map((record, index) => ({
    ...record,
    __approval_row_key: `${recordSet.name}-${index}`,
  }))

  return (
    <section style={{ marginTop: 16 }} aria-label={`${recordSet.name} 解析结果`}>
      <Space size={8} wrap style={{ marginBottom: 10 }}>
        <Typography.Text strong>{recordSet.name}</Typography.Text>
        <Typography.Text type="secondary">
          {recordSet.records.length} 条记录 · {fields.length} 个字段
        </Typography.Text>
        <Typography.Text type="secondary">
          主键 {(recordSet.primary_keys || []).join(' + ') || '无（单例）'}
        </Typography.Text>
      </Space>
      <div
        className="parser-approval-workbench__grid"
        style={{ gridTemplateColumns: 'minmax(0, 0.82fr) minmax(0, 1.18fr)' }}
      >
        <section className="parser-approval-workbench__panel parser-approval-workbench__template">
          <header className="parser-approval-workbench__panel-header">
            <Space size={6} wrap>
              <Typography.Text strong>TextFSM 模板</Typography.Text>
              {isNtcCandidate && <Tag color="green">NTC</Tag>}
              {templateName && <Tag>{templateName}</Tag>}
            </Space>
            <Typography.Text type="secondary" copyable={{ text: templateText }}>
              复制模板
            </Typography.Text>
          </header>
          <pre className="parser-approval-workbench__code">{templateText}</pre>
        </section>
        <section className="parser-approval-workbench__panel parser-approval-workbench__result">
          <header className="parser-approval-workbench__panel-header">
            <Typography.Text strong>解析结果</Typography.Text>
            <Typography.Text type="secondary">
              {recordSet.name} · {data.length} 条 · {fields.length} 个字段
            </Typography.Text>
          </header>
          {data.length === 1 ? (
            <dl className="parser-approval-record-detail" aria-label="单条解析记录">
              {fields.map((field) => {
                const value = displayRecordValue(recordSet.records[0]?.[field])
                return (
                  <div className="parser-approval-record-detail__field" key={field}>
                    <dt>
                      <span>{field}</span>
                      {primaryKeySet.has(field) && <Tag>主键</Tag>}
                    </dt>
                    <dd title={value}>{value || '-'}</dd>
                  </div>
                )
              })}
            </dl>
          ) : (
            <Table
              className="parser-approval-result-table"
              rowKey="__approval_row_key"
              size="small"
              pagination={false}
              sticky
              scroll={{ x: 'max-content', y: 420 }}
              columns={columns}
              dataSource={data}
              locale={{ emptyText: '该记录集没有解析记录' }}
            />
          )}
        </section>
      </div>
      {showCurrentComparison && currentRecords ? (
        <details className="parser-approval-workbench__current">
          <summary>与当前生产模板的解析结果对比</summary>
          <JsonEvidence value={currentRecords} />
        </details>
      ) : null}
    </section>
  )
}

const ParserTemplateApprovalDrawer: React.FC<ParserTemplateApprovalDrawerProps> = ({
  open,
  onClose,
  isMobile,
  loading,
  comparison,
  title,
  drawerWidth,
  expandedEvidence = false,
  evidenceOnly = false,
  readOnly = false,
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
  const primaryKeys = useMemo(
    () => (comparison?.candidate_contract.primary_keys || []) as string[],
    [comparison?.candidate_contract],
  )
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
    [comparison?.active_contract, comparison?.candidate_template.category, selectedSample],
  )
  const currentRecordSet = currentRecordSets.find(
    (item) => item.name === selectedRecordSet?.name,
  )
  const recordFields = useMemo(
    () => Array.from(new Set(
      (selectedRecordSet?.records || []).flatMap((record) => Object.keys(record)),
    )),
    [selectedRecordSet],
  )
  const recordTableColumns = useMemo(() => {
    const primaryKeySet = new Set(selectedRecordSet?.primary_keys || [])
    return recordFields.map((key) => ({
      title: key,
      dataIndex: key,
      key,
      width: Math.max(140, Math.min(240, key.length * 9 + 48)),
      fixed: primaryKeySet.has(key) ? ('left' as const) : undefined,
      render: (value: unknown) => {
        const text = displayRecordValue(value)
        return <span className="parser-approval-result-table__value" title={text}>{text || '-'}</span>
      },
    }))
  }, [recordFields, selectedRecordSet?.primary_keys])
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
  const isEngineerCandidate = candidateProvenance.source === 'engineer'
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
  const reviewStatusMessage = readOnly
    ? '已发布 Parser'
    : reviewAccepted
      ? '人工语义复核已保存，等待发布确认'
      : reviewReadiness.can_review
        ? humanDirectReviewRequired
          ? '候选已通过客观执行校验，需要工程师直接复核'
          : '候选已通过自动校验，等待工程师复核最终结果'
        : '候选模板暂不可复核'
  const reviewStatusDetail = reviewReadiness.blockers.length
    ? reviewReadiness.blockers.join('；')
    : reviewAccepted
      ? '复核不会自动激活 Parser；请确认发布，系统会保留两步操作的审计记录。'
      : generationOnly
        ? `当前使用 ${comparison?.sample_count || 0} 份生成样本完成校验；请逐项确认解析结果和主键。`
        : `全部 ${comparison?.sample_count || 0} 份样本均已通过独立验收。`

  useEffect(() => {
    if (!proposedRecordSets.length) return
    if (!proposedRecordSets.some((item) => item.name === selectedRecordSetName)) {
      setSelectedRecordSetName(proposedRecordSets[0].name)
    }
  }, [proposedRecordSets, selectedRecordSetName])

  return (
    <Drawer
      rootClassName={expandedEvidence ? 'parser-approval-drawer parser-approval-drawer--expanded' : 'parser-approval-drawer'}
      title={title || (readOnly ? '查看 Parser 审批证据' : '确认 Parser 最终结果')}
      open={open}
      onClose={onClose}
      width={isMobile ? '100vw' : drawerWidth || 1180}
      destroyOnClose
      extra={
        comparison && !readOnly ? (
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
          <section className="parser-approval-brief">
            <div className="parser-approval-brief__status">
              <Space size={8} wrap>
                <Tag color={readOnly || reviewAccepted ? 'green' : reviewReadiness.can_review ? 'orange' : 'red'}>
                  {readOnly ? '已发布' : reviewAccepted ? '待发布' : reviewReadiness.can_review ? '待复核' : '已阻断'}
                </Tag>
                <Typography.Text strong>{reviewStatusMessage}</Typography.Text>
              </Space>
              {!readOnly && <Typography.Text type="secondary">{reviewStatusDetail}</Typography.Text>}
            </div>
            <Space className="parser-approval-brief__facts" size={12} wrap>
              <Typography.Text code>{comparison.candidate_template.canonical_command}</Typography.Text>
              <Typography.Text type="secondary">
                {comparison.candidate_template.vendor} / {comparison.candidate_template.profile}
              </Typography.Text>
              <Typography.Text type="secondary">
                样本 {comparison.passed_samples}/{comparison.sample_count}
              </Typography.Text>
              <Typography.Text type="secondary">
                {recordSetContracts.length || 1} 个记录集
              </Typography.Text>
              {isNtcCandidate ? <Tag color="green">NTC 公共模板</Tag> : isEngineerCandidate ? <Tag color="blue">工程师修订</Tag> : <Tag>平台生成</Tag>}
            </Space>
          </section>

          <Tabs
            defaultActiveKey="result"
            tabBarStyle={evidenceOnly ? { display: 'none' } : undefined}
            items={[
              {
                key: 'result',
                label: `证据对照 (${comparison.sample_count})`,
                children: (
                  <div className="parser-approval-workbench">
                    <div className="parser-approval-workbench__toolbar">
                      <label className="parser-approval-workbench__control">
                        <span className="parser-approval-workbench__control-label">样本</span>
                        <Select
                          aria-label="命令样本"
                          value={selectedSample?.snapshot_id}
                          placeholder="选择命令样本"
                          onChange={setSelectedSnapshotId}
                          options={comparison.rows.map((row) => ({
                            value: row.snapshot_id,
                            label: `${row.device_id} · ${row.dataset_id} · ${row.proposed.record_count ?? recordCount(row.proposed.records)} 条记录`,
                          }))}
                        />
                      </label>
                      {isMobile && proposedRecordSets.length > 1 && (
                        <label className="parser-approval-workbench__control is-record-set">
                          <span className="parser-approval-workbench__control-label">记录集</span>
                          <Select
                            aria-label="解析记录集"
                            value={selectedRecordSet?.name}
                            onChange={setSelectedRecordSetName}
                            options={proposedRecordSets.map((item) => ({
                              value: item.name,
                              label: `${item.name} · ${item.records.length} 条`,
                            }))}
                          />
                        </label>
                      )}
                    </div>
                    {selectedSample ? (
                      <>
                        <div className="parser-approval-workbench__meta">
                          <span><Typography.Text type="secondary">设备</Typography.Text> {selectedSample.device_id}</span>
                          <span><Typography.Text type="secondary">数据集</Typography.Text> {selectedSample.dataset_id}</span>
                          <span>
                            <Typography.Text type="secondary">状态</Typography.Text>{' '}
                            <Tag color={statusColor(selectedSample.proposed.status)}>
                              {selectedSample.proposed.status}
                            </Tag>
                          </span>
                          {isMobile && (
                            <>
                              <span><Typography.Text type="secondary">记录</Typography.Text> {selectedRecordSet?.records.length || 0}</span>
                              <span>
                                <Typography.Text type="secondary">主键</Typography.Text>{' '}
                                {(selectedRecordSet?.primary_keys || []).join(' + ') || '无主键（单例）'}
                              </span>
                            </>
                          )}
                        </div>
                        <div className="parser-approval-workbench__grid">
                          <section
                            className="parser-approval-workbench__panel parser-approval-workbench__source"
                            style={!isMobile ? { gridColumn: '1 / -1' } : undefined}
                          >
                            <header className="parser-approval-workbench__panel-header">
                              <Space size={6} wrap>
                                <Typography.Text strong>CLI 输出样本</Typography.Text>
                                <Tag>{selectedSample.raw_text_redacted ? '已脱敏' : '原始快照'}</Tag>
                              </Space>
                              <Typography.Text type="secondary" copyable={{ text: selectedSample.raw_text_sha256 }}>
                                证据摘要
                              </Typography.Text>
                            </header>
                            <pre className="parser-approval-workbench__code">
                              {selectedSample.raw_text || '原始输出为空'}
                            </pre>
                          </section>
                          {isMobile && (<>
                          <section className="parser-approval-workbench__panel parser-approval-workbench__template">
                            <header className="parser-approval-workbench__panel-header">
                              <Space size={6} wrap>
                                <Typography.Text strong>TextFSM 模板</Typography.Text>
                                {isNtcCandidate && <Tag color="green">NTC</Tag>}
                                {selectedBundleTemplate?.name && <Tag>{selectedBundleTemplate.name}</Tag>}
                              </Space>
                              <Typography.Text
                                type="secondary"
                                copyable={{
                                  text: selectedBundleTemplate?.template_text
                                    || comparison.candidate_template.content,
                                }}
                              >
                                复制模板
                              </Typography.Text>
                            </header>
                            <pre className="parser-approval-workbench__code">
                              {selectedBundleTemplate?.template_text || comparison.candidate_template.content}
                            </pre>
                          </section>
                          <section className="parser-approval-workbench__panel parser-approval-workbench__result">
                            <header className="parser-approval-workbench__panel-header">
                              <Typography.Text strong>解析结果</Typography.Text>
                              <Typography.Text type="secondary">
                                {selectedRecordSet?.name || 'records'} · {recordTableData.length} 条 · {recordFields.length} 个字段
                              </Typography.Text>
                            </header>
                            {recordTableData.length === 1 ? (
                              <dl className="parser-approval-record-detail" aria-label="单条解析记录">
                                {recordFields.map((field) => {
                                  const value = displayRecordValue(selectedRecordSet?.records[0]?.[field])
                                  const isPrimaryKey = selectedRecordSet?.primary_keys.includes(field)
                                  return (
                                    <div className="parser-approval-record-detail__field" key={field}>
                                      <dt>
                                        <span>{field}</span>
                                        {isPrimaryKey && <Tag>主键</Tag>}
                                      </dt>
                                      <dd title={value}>{value || '-'}</dd>
                                    </div>
                                  )
                                })}
                              </dl>
                            ) : (
                              <Table
                                className="parser-approval-result-table"
                                rowKey="__approval_row_key"
                                size="small"
                                pagination={false}
                                sticky
                                scroll={{ x: 'max-content', y: 420 }}
                                columns={recordTableColumns}
                                dataSource={recordTableData}
                                locale={{
                                  emptyText: selectedSample.proposed.failure_code || '该记录集没有解析记录',
                                }}
                              />
                            )}
                          </section>
                          </>)}
                        </div>
                        {isMobile && selectedSample.current && selectedSample.changed ? (
                          <details className="parser-approval-workbench__current">
                            <summary>与当前生产模板的解析结果对比</summary>
                            <JsonEvidence
                              value={currentRecordSet?.records || selectedSample.current.failure_code}
                            />
                          </details>
                        ) : null}
                        {!isMobile && proposedRecordSets.map((recordSet) => {
                          const bundleTemplate = bundleTemplates.find((item) => item.name === recordSet.name)
                          const currentSet = currentRecordSets.find((item) => item.name === recordSet.name)
                          return (
                            <ParserRecordSetEvidence
                              key={recordSet.name}
                              recordSet={recordSet}
                              templateText={bundleTemplate?.template_text || comparison.candidate_template.content}
                              templateName={bundleTemplate?.name}
                              isNtcCandidate={isNtcCandidate}
                              currentRecords={currentSet?.records}
                              showCurrentComparison={Boolean(selectedSample.current && selectedSample.changed)}
                            />
                          )
                        })}
                      </>
                    ) : (
                      <Alert type="warning" showIcon message="没有可核对的命令样本" />
                    )}
                  </div>
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
                key: 'process',
                label: '过程资产',
                children: (
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">ParserSpec、记录集合同、样本清单和审计元数据。</Typography.Text>
                    {isNtcCandidate && (
                      <>
                        <Alert
                          showIcon
                          type="info"
                          message="NTC 模板来源"
                          description={`${candidateProvenance.source_template || '来源文件未记录'} · ntc_templates ${candidateProvenance.package_version || '-'}`}
                        />
                        <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                          <Descriptions.Item label="开源许可">{candidateProvenance.license || '-'}</Descriptions.Item>
                          <Descriptions.Item label="平台映射">
                            {candidateProvenance.platform || '-'}
                            {candidateProvenance.platform_mapping_version != null
                              ? ` · v${candidateProvenance.platform_mapping_version}`
                              : ''}
                          </Descriptions.Item>
                          <Descriptions.Item label="兼容转换" span={isMobile ? 1 : 2}>
                            {candidateProvenance.transformation_version || '未执行兼容转换'}
                          </Descriptions.Item>
                        </Descriptions>
                        <Typography.Text strong>NTC 原始模板</Typography.Text>
                        <pre className="parser-approval-evidence__code">
                          {candidateProvenance.source_template_text || '原始模板内容未记录'}
                        </pre>
                      </>
                    )}
                    <JsonEvidence value={comparison.candidate_contract} />
                  </Space>
                ),
              },
            ]}
          />

          {!readOnly && <section className="parser-generation-step">
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
                  type="info"
                  message="发布确认"
                  description="有应用使用权的用户可以完成复核与发布。发布时仍需通过业务校验，并记录发布人和证据摘要。"
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
          </section>}
        </Space>
      ) : (
        <Empty description="暂无可核对的 Parser 证据" />
      )}
    </Drawer>
  )
}

export default ParserTemplateApprovalDrawer
