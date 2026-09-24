import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Grid,
  Input,
  List,
  Pagination,
  Popconfirm,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowRightOutlined,
  EyeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  ruleAssetSourceApi,
  type CheckItemProductionGroup,
  type OfflineCommandAssetStageStatus,
  type OfflineCommandAssetUnit,
  type OfflineCommandAssetUnitDetail,
  type OfflineCommandAssetUnitStage,
} from '@netops/services/api'
import ParserGenerationStep, {
  type ParserProjectionAdapterDraft,
} from '../skills/parser/ParserGenerationStep'
import CommandAutopilotPanel from '../skills/parser/CommandAutopilotPanel'

const stageOptions = [
  ['output_slicing', '命令输出切片'],
  ['identity_grouping', '身份识别与归组'],
  ['parser_generation', 'Parser 生成'],
  ['parser_validation', 'Parser 全样本验证'],
  ['structured_quality', '结构化数据质量'],
  ['adapter_projection', 'A→B Projection / 命令适配器'],
  ['change_policy', 'Change Policy'],
  ['event_rule', 'Event Rule'],
  ['validation_review', '验证、回放与工程复核'],
  ['release', '审批发布'],
].map(([value, label]) => ({ value, label }))

const unitStatusLabel: Record<string, string> = {
  not_started: '未开始',
  ready: '可处理',
  running: '进行中',
  needs_review: '待复核',
  blocked: '已阻断',
  failed: '失败',
  completed: '已完成',
}

const stageStatusLabel: Record<string, string> = {
  not_started: '未开始',
  ready: '可处理',
  running: '进行中',
  needs_review: '待复核',
  blocked: '已阻断',
  failed: '失败',
  passed: '通过',
  skipped: '不适用',
}

const statusColor: Record<string, string> = {
  ready: 'blue',
  running: 'processing',
  needs_review: 'gold',
  blocked: 'red',
  failed: 'red',
  completed: 'green',
  passed: 'green',
}

const modeLabel: Record<string, string> = {
  full_chain: '完整规则链',
  preproduction_mapping: '预生产映射',
  check_item_mapping: '检查项映射',
  observation_only: '仅状态观测',
  change_only: '仅变化检测',
  raw_hash: '原始哈希比较',
  ignored: '忽略',
}

const stageTag = (status?: OfflineCommandAssetStageStatus) => (
  <Tag color={status ? statusColor[status] : undefined}>
    {status ? stageStatusLabel[status] || status : '-'}
  </Tag>
)

const metricValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const assetScopeLabel = (unit: OfflineCommandAssetUnit) => {
  const models = unit.models.filter(Boolean)
  if (models.length === 1 && models[0].toLowerCase() === unit.profile.toLowerCase()) {
    return `${unit.vendor} · ${models[0]}`
  }
  if (models.length) {
    return `${unit.vendor} · Profile ${unit.profile} · 已验证 ${models.join(' / ')}`
  }
  return `${unit.vendor} · Profile ${unit.profile}`
}

const stageStepStatus = (stage: OfflineCommandAssetUnitStage) => {
  if (stage.status === 'passed' || stage.status === 'skipped') return 'finish' as const
  if (stage.status === 'blocked' || stage.status === 'failed') return 'error' as const
  if (['ready', 'running', 'needs_review'].includes(stage.status)) return 'process' as const
  return 'wait' as const
}

const AssetFlowStatus: React.FC<{ unit: OfflineCommandAssetUnit }> = ({ unit }) => (
  <div className="command-asset-unit__asset-states">
    {[
      ['parser_generation', 'Parser'],
      ['adapter_projection', 'Projection'],
      ['change_policy', 'Policy'],
      ['event_rule', 'Event'],
    ].map(([key, label]) => (
      <span key={key}>
        <Typography.Text type="secondary">{label}</Typography.Text>
        {stageTag(unit.stage_statuses[key])}
      </span>
    ))}
  </div>
)

export const CommandAssetDetail: React.FC<{
  detail?: OfflineCommandAssetUnitDetail
  evidencePage: number
  evidencePageSize: number
  loading: boolean
  open: boolean
  generationContent?: React.ReactNode
  initialTab?: 'flow' | 'parser' | 'generation' | 'evidence' | 'attempts' | 'assets'
  onEvidencePageChange: (page: number, pageSize: number) => void
  onConfirmNotApplicable?: () => void
  notApplicableLoading?: boolean
  onCreateAdapter?: (
    draft: ParserProjectionAdapterDraft,
    detail: OfflineCommandAssetUnitDetail,
  ) => void
  onClose: () => void
  onChanged?: () => void
}> = ({
  detail,
  evidencePage,
  evidencePageSize,
  loading,
  open,
  generationContent,
  initialTab = 'flow',
  onEvidencePageChange,
  onConfirmNotApplicable,
  notApplicableLoading = false,
  onCreateAdapter,
  onClose,
  onChanged,
}) => {
  const screens = Grid.useBreakpoint()
  const navigate = useRuleTaskNavigate()
  const isMobile = !screens.md
  const [activeTab, setActiveTab] = useState(initialTab)
  useEffect(() => {
    if (open) setActiveTab(initialTab)
  }, [initialTab, open])
  const handleStageAction = (targetUrl: string) => {
    const target = new URL(targetUrl, window.location.origin)
    const params = target.searchParams
    const action = params.get('action')
    const subtab = params.get('subtab')
    if (
      generationContent
      && (action === 'rule-generation' || subtab === 'rule-generation')
    ) {
      // 同步 next_action 深链参数（尤其是 dataset_ids），否则规则生成步骤会误报“尚未选择”。
      const next = new URLSearchParams(window.location.search)
      let changed = false
      for (const key of [
        'workspace',
        'subtab',
        'action',
        'source_job_id',
        'offline_job_id',
        'command',
        'dataset_ids',
        'dataset_id',
        'rule_seed_job_id',
        'bundle_id',
      ]) {
        const value = params.get(key)
        if (value != null && value !== '' && next.get(key) !== value) {
          next.set(key, value)
          changed = true
        }
      }
      if (changed) {
        navigate(`${window.location.pathname}?${next.toString()}`, { replace: true })
      }
      setActiveTab('generation')
      return
    }
    if (action === 'parser' || subtab === 'jobs') {
      // Parser production is rendered inside this drawer. Keep its deep-link
      // inputs in sync when operators move directly from one command to the
      // next; changing only the local tab leaves the previous command mounted.
      const next = new URLSearchParams(window.location.search)
      let changed = false
      for (const key of [
        'workspace',
        'subtab',
        'action',
        'source_job_id',
        'offline_job_id',
        'command',
        'dataset_ids',
        'dataset_id',
      ]) {
        const value = params.get(key)
        if (value != null && value !== '' && next.get(key) !== value) {
          next.set(key, value)
          changed = true
        }
      }
      if (changed) {
        navigate(`${window.location.pathname}?${next.toString()}`, { replace: true })
      }
      setActiveTab('parser')
      return
    }
    if (action === 'bundle-review' || subtab === 'asset-bundles') {
      setActiveTab('assets')
      return
    }
    navigate(businessHref(targetUrl))
  }
  return (
    <Drawer
      title="命令规则资产生产详情"
      rootClassName="rule-asset-detail-drawer"
      open={open}
      width={isMobile ? '100vw' : 1040}
      destroyOnClose
      loading={loading}
      onClose={onClose}
      extra={detail ? (
        <Space>
          {detail.not_applicable_confirmation?.eligible && onConfirmNotApplicable && (
            <Popconfirm
              title="确认当前设备不适用此命令？"
              description="仅当全部 CLI 证据均为已确认的合法空输出时使用；证据变化后本确认自动失效。"
              okText="确认无需规则"
              cancelText="取消"
              onConfirm={onConfirmNotApplicable}
            >
              <Button loading={notApplicableLoading}>确认无需规则</Button>
            </Popconfirm>
          )}
          {detail.next_action?.target_url && !detail.rule_seed_job_id && (
            <Button
              icon={<ArrowRightOutlined />}
              onClick={() => handleStageAction(detail.next_action?.target_url || '')}
            >
              {detail.next_action.label}
            </Button>
          )}
        </Space>
      ) : null}
    >
      {detail && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <CommandAutopilotPanel jobId={detail.offline_job_id} unitId={detail.id} associated={Boolean(detail.candidate_check_item_id || detail.check_item_id)} />
          <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
            <Descriptions.Item label="规范命令" span={isMobile ? 1 : 3}>
              <Typography.Text code>{detail.canonical_command}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="厂家 / Profile">
              {detail.vendor} / {detail.profile}
            </Descriptions.Item>
            <Descriptions.Item label="兼容型号">
              {detail.models.join('、') || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="生产模式">
              {modeLabel[detail.asset_mode] || detail.asset_mode}
            </Descriptions.Item>
            {detail.candidate_check_item_id && (
              <Descriptions.Item label="预生产 CheckItem" span={isMobile ? 1 : 2}>
                <Tag color="blue">仅用于 Parser / Projection</Tag>
                {' '}{detail.candidate_check_item_name || detail.candidate_check_item_id}
              </Descriptions.Item>
            )}
            <Descriptions.Item label="证据规模">
              {detail.sample_count} 输出 / {detail.device_count} 设备
            </Descriptions.Item>
            <Descriptions.Item label="当前阶段">
              {detail.current_stage_label}
            </Descriptions.Item>
            <Descriptions.Item label="整体状态">
              <Tag color={statusColor[detail.status]}>
                {unitStatusLabel[detail.status] || detail.status}
              </Tag>
            </Descriptions.Item>
          </Descriptions>

          <Tabs
            activeKey={activeTab}
            onChange={(key) => setActiveTab(key as typeof activeTab)}
            items={[
              {
                key: 'flow',
                label: '生产流程',
                children: (
                  <Steps
                    direction="vertical"
                    size="small"
                    items={detail.stages.map((stage) => ({
                      status: stageStepStatus(stage),
                      title: (
                        <Space size={6} wrap>
                          <Typography.Text strong>{stage.label}</Typography.Text>
                          {stageTag(stage.status)}
                        </Space>
                      ),
                      description: (
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          <Typography.Text type="secondary">{stage.detail}</Typography.Text>
                          {stage.blockers.map((blocker) => (
                            <Alert key={blocker} type="error" showIcon message={blocker} />
                          ))}
                          {Object.keys(stage.metrics).length > 0 && (
                            <Descriptions size="small" column={isMobile ? 1 : 2}>
                              {Object.entries(stage.metrics).map(([key, value]) => (
                                <Descriptions.Item key={key} label={key}>
                                  <Typography.Text ellipsis={{ tooltip: metricValue(value) }}>
                                    {metricValue(value)}
                                  </Typography.Text>
                                </Descriptions.Item>
                              ))}
                            </Descriptions>
                          )}
                          {stage.artifacts.length > 0 && (
                            <Space wrap>
                              {stage.artifacts.map((artifact, index) => (
                                <Tag key={`${String(artifact.type)}-${String(artifact.id)}-${index}`}>
                                  {String(artifact.type || 'asset')} · {String(artifact.status || '-')}
                                </Tag>
                              ))}
                            </Space>
                          )}
                          {stage.key === detail.current_stage && detail.next_action?.target_url && (
                            <Alert
                              type={stage.status === 'blocked' || stage.status === 'failed' ? 'error' : 'info'}
                              showIcon
                              message={detail.next_action.description || `下一步：${detail.next_action.label}`}
                              description={stage.key === 'structured_quality'
                                ? '先对当前命令单元的历史输出进行重新解析预演；全部通过后，再由你确认写入结果。'
                                : undefined}
                              action={(
                                <Button
                                  type="primary"
                                  size="small"
                                  icon={<ArrowRightOutlined />}
                                  onClick={() => handleStageAction(detail.next_action?.target_url || '')}
                                >
                                  {detail.next_action.label}
                                </Button>
                              )}
                            />
                          )}
                        </Space>
                      ),
                    }))}
                  />
                ),
              },
              ...(detail.parser_job_id ? [{
                key: 'parser',
                label: 'Parser 生成',
                children: (
                  <ParserGenerationStep
                    jobId={detail.parser_job_id}
                    // A manually associated command and a uniquely resolved
                    // catalog CommandAdapter both identify the same standard
                    // semantic target B.  Parser generation must not ask the
                    // engineer to choose it a second time.
                    lockedCheckItemId={detail.candidate_check_item_id || detail.check_item_id}
                    onCreateAdapter={onCreateAdapter
                      ? (draft) => onCreateAdapter(draft, detail)
                      : undefined}
                  />
                ),
              }] : []),
              {
                key: 'evidence',
                label: `证据样本 (${detail.evidence_pagination.total})`,
                children: detail.evidence.length ? (
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    <Table
                      rowKey="snapshot_id"
                      size="small"
                      scroll={{ x: 880 }}
                      pagination={false}
                      dataSource={detail.evidence}
                      columns={[
                        {
                          title: '周期 / 设备',
                          width: 210,
                          render: (_, row) => (
                            <Space direction="vertical" size={0}>
                              <Typography.Text strong>{row.period?.label || '-'}</Typography.Text>
                              <Typography.Text type="secondary">{row.device_name}</Typography.Text>
                            </Space>
                          ),
                        },
                        { title: '解析状态', dataIndex: 'parse_status', width: 110, render: (value) => <Tag>{value}</Tag> },
                        { title: '输出状态', dataIndex: 'output_status', width: 110 },
                        { title: '结构化记录', dataIndex: 'structured_record_count', width: 110 },
                        {
                          title: '快照哈希',
                          dataIndex: 'raw_text_hash',
                          ellipsis: true,
                          render: (value) => <Typography.Text code copyable>{value}</Typography.Text>,
                        },
                      ]}
                    />
                    <Pagination
                      current={evidencePage}
                      pageSize={evidencePageSize}
                      total={detail.evidence_pagination.total}
                      showSizeChanger={!isMobile}
                      showTotal={(total) => `共 ${total} 条证据`}
                      onChange={onEvidencePageChange}
                    />
                  </Space>
                ) : <Empty description="暂无证据样本" />,
              },
              {
                key: 'attempts',
                label: `Parser 迭代 (${detail.parser_attempts.length})`,
                children: detail.parser_attempts.length ? (
                  <Table
                    rowKey={(row) => String(row.id)}
                    size="small"
                    pagination={false}
                    dataSource={detail.parser_attempts}
                    columns={[
                      { title: '轮次', dataIndex: 'attempt_number', width: 70 },
                      { title: '阶段', dataIndex: 'stage', width: 120 },
                      { title: '动作', dataIndex: 'action' },
                      { title: '模型', render: (_, row) => `${row.model_provider || '-'} / ${row.model_name || '-'}` },
                      { title: '状态', dataIndex: 'status', width: 100, render: (value) => <Tag>{String(value)}</Tag> },
                    ]}
                  />
                ) : <Empty description="尚无 Parser Agent 迭代" />,
              },
              {
                key: 'assets',
                label: `规则资产 (${detail.rule_assets.length})`,
                children: detail.rule_assets.length ? (
                  <Table
                    rowKey={(row) => String(row.job_id)}
                    size="small"
                    pagination={false}
                    dataSource={detail.rule_assets}
                    columns={[
                      { title: '资产类型', dataIndex: 'asset_type' },
                      { title: '版本', dataIndex: 'asset_version_id', ellipsis: true },
                      { title: '状态', dataIndex: 'status', width: 110, render: (value) => <Tag>{String(value)}</Tag> },
                      {
                        title: '确定性验证',
                        dataIndex: 'validation_success',
                        width: 120,
                        render: (value) => <Tag color={value ? 'green' : 'red'}>{value ? '通过' : '未通过'}</Tag>,
                      },
                    ]}
                  />
                ) : <Empty description="尚未生成规则资产" />,
              },
            ]}
          />
        </Space>
      )}
    </Drawer>
  )
}

interface OfflineCommandAssetUnitsProps {
  jobId: string
  initialSearch?: string
  focusAction?: string
  generationContent?: React.ReactNode
}

const OfflineCommandAssetUnits: React.FC<OfflineCommandAssetUnitsProps> = ({
  jobId,
  initialSearch = '',
  focusAction,
  generationContent,
}) => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const navigate = useRuleTaskNavigate()
  const queryClient = useQueryClient()
  const autoRefreshAttempted = useRef(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState(initialSearch)
  const [search, setSearch] = useState(initialSearch)
  const [status, setStatus] = useState<string>()
  const [stage, setStage] = useState<string>()
  const [assetMode, setAssetMode] = useState<string>()
  const [selectedUnitId, setSelectedUnitId] = useState<string>(() => new URLSearchParams(window.location.search).get('unit_id') || undefined)
  const initialFocusOpened = useRef(false)
  const [evidencePage, setEvidencePage] = useState(1)
  const [evidencePageSize, setEvidencePageSize] = useState(25)

  useEffect(() => {
    initialFocusOpened.current = false
    setSelectedUnitId(new URLSearchParams(window.location.search).get('unit_id') || undefined)
    setSearchDraft(initialSearch)
    setSearch(initialSearch)
    setPage(1)
  }, [initialSearch])

  const listQuery = useQuery(
    ['offline-command-asset-units', jobId, page, pageSize, search, status, stage, assetMode],
    () => ruleAssetSourceApi.commandAssetUnits(jobId, {
      page,
      page_size: pageSize,
      search: search || undefined,
      status,
      stage,
      asset_mode: assetMode,
    }),
    { keepPreviousData: true },
  )
  const checkItemGroupsQuery = useQuery(
    ['offline-command-asset-check-item-groups', jobId],
    () => ruleAssetSourceApi.checkItemProductionGroups({
      page: 1,
      page_size: 100,
      offline_job_id: jobId,
    }),
    { keepPreviousData: true },
  )
  const detailQuery = useQuery(
    [
      'offline-command-asset-unit',
      jobId,
      selectedUnitId,
      evidencePage,
      evidencePageSize,
    ],
    () => ruleAssetSourceApi.commandAssetUnit(jobId, selectedUnitId || '', {
      evidence_page: evidencePage,
      evidence_page_size: evidencePageSize,
    }),
    { enabled: Boolean(selectedUnitId), keepPreviousData: false },
  )
  const openUnitDetail = useCallback((unitId: string, canonicalCommand?: string) => {
    setEvidencePage(1)
    setSelectedUnitId(unitId)
    if (canonicalCommand) {
      const next = new URLSearchParams(window.location.search)
      if (next.get('command') !== canonicalCommand || next.get('unit_id') !== unitId) {
        next.set('unit_id', unitId)
        next.set('command', canonicalCommand)
        navigate(`${window.location.pathname}?${next.toString()}`, { replace: true })
      }
    }
  }, [navigate])
  const openClassification = (unit: OfflineCommandAssetUnit) => {
    const params = new URLSearchParams({
      workspace: 'production',
      subtab: 'catalog-management',
      source_job_id: jobId,
      offline_job_id: jobId,
      command: unit.canonical_command,
    })
    navigate(businessHref(`/skills/parser?${params.toString()}`))
  }
  const openAdapterDraft = (
    draft: ParserProjectionAdapterDraft,
    detail: OfflineCommandAssetUnitDetail,
  ) => {
    const params = new URLSearchParams({
      workspace: 'production',
      subtab: 'catalog-management',
    })
    navigate(businessHref(`/skills/parser?${params.toString()}`), {
      state: {
        commandAdapterDraft: {
          checkItemId: draft.checkItemId,
          parserTemplateId: draft.parserTemplateId,
          sourceRecordSet: draft.sourceRecordSet,
          adapterSpec: draft.adapterSpec,
          sourceJobId: detail.offline_job_id,
          command: detail.canonical_command,
          vendor: detail.vendor,
          family: detail.family,
          model: detail.model,
          profile: detail.profile,
        },
      },
    })
  }
  const refreshMutation = useMutation(
    () => ruleAssetSourceApi.refreshCommandAssetUnits(jobId),
    {
      onSuccess: async () => {
        await Promise.all([
          queryClient.invalidateQueries(['offline-command-asset-units', jobId]),
          queryClient.invalidateQueries(['offline-command-asset-check-item-groups', jobId]),
          queryClient.invalidateQueries(['offline-rule-asset-pipeline', jobId]),
          queryClient.invalidateQueries(['offline-command-asset-unit', jobId, selectedUnitId]),
        ])
      },
      onError: (error: Error) => message.error(error.message || '命令资产状态刷新失败'),
    },
  )
  const notApplicableMutation = useMutation(
    () => ruleAssetSourceApi.confirmCommandAssetNotApplicable(
      jobId,
      selectedUnitId || '',
      { reason: '已核对全部 CLI 证据，确认当前设备未配置该命令对应能力。' },
    ),
    {
      onSuccess: async () => {
        message.success('已确认为设备不适用，命令资产无需生成规则')
        await Promise.all([
          queryClient.invalidateQueries(['offline-command-asset-units', jobId]),
          queryClient.invalidateQueries(['offline-command-asset-check-item-groups', jobId]),
          queryClient.invalidateQueries(['offline-command-asset-unit', jobId, selectedUnitId]),
          queryClient.invalidateQueries(['offline-rule-asset-pipeline', jobId]),
        ])
      },
      onError: (error: Error) => message.error(error.message || '确认命令不适用失败'),
    },
  )

  useEffect(() => {
    if (
      listQuery.data
      && listQuery.data.total === 0
      && !listQuery.isFetching
      && !autoRefreshAttempted.current
    ) {
      autoRefreshAttempted.current = true
      refreshMutation.mutate()
    }
  }, [listQuery.data, listQuery.isFetching, refreshMutation])

  useEffect(() => {
    if (
      initialFocusOpened.current
      || !(initialSearch || new URLSearchParams(window.location.search).get('unit_id'))
      || !listQuery.data
      || listQuery.isFetching
    ) return
    const persistedUnitId = new URLSearchParams(window.location.search).get('unit_id')
    if (persistedUnitId) { initialFocusOpened.current = true; setSelectedUnitId(persistedUnitId); return }
    const exactMatch = listQuery.data.items.find(
      (item) => new URLSearchParams(window.location.search).get('unit_id')
        ? item.id === new URLSearchParams(window.location.search).get('unit_id')
        : item.canonical_command === initialSearch,
    )
    initialFocusOpened.current = true
    if (exactMatch) openUnitDetail(exactMatch.id, exactMatch.canonical_command)
  }, [initialSearch, listQuery.data, listQuery.isFetching, openUnitDetail])

  const data = listQuery.data
  const summary = data?.summary
  const notStartedCount = Math.max(
    0,
    (summary?.total || 0)
      - (summary?.completed || 0)
      - (summary?.running || 0)
      - (summary?.needs_review || 0)
      - (summary?.blocked || 0),
  )
  const columns: ColumnsType<OfflineCommandAssetUnit> = [
    {
      title: '命令资产',
      key: 'identity',
      width: 280,
      render: (_, unit) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{unit.canonical_command}</Typography.Text>
          <Typography.Text type="secondary">
            {assetScopeLabel(unit)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '证据覆盖',
      key: 'evidence',
      width: 160,
      render: (_, unit) => (
        <Typography.Text type="secondary">
          {unit.sample_count} 输出 · {unit.device_count} 设备<br />
          {unit.output_signature_count} 输出签名
        </Typography.Text>
      ),
    },
    {
      title: '资产模式',
      dataIndex: 'asset_mode',
      width: 130,
      render: (value) => <Tag>{modeLabel[value] || value}</Tag>,
    },
    {
      title: '规则链',
      key: 'asset_states',
      width: 280,
      render: (_, unit) => <AssetFlowStatus unit={unit} />,
    },
    {
      title: '当前阶段',
      key: 'progress',
      width: 210,
      render: (_, unit) => (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Space size={4} wrap>
            <Typography.Text>{unit.current_stage_label}</Typography.Text>
            <Tag color={statusColor[unit.status]}>{unitStatusLabel[unit.status] || unit.status}</Tag>
          </Space>
          <Progress percent={unit.progress_percent} size="small" showInfo={false} />
          {unit.blocker_count > 0 && (
            <Typography.Text type="danger">{unit.blocker_count} 个阻断原因</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 132,
      render: (_, unit) => (
        <Space size={2}>
          <Button
            type="text"
            icon={<EyeOutlined />}
            aria-label={`查看 ${unit.canonical_command} 资产详情`}
            onClick={() => openUnitDetail(unit.id, unit.canonical_command)}
          >
            详情
          </Button>
          {unit.next_action?.target_url && (
            <Button
              type="text"
              icon={<ArrowRightOutlined />}
              aria-label={unit.next_action.label}
              onClick={() => navigate(businessHref(unit.next_action?.target_url || ''))}
            />
          )}
        </Space>
      ),
    },
  ]
  const checkItemGroupColumns: ColumnsType<CheckItemProductionGroup> = [
    {
      title: '检查项分组',
      key: 'check_item',
      render: (_, group) => {
        const candidate = group.implementations[0]?.candidate_check_item_name
          || group.implementations[0]?.candidate_check_item_id
        return (
          <Space direction="vertical" size={1}>
            <Space size={6} wrap>
              <Typography.Text strong>{group.display_name}</Typography.Text>
              {group.classification_required && (
                <Tag color={group.preproduction_associated ? 'blue' : 'red'}>
                  {group.preproduction_associated ? '待生产映射' : '待归类'}
                </Tag>
              )}
              {!group.check_item_id && !group.classification_required
                && group.category !== 'not_applicable' && <Tag color="orange">未关联检查项</Tag>}
              {group.category === 'not_applicable' && <Tag>无需规则</Tag>}
            </Space>
            <Typography.Text type="secondary">
              {group.check_item_id || (candidate
                ? `预生产 CheckItem：${candidate}`
                : '尚未绑定标准检查项')}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '命令',
      key: 'commands',
      render: (_, group) => (
        <Space size={[4, 4]} wrap>
          {Array.from(new Set(
            group.implementations.map((item) => item.canonical_command),
          )).map((command) => <Tag key={command}>{command}</Tag>)}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 170,
      render: (_, group) => (
        <Space direction="vertical" size={1}>
          <Tag color={group.classification_required
            ? (group.preproduction_associated ? 'blue' : 'red')
            : 'green'}>
            {group.current_stage_label}
          </Tag>
          <Typography.Text type="secondary">
            {group.sample_count} 输出 · {group.evidence_device_count} 设备
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 310,
      render: (_, group) => (
        <Space>
          {group.implementations[0] && (
            <Button
              onClick={() => openUnitDetail(
                group.implementations[0].id,
                group.implementations[0].canonical_command,
              )}
            >
              查看命令
            </Button>
          )}
          {group.classification_required && (
            <Button
              type="primary"
              onClick={() => openClassification(group.implementations[0])}
            >
              {group.preproduction_associated ? '继续 Parser / Projection' : '在检查项目录归类'}
            </Button>
          )}
        </Space>
      ),
    },
  ]

  if (listQuery.isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="命令资产列表加载失败"
        action={<Button icon={<ReloadOutlined />} onClick={() => listQuery.refetch()}>重试</Button>}
      />
    )
  }

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <div className={`command-asset-unit__summary${isMobile ? '' : ' is-desktop'}`}>
        {[
          ['命令资产', summary?.total || 0],
          ['已完成', summary?.completed || 0],
          ['进行中', summary?.running || 0],
          ['未开始', notStartedCount],
          ['待复核', summary?.needs_review || 0],
          ['阻断', summary?.blocked || 0],
        ].map(([label, value]) => (
          <div key={label}>
            <Typography.Text type="secondary">{label}</Typography.Text>
            <Typography.Title level={4}>{value}</Typography.Title>
          </div>
        ))}
      </div>
      <details className="command-group-browser">
        <summary>按检查项归类 · {checkItemGroupsQuery.data?.summary.unclassified || 0} 项待归类</summary>
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <div>
            <Typography.Title level={4} style={{ marginBottom: 2 }}>按检查项归类</Typography.Title>
            <Typography.Text type="secondary">
              样本命令通过厂商、型号和 Command Adapter 自动归入标准检查项。
            </Typography.Text>
          </div>
          {(checkItemGroupsQuery.data?.summary.unclassified || 0) > 0 && (
            <Alert
              showIcon
              type="warning"
              message={`待归类检查项（${checkItemGroupsQuery.data?.summary.unclassified || 0}）`}
              description="每条命令只需一次“关联检查项”：它保存预生产业务目标，随后在同一流程完成 Parser、Projection 与命令适配器审批；审批前不会进入生产运行包。"
            />
          )}
          {checkItemGroupsQuery.isError ? (
            <Alert
              showIcon
              type="error"
              message="检查项分组加载失败"
              action={<Button onClick={() => checkItemGroupsQuery.refetch()}>重试</Button>}
            />
          ) : (
            <Table<CheckItemProductionGroup>
              rowKey="id"
              size="small"
              loading={checkItemGroupsQuery.isLoading || checkItemGroupsQuery.isFetching}
              dataSource={checkItemGroupsQuery.data?.items || []}
              columns={checkItemGroupColumns}
              pagination={false}
              locale={{ emptyText: <Empty description="当前样本任务尚无检查项分组" /> }}
              scroll={{ x: 900 }}
            />
          )}
        </Space>
      </details>
      <div className="command-asset-unit__toolbar">
        <Input.Search
          allowClear
          className="command-asset-unit__search"
          value={searchDraft}
          placeholder="搜索命令、厂家或型号"
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => { setPage(1); setSearch(value.trim()) }}
        />
        <Select
          allowClear
          value={status}
          placeholder="整体状态"
          style={{ width: 120 }}
          options={Object.entries(unitStatusLabel).map(([value, label]) => ({ value, label }))}
          onChange={(value) => { setPage(1); setStatus(value) }}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          value={stage}
          placeholder="当前阶段"
          style={{ width: 190 }}
          options={stageOptions}
          onChange={(value) => { setPage(1); setStage(value) }}
        />
        <Select
          allowClear
          value={assetMode}
          placeholder="资产模式"
          style={{ width: 150 }}
          options={Object.entries(modeLabel).map(([value, label]) => ({ value, label }))}
          onChange={(value) => { setPage(1); setAssetMode(value) }}
        />
        <Button
          icon={<ReloadOutlined />}
          loading={refreshMutation.isLoading}
          onClick={() => refreshMutation.mutate()}
        >
          刷新资产状态
        </Button>
      </div>

      {isMobile ? (
        <div className="command-asset-unit__mobile-list">
          <List
            loading={listQuery.isLoading || listQuery.isFetching}
            dataSource={data?.items || []}
            locale={{ emptyText: <Empty description="尚未形成命令资产单元" /> }}
            renderItem={(unit) => (
              <List.Item>
                <div className="command-asset-unit__mobile-item">
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <div className="command-asset-unit__mobile-header">
                      <Typography.Text code>{unit.canonical_command}</Typography.Text>
                      <Tag color={statusColor[unit.status]}>{unitStatusLabel[unit.status]}</Tag>
                    </div>
                    <Typography.Text type="secondary">
                      {assetScopeLabel(unit)} · {modeLabel[unit.asset_mode]}
                    </Typography.Text>
                    <Progress percent={unit.progress_percent} size="small" />
                    <Typography.Text>
                      {unit.current_stage_label} · {unit.sample_count} 输出 · {unit.device_count} 设备
                    </Typography.Text>
                    <AssetFlowStatus unit={unit} />
                    <Space>
                      <Button icon={<EyeOutlined />} onClick={() => openUnitDetail(unit.id)}>详情</Button>
                      {unit.next_action?.target_url && (
                        <Button
                          icon={<ArrowRightOutlined />}
                          onClick={() => navigate(businessHref(unit.next_action?.target_url || ''))}
                        >
                          {unit.next_action.label}
                        </Button>
                      )}
                    </Space>
                  </Space>
                </div>
              </List.Item>
            )}
          />
          {(data?.total || 0) > 0 && (
            <Pagination
              current={page}
              pageSize={pageSize}
              total={data?.total || 0}
              showSizeChanger
              onChange={(nextPage, nextSize) => {
                setPage(nextSize === pageSize ? nextPage : 1)
                setPageSize(nextSize)
              }}
            />
          )}
        </div>
      ) : (
        <Table<OfflineCommandAssetUnit>
          rowKey="id"
          className="command-asset-unit__table"
          loading={listQuery.isLoading || listQuery.isFetching}
          dataSource={data?.items || []}
          columns={columns}
          scroll={{ x: 1192 }}
          pagination={{
            current: page,
            pageSize,
            total: data?.total || 0,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条命令资产`,
            onChange: (nextPage, nextSize) => {
              setPage(nextSize === pageSize ? nextPage : 1)
              setPageSize(nextSize)
            },
          }}
        />
      )}

      {(data?.total || 0) === 0 && !refreshMutation.isLoading && !listQuery.isLoading && (
        <Alert
          type="info"
          showIcon
          icon={<SafetyCertificateOutlined />}
          message="尚未形成命令级资产生产单元"
          description="完成离线导入后刷新资产状态，系统会按厂家、适配 Profile 和规范命令归并证据。"
        />
      )}
      <CommandAssetDetail
        open={Boolean(selectedUnitId)}
        onChanged={() => { void detailQuery.refetch() }}
        detail={detailQuery.data}
        generationContent={generationContent}
        initialTab={focusAction === 'parser'
          ? 'parser'
          : focusAction === 'rule-generation' || focusAction === 'bundle-review'
            ? 'generation'
            : 'flow'}
        evidencePage={evidencePage}
        evidencePageSize={evidencePageSize}
        loading={detailQuery.isLoading}
        notApplicableLoading={notApplicableMutation.isLoading}
        onConfirmNotApplicable={() => notApplicableMutation.mutate()}
        onCreateAdapter={openAdapterDraft}
        onEvidencePageChange={(nextPage, nextSize) => {
          setEvidencePage(nextSize === evidencePageSize ? nextPage : 1)
          setEvidencePageSize(nextSize)
        }}
        onClose={() => {
          setEvidencePage(1)
          setSelectedUnitId(undefined)
          const next = new URLSearchParams(window.location.search); next.delete('unit_id'); next.delete('command')
          navigate(`${window.location.pathname}?${next}`, { replace: true })
        }}
      />
    </Space>
  )
}

export default OfflineCommandAssetUnits
