import React, { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  parserApi,
  type ParserRuleAssetBundle,
  type ParserRuleAssetBundleStep,
} from '@netops/services/api'
import StandardTableShell from './StandardTableShell'
import LifecycleActionDialog from './LifecycleActionDialog'
import { governanceStatusLabel, shortDigest } from './parserUi'
import RuleAssetEngineeringReviewDrawer from './RuleAssetEngineeringReviewDrawer'

interface RuleAssetBundleWorkspaceProps {
  onAssetsChanged?: () => void
  onInspectAssetChain?: () => void
  initialSearch?: string
  initialBundleId?: string
  initialCheckItemVersionId?: string
}

const statusColor = (status: string) => {
  if (status === 'active') return 'green'
  if (status === 'pending_approval') return 'blue'
  if (status === 'rejected') return 'red'
  if (status === 'deprecated') return 'default'
  return 'orange'
}

const stepStatus = (step: ParserRuleAssetBundleStep) => {
  if (step.status === 'passed') return 'finish' as const
  if (step.status === 'ready') return 'process' as const
  if (step.status === 'skipped') return 'wait' as const
  return 'error' as const
}

const requiresHistoricalReplay = (row: ParserRuleAssetBundle) => (
  row.asset_requirements?.requires_historical_replay !== false
)

const releaseImpact = (row: ParserRuleAssetBundle) => {
  const labels = [
    row.asset_counts.OBSERVATION_CONTRACT ? 'Observation Contract' : '',
    row.asset_counts.CHANGE_POLICY ? 'Change Policy' : '',
    row.asset_counts.EVENT_RULE ? 'Current State / Event Rule' : '',
  ].filter(Boolean)
  return `${labels.join('、') || '候选规则资产'}将在同一事务内激活；任一步失败都会整体回滚。`
}

const stepLabel: Record<string, string> = {
  context: '脱敏数据上下文',
  contract: '观测契约',
  policy: '变化策略',
  rules: '当前状态 / 事件规则',
  replay: '历史回放',
  release: '审批发布',
  review: '工程师复核',
}

const assetTypeLabel: Record<string, string> = {
  OBSERVATION_CONTRACT: '观测契约',
  CHANGE_POLICY: '变化策略',
  EVENT_RULE: '事件规则',
}

const blockerText: Record<string, string> = {
  'bundle must contain one Contract, one or more Policies and EventRules': '资产不完整：必须包含 1 个观测契约、至少 1 个变化策略和 1 个事件规则',
  'bundle must contain one Contract, one Policy and at least one EventRule': '资产不完整：必须包含 1 个观测契约、至少 1 个变化策略和 1 个事件规则',
  'bundle child jobs are incomplete': '资产记录不完整：部分候选子资产已缺失，请重新生成整套资产',
  'one or more child assets failed deterministic validation': '至少一项候选资产未通过确定性校验',
  'one or more child assets are not pending approval or failed validation': '至少一项候选资产状态异常或未通过确定性校验',
  'historical replay is missing, failed or has collisions': '历史回放缺失、失败或存在规则碰撞',
  'immutable context has no replay_run_ids': '不可变上下文未绑定巡检 Run，无法证明回放数据范围',
  'historical replay must be scoped by immutable run_ids': '历史回放未限定为不可变上下文中的巡检 Run',
  'historical replay run_ids do not match immutable context': '历史回放 Run 范围与不可变上下文不一致',
  'historical replay run_id_count is inconsistent': '历史回放 Run 数量与范围明细不一致',
  'historical replay contains no Change evidence': '历史回放没有读取到任何 Change，不能证明规则有效',
  'historical replay sampled_change_count exceeds total_change_count': '历史回放采样数量超过变化总数，统计结果不一致',
  'historical replay sampled_run_ids exceed immutable run scope': '历史回放采样 Run 超出不可变上下文范围',
  'historical replay sampled_run_id_count is inconsistent': '历史回放采样 Run 数量与明细不一致',
  'historical replay Change evidence is not bound to an immutable run': '历史 Change 证据未绑定到不可变巡检 Run',
  'some immutable runs contain no Change evidence': '部分巡检 Run 没有产生 Change，请确认这是基线周期还是数据缺口',
  'immutable sanitized context gate failed': '不可变脱敏上下文未通过隐私或一致性门禁',
  'engineering review has not accepted the generated candidates': '候选资产尚未通过工程师复核',
}

const displayBlocker = (value: string) => blockerText[value] || value
const displayBlockers = (values: string[]) => [...new Set(values.map(displayBlocker))].join('；')

const errorText = (error: any) => (
  error?.response?.data?.detail || error?.message || '规则资产包操作失败'
)

const replayScopeSummary = (row: ParserRuleAssetBundle) => {
  const scope = row.replay.scope || {}
  const gate = row.replay_gate || {}
  const count = Number(scope.run_id_count ?? gate.actual_run_ids?.length ?? 0)
  if (scope.mode === 'run_ids' || gate.scope_required) return `限定 ${count} 个巡检 Run`
  return '手工兼容回放'
}

const compactRunIds = (values?: string[]) => {
  const items = values || []
  if (items.length <= 5) return items.join('、')
  return `${items.slice(0, 5).join('、')} 等 ${items.length} 个`
}

const compactMetrics = (values?: Record<string, number>, limit = 8) => {
  const entries = Object.entries(values || {}).sort((left, right) => right[1] - left[1])
  return {
    items: entries.slice(0, limit),
    remaining: Math.max(0, entries.length - limit),
  }
}

const RuleAssetBundleWorkspace: React.FC<RuleAssetBundleWorkspaceProps> = ({
  onAssetsChanged,
  onInspectAssetChain,
  initialSearch = '',
  initialBundleId,
  initialCheckItemVersionId,
}) => {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState(initialSearch)
  const [status, setStatus] = useState<string | undefined>()
  const [lifecycleRequest, setLifecycleRequest] = useState<{
    action: 'approve' | 'reject' | 'deprecate'
    row: ParserRuleAssetBundle
  } | null>(null)
  const [engineeringBundle, setEngineeringBundle] = useState<ParserRuleAssetBundle | null>(null)
  const [autoOpenedBundleId, setAutoOpenedBundleId] = useState<string | undefined>()
  const pageSize = 20
  const query = useQuery(
    ['parser-rule-asset-bundles', page, search, status],
    () => parserApi.ruleAssetBundles({
      limit: pageSize,
      offset: (page - 1) * pageSize,
      search: search || undefined,
      status,
    }),
    { keepPreviousData: true },
  )
  useEffect(() => {
    setSearch(initialSearch)
    setPage(1)
  }, [initialSearch])
  useEffect(() => {
    if (!initialBundleId || autoOpenedBundleId === initialBundleId) return
    const bundle = (query.data?.items || []).find((item) => item.id === initialBundleId)
    if (!bundle) return
    setEngineeringBundle(bundle)
    setAutoOpenedBundleId(initialBundleId)
  }, [autoOpenedBundleId, initialBundleId, query.data?.items])
  const lifecycle = useMutation(
    ({ action, id, reason }: { action: 'approve' | 'reject' | 'deprecate'; id: string; reason: string }) => {
      if (action === 'approve') return parserApi.approveRuleGenerationJob(id, { reason })
      if (action === 'reject') return parserApi.rejectRuleGenerationJob(id, { reason })
      return parserApi.deprecateRuleGenerationJob(id, { reason })
    },
    {
      onSuccess: async (_result, variables) => {
        message.success(variables.action === 'approve' ? '规则资产包已原子发布' : variables.action === 'reject' ? '候选资产包已拒绝' : '资产包已废弃并执行版本回退')
        setLifecycleRequest(null)
        await query.refetch()
        onAssetsChanged?.()
      },
      onError: (error: any) => message.error(errorText(error)),
    },
  )

  const renderActions = (row: ParserRuleAssetBundle) => (
    <Space size={4} wrap>
      {row.status === 'pending_approval' && (
        <>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => setEngineeringBundle(row)}
          >
            复核候选
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<CheckOutlined />}
            disabled={!row.release_ready}
            onClick={() => setLifecycleRequest({ action: 'approve', row })}
          >
            审批发布
          </Button>
          <Button
            size="small"
            icon={<CloseOutlined />}
            onClick={() => setLifecycleRequest({ action: 'reject', row })}
          >
            拒绝
          </Button>
        </>
      )}
      {row.status === 'rejected' && (
        <Button size="small" icon={<EditOutlined />} onClick={() => setEngineeringBundle(row)}>
          修订候选
        </Button>
      )}
      {row.status === 'active' && (
        <Button
          size="small"
          icon={<StopOutlined />}
          onClick={() => setLifecycleRequest({ action: 'deprecate', row })}
        >
          回退
        </Button>
      )}
    </Space>
  )

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {initialSearch && !query.isLoading && Number(query.data?.total || 0) === 0 && (
        <Alert
          type="info"
          showIcon
          message={initialCheckItemVersionId ? '当前 CheckItem 版本没有待治理的规则资产包' : '当前样本任务没有待治理的候选资产包'}
          description={initialCheckItemVersionId
            ? '该检查项可能不需要规则资产，或尚未从其标准语义 B 创建规则草稿。'
            : '没有候选包不代表运行时资产缺失。请先检查 Contract、Policy 与 EventRule 资产链；只有存在缺口时才需要生成或补齐。'}
          action={onInspectAssetChain && (
            <Button size="small" onClick={onInspectAssetChain}>检查资产链</Button>
          )}
        />
      )}
      <Alert
        type="info"
        showIcon
        message="候选资产按 Bundle 整体验证、审批和发布"
        description="每套 Bundle 只包含检查项声明需要的资产，并通过受控生成上下文、确定性校验以及必要时的历史回放。子资产不能绕过 Bundle 单独激活。"
      />
      {initialSearch && (
        <Alert
          type="info"
          showIcon
          message={initialCheckItemVersionId
            ? `正在查看 CheckItem 版本 ${initialCheckItemVersionId} 的规则资产包`
            : `正在查看样本任务 ${initialSearch} 的规则资产包`}
          description={initialCheckItemVersionId
            ? '规则候选仅由该版本的标准语义 B 编译，不读取样本或 Parser。'
            : '列表通过不可变生成上下文关联样本任务，不依赖资产名称或命令文本猜测。'}
        />
      )}
      <Space wrap>
        <Input.Search
          allowClear
          placeholder="搜索 Bundle、来源或资产"
          style={{ width: 300, maxWidth: '100%' }}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onSearch={(value) => { setSearch(value.trim()); setPage(1) }}
        />
        <Select
          allowClear
          placeholder="发布状态"
          style={{ width: 170 }}
          value={status}
          onChange={(value) => { setStatus(value); setPage(1) }}
          options={['draft', 'validating', 'pending_approval', 'active', 'rejected', 'deprecated'].map((value) => ({
            value,
            label: governanceStatusLabel(value),
          }))}
        />
        <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>
      </Space>
      <StandardTableShell<ParserRuleAssetBundle>
        rowKey="id"
        childrenColumnName="__tableTreeChildren"
        loading={query.isLoading || query.isFetching}
        error={query.error}
        onRetry={() => query.refetch()}
        dataSource={query.data?.items || []}
        emptyText="暂无规则资产包"
        pagination={{
          current: page,
          pageSize,
          total: query.data?.total || 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 套`,
          onChange: setPage,
        }}
        columns={[
          {
            title: '资产语义',
            width: 330,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={3}>
                <Typography.Text strong>
                  {(row.observation_types || []).length > 1
                    ? `完整命令规则资产 · ${row.observation_types?.length} 个观测语义`
                    : row.observation_type || row.semantic_label || '待识别 Observation Type'}
                </Typography.Text>
                <Typography.Text type="secondary">{row.command || row.source_ref || '-'}</Typography.Text>
                {(row.observation_types || []).length > 1 && (
                  <Space wrap size={[4, 2]}>
                    {(row.observation_types || []).slice(0, 3).map((item) => <Tag key={item}>{item}</Tag>)}
                    {(row.observation_types || []).length > 3 && <Tag>另 {(row.observation_types || []).length - 3} 项</Tag>}
                  </Space>
                )}
              </Space>
            ),
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 120,
            render: (value: string) => <Tag color={statusColor(value)}>{governanceStatusLabel(value)}</Tag>,
          },
          {
            title: '资产组成',
            width: 260,
            render: (_: unknown, row) => (
              <Space wrap size={4}>
                <Tag>观测契约 {row.asset_counts.OBSERVATION_CONTRACT || 0}</Tag>
                <Tag>变化策略 {row.asset_counts.CHANGE_POLICY || 0}</Tag>
                <Tag>当前状态 / 事件规则 {row.asset_counts.EVENT_RULE || 0}</Tag>
              </Space>
            ),
          },
          {
            title: '历史回放',
            width: 250,
            render: (_: unknown, row) => requiresHistoricalReplay(row) ? (
              <Space direction="vertical" size={0}>
                <Typography.Text>
                  命中 {String(row.replay.matched_change_count ?? '-')} / 样本 {String(row.replay.sampled_change_count ?? '-')}
                </Typography.Text>
                <Typography.Text type={(Number(row.replay.collision_count) || 0) > 0 ? 'danger' : 'secondary'}>
                  碰撞 {String(row.replay.collision_count ?? '-')} · 替换旧规则 {row.replay.replacement_rule_ids?.length || 0}
                </Typography.Text>
                <Tag color={row.replay_gate?.success === false ? 'red' : 'green'}>
                  {row.replay_gate?.evidence_success === false ? '没有有效 Change 证据' : replayScopeSummary(row)}
                </Tag>
              </Space>
            ) : <Tag>不需要历史回放</Tag>,
          },
          {
            title: '上下文',
            width: 170,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text>{row.context_gate?.mode === 'check_item_semantics' ? 'CheckItem 标准语义 B' : row.context_id || '手工兼容模式'}</Typography.Text>
                <Typography.Text type="secondary">{shortDigest(row.context_digest)}</Typography.Text>
              </Space>
            ),
          },
          {
            title: '复核 / 阻断',
            width: 250,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={2}>
                <Tag color={row.engineering_review?.decision === 'accepted' ? 'green' : 'orange'}>
                  {row.engineering_review?.decision === 'accepted' ? '工程师已接受' : '等待工程师复核'}
                </Tag>
                {row.blockers.length
                  ? <Typography.Text type="danger">{displayBlockers(row.blockers)}</Typography.Text>
                  : <Typography.Text type="secondary">全部门禁已通过</Typography.Text>}
              </Space>
            ),
          },
          { title: '操作', width: 180, render: (_: unknown, row) => renderActions(row) },
        ]}
        expandable={{
          expandedRowRender: (row) => (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Steps
                responsive
                size="small"
                items={row.steps.map((step) => ({
                  title:
                    step.key === 'context'
                      ? step.label
                      : stepLabel[step.key] || step.label,
                  status: stepStatus(step),
                  description: step.key === 'replay'
                    ? requiresHistoricalReplay(row)
                      ? `命中 ${String(step.detail.matched_change_count ?? '-')}，碰撞 ${String(step.detail.collision_count ?? '-')}`
                      : '该资产模式不依赖双周期 Change'
                    : undefined,
                }))}
              />
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Space wrap size={[8, 4]}>
                  <Typography.Text strong>{row.context_gate?.mode === 'check_item_semantics' ? '规则生成依据' : 'LLM 数据边界'}</Typography.Text>
                  <Tag color={row.context_gate?.success ? 'green' : 'red'}>
                    {row.context_gate?.mode === 'check_item_semantics'
                      ? (row.context_gate?.success ? '标准语义校验已通过' : '标准语义校验未通过')
                      : (row.context_gate?.success ? '隐私门禁已通过' : '隐私门禁未通过')}
                  </Tag>
                  <Tag color={row.context_gate?.privacy?.raw_cli_included ? 'red' : 'green'}>
                    原始 CLI {row.context_gate?.privacy?.raw_cli_included ? '已包含' : '0'}
                  </Tag>
                  <Tag>来源 Snapshot {String(row.context_gate?.privacy?.source_snapshot_count ?? '-')}</Tag>
                  <Tag>候选记录 {String(row.context_gate?.privacy?.candidate_record_count ?? '-')}</Tag>
                  <Tag>脱敏结构化记录 {String(row.context_gate?.privacy?.sampled_record_count ?? '-')}</Tag>
                </Space>
                {(row.context_gate?.errors || []).length > 0 && (
                  <Alert
                    type="error"
                    showIcon
                    message="脱敏上下文不满足发布要求"
                    description={displayBlockers(row.context_gate?.errors || [])}
                  />
                )}
                <Typography.Text type="secondary">
                  {row.context_gate?.mode === 'check_item_semantics'
                    ? `仅使用 CheckItem 标准语义 B；上下文摘要 ${shortDigest(row.context_gate?.context_digest || row.context_digest)}。`
                    : `生成输入仅包含白名单结构化字段；上下文摘要 ${shortDigest(row.context_gate?.context_digest || row.context_digest)}，绑定 ${row.context_gate?.replay_run_id_count ?? '-'} 个巡检 Run。`}
                </Typography.Text>
              </Space>
              <Space wrap size={[8, 4]}>
                <Typography.Text strong>回放摘要</Typography.Text>
                <Tag>样本 {String(row.replay.sampled_change_count ?? '-')}</Tag>
                <Tag>命中 {String(row.replay.matched_change_count ?? '-')}</Tag>
                <Tag>事件 {String(row.replay.event_count ?? '-')}</Tag>
                <Tag color={(Number(row.replay.collision_count) || 0) > 0 ? 'red' : 'green'}>
                  碰撞 {String(row.replay.collision_count ?? '-')}
                </Tag>
                <Tag color="blue">
                  替换旧规则 {row.replay.replacement_rule_ids?.length || 0}（不计碰撞）
                </Tag>
                <Tag>预估每日事件 {String(row.replay.estimated_daily_event_count ?? '-')}</Tag>
              </Space>
              {row.replay_gate?.evidence_success === false && (
                <Alert
                  type="error"
                  showIcon
                  message="回放样本为 0，候选资产禁止审批"
                  description="请导入至少两个可比较周期并完成 State/Change 生成，再重新执行规则资产生成或工程修订。"
                />
              )}
              {row.replay.representativeness && (
                <Space wrap size={[8, 4]}>
                  <Typography.Text strong>回放代表性</Typography.Text>
                  <Tag>
                    覆盖 {String(row.replay.representativeness.sampled_change_count ?? '-')}/
                    {String(row.replay.representativeness.total_change_count ?? '-')} 条历史变化
                  </Tag>
                  <Tag>窗口 {String(row.replay.representativeness.window_days ?? '-')} 天</Tag>
                  <Tag>
                    变化形态 {String(row.replay.representativeness.matched_change_shape_count ?? '-')}/
                    {String(row.replay.representativeness.change_shape_count ?? '-')} 种命中
                  </Tag>
                  {row.replay.representativeness.sparse_history && (
                    <Tag color="orange">历史稀疏：碰撞=0 说服力有限，建议扩大回放窗口</Tag>
                  )}
                </Space>
              )}
              {Object.keys(row.replay.event_types || {}).length > 0 && (() => {
                const metrics = compactMetrics(row.replay.event_types)
                return (
                  <Space wrap size={[8, 4]}>
                    <Typography.Text strong>事件类型</Typography.Text>
                    {metrics.items.map(([name, count]) => <Tag key={name}>{name} {count}</Tag>)}
                    {metrics.remaining > 0 && <Tag>另 {metrics.remaining} 类</Tag>}
                  </Space>
                )
              })()}
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Space wrap size={[8, 4]}>
                  <Typography.Text strong>回放范围</Typography.Text>
                  <Tag color={row.replay_gate?.success === false ? 'red' : 'green'}>
                    {row.replay_gate?.success === false ? '范围门禁未通过' : '范围门禁已通过'}
                  </Tag>
                  <Tag>{replayScopeSummary(row)}</Tag>
                  <Tag>
                    实际采样 {String(row.replay.scope?.sampled_run_id_count ?? row.replay.scope?.sampled_run_ids?.length ?? '-')}
                  </Tag>
                </Space>
                {(row.replay_gate?.errors || []).length > 0 && (
                  <Alert
                    type="error"
                    showIcon
                    message="回放范围证据不满足发布要求"
                    description={displayBlockers(row.replay_gate?.errors || [])}
                  />
                )}
                {(row.replay_gate?.warnings || []).length > 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    message="回放范围存在未覆盖 Run"
                    description={displayBlockers(row.replay_gate?.warnings || [])}
                  />
                )}
                {(row.replay_gate?.actual_run_ids || row.replay.scope?.run_ids || []).length > 0 && (
                  <Typography.Text type="secondary" copyable>
                    巡检 Run：{compactRunIds(row.replay_gate?.actual_run_ids || row.replay.scope?.run_ids)}
                  </Typography.Text>
                )}
                {(row.replay_gate?.missing_run_ids || row.replay.scope?.missing_run_ids || []).length > 0 && (
                  <Typography.Text type="warning" copyable>
                    未产生 Change 的 Run：{compactRunIds(row.replay_gate?.missing_run_ids || row.replay.scope?.missing_run_ids)}
                  </Typography.Text>
                )}
                {(row.offline_job_ids || (row.offline_job_id ? [row.offline_job_id] : [])).length > 0 && (
                  <Typography.Text type="secondary" copyable>
                    样本任务：{(row.offline_job_ids || [row.offline_job_id!]).join('、')}
                  </Typography.Text>
                )}
                {(row.dataset_ids || []).length > 0 && (
                  <Typography.Text type="secondary" copyable>
                    数据集：{(row.dataset_ids || []).join('、')}
                  </Typography.Text>
                )}
              </Space>
              <Space direction="vertical" size={2}>
                <Typography.Text strong>候选资产版本</Typography.Text>
                {row.children.map((child, index) => {
                  const type = String(child.asset_type || child.job_type || 'unknown')
                  return (
                    <Typography.Text key={String(child.id || `${type}-${index}`)} type="secondary" copyable>
                      {assetTypeLabel[type] || type} · {String(child.asset_version_id || child.asset_id || child.id || '-')}
                      {' · '}{governanceStatusLabel(String(child.status || 'unknown'))}
                    </Typography.Text>
                  )
                })}
              </Space>
              <Typography.Text type="secondary">
                Bundle {row.id} · 创建于 {row.created_at || '-'}
              </Typography.Text>
            </Space>
          ),
        }}
        scroll={{ x: 1500 }}
      />
      <LifecycleActionDialog
        open={Boolean(lifecycleRequest)}
        title={lifecycleRequest?.action === 'approve'
          ? '审批并发布规则资产包'
          : lifecycleRequest?.action === 'reject'
            ? '拒绝候选规则资产包'
            : '废弃并回退规则资产包'}
        actionLabel={lifecycleRequest?.action === 'approve'
          ? '审批发布'
          : lifecycleRequest?.action === 'reject'
            ? '确认拒绝'
            : '确认回退'}
        resourceName={lifecycleRequest?.row.semantic_label || lifecycleRequest?.row.observation_type || lifecycleRequest?.row.id || '规则资产包'}
        impact={lifecycleRequest?.action === 'approve'
          ? releaseImpact(lifecycleRequest.row)
          : lifecycleRequest?.action === 'reject'
            ? '整套候选资产将被拒绝，不会进入生产运行时；候选内容和验证证据仍会保留。'
            : '当前资产包的全部 active 版本将一起废弃，并尝试恢复各资产最近的上一版本。'}
        blockers={lifecycleRequest?.action === 'approve' && !lifecycleRequest.row.release_ready
          ? lifecycleRequest.row.blockers.map(displayBlocker)
          : []}
        danger={lifecycleRequest?.action !== 'approve'}
        loading={lifecycle.isLoading}
        onCancel={() => setLifecycleRequest(null)}
        onConfirm={(reason) => lifecycleRequest && lifecycle.mutate({
          action: lifecycleRequest.action,
          id: lifecycleRequest.row.id,
          reason,
        })}
      />
      <RuleAssetEngineeringReviewDrawer
        open={Boolean(engineeringBundle)}
        bundle={engineeringBundle}
        onClose={() => setEngineeringBundle(null)}
        onChanged={async () => {
          await query.refetch()
          onAssetsChanged?.()
        }}
      />
    </Space>
  )
}

export default RuleAssetBundleWorkspace
