import React, { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Popconfirm,
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
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  parserApi,
  type ParserRuleAssetBundle,
  type ParserRuleAssetBundleStep,
} from '../../../services/api'
import StandardTableShell from './StandardTableShell'
import { governanceStatusLabel, shortDigest } from './parserUi'

interface RuleAssetBundleWorkspaceProps {
  onAssetsChanged?: () => void
  onInspectAssetChain?: () => void
  initialSearch?: string
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
  return 'error' as const
}

const stepLabel: Record<string, string> = {
  context: '脱敏数据上下文',
  contract: '观测契约',
  policy: '变化策略',
  rules: '事件规则',
  replay: '历史回放',
  release: '审批发布',
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
  'immutable sanitized context gate failed': '不可变脱敏上下文未通过隐私或一致性门禁',
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
}) => {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState(initialSearch)
  const [status, setStatus] = useState<string | undefined>()
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
  const lifecycle = useMutation(
    ({ action, id }: { action: 'approve' | 'reject' | 'deprecate'; id: string }) => {
      if (action === 'approve') return parserApi.approveRuleGenerationJob(id, { actor: 'ui' })
      if (action === 'reject') return parserApi.rejectRuleGenerationJob(id, { actor: 'ui' })
      return parserApi.deprecateRuleGenerationJob(id, { actor: 'ui', reason: 'manual rollback from control plane' })
    },
    {
      onSuccess: async (_result, variables) => {
        message.success(variables.action === 'approve' ? '规则资产包已原子发布' : variables.action === 'reject' ? '候选资产包已拒绝' : '资产包已废弃并执行版本回退')
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
          <Popconfirm
            title="整包审批并发布？"
            description="Contract、Policy 和全部 EventRule 将在同一事务内激活；任一步失败都会整体回滚。"
            okText="审批发布"
            cancelText="取消"
            disabled={!row.release_ready}
            onConfirm={() => lifecycle.mutate({ action: 'approve', id: row.id })}
          >
            <Button
              size="small"
              type="primary"
              icon={<CheckOutlined />}
              disabled={!row.release_ready}
              loading={lifecycle.isLoading}
            >
              审批发布
            </Button>
          </Popconfirm>
          <Popconfirm
            title="拒绝整套候选资产？"
            okText="拒绝"
            cancelText="取消"
            onConfirm={() => lifecycle.mutate({ action: 'reject', id: row.id })}
          >
            <Button size="small" icon={<CloseOutlined />} loading={lifecycle.isLoading}>拒绝</Button>
          </Popconfirm>
        </>
      )}
      {row.status === 'active' && (
        <Popconfirm
          title="废弃并回退整套资产？"
          description="当前 Bundle 的全部 active 版本将一起废弃，并恢复各资产最近的上一版本。"
          okText="确认回退"
          cancelText="取消"
          onConfirm={() => lifecycle.mutate({ action: 'deprecate', id: row.id })}
        >
          <Button size="small" icon={<StopOutlined />} loading={lifecycle.isLoading}>回退</Button>
        </Popconfirm>
      )}
    </Space>
  )

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {initialSearch && !query.isLoading && Number(query.data?.total || 0) === 0 && (
        <Alert
          type="info"
          showIcon
          message="当前离线任务没有待治理的候选资产包"
          description="没有候选包不代表运行时资产缺失。请先检查 Contract、Policy 与 EventRule 资产链；只有存在缺口时才需要生成或补齐。"
          action={onInspectAssetChain && (
            <Button size="small" onClick={onInspectAssetChain}>检查资产链</Button>
          )}
        />
      )}
      <Alert
        type="info"
        showIcon
        message="候选资产按 Bundle 整体验证、审批和发布"
        description="一套 Bundle 必须同时包含 Observation Contract、Change Policy 和 Event Rules，并通过脱敏上下文、确定性校验与历史回放。子资产不能绕过 Bundle 单独激活。"
      />
      {initialSearch && (
        <Alert
          type="info"
          showIcon
          message={`正在查看离线任务 ${initialSearch} 的规则资产包`}
          description="列表通过不可变生成上下文关联离线任务，不依赖资产名称或命令文本猜测。"
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
            width: 280,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{row.observation_type || '待识别 Observation Type'}</Typography.Text>
                <Typography.Text type="secondary">{row.command || row.source_ref || '-'}</Typography.Text>
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
                <Tag>事件规则 {row.asset_counts.EVENT_RULE || 0}</Tag>
              </Space>
            ),
          },
          {
            title: '历史回放',
            width: 250,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text>
                  命中 {String(row.replay.matched_change_count ?? '-')} / 样本 {String(row.replay.sampled_change_count ?? '-')}
                </Typography.Text>
                <Typography.Text type={(Number(row.replay.collision_count) || 0) > 0 ? 'danger' : 'secondary'}>
                  碰撞 {String(row.replay.collision_count ?? '-')} · 替换旧规则 {row.replay.replacement_rule_ids?.length || 0}
                </Typography.Text>
                <Tag color={row.replay_gate?.success === false ? 'red' : 'green'}>
                  {replayScopeSummary(row)}
                </Tag>
              </Space>
            ),
          },
          {
            title: '上下文',
            width: 170,
            render: (_: unknown, row) => (
              <Space direction="vertical" size={0}>
                <Typography.Text>{row.context_id || '手工兼容模式'}</Typography.Text>
                <Typography.Text type="secondary">{shortDigest(row.context_digest)}</Typography.Text>
              </Space>
            ),
          },
          {
            title: '阻断',
            width: 250,
            render: (_: unknown, row) => row.blockers.length
              ? <Typography.Text type="danger">{displayBlockers(row.blockers)}</Typography.Text>
              : <Typography.Text type="secondary">全部门禁已通过</Typography.Text>,
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
                  title: stepLabel[step.key] || step.label,
                  status: stepStatus(step),
                  description: step.key === 'replay'
                    ? `命中 ${String(step.detail.matched_change_count ?? '-')}，碰撞 ${String(step.detail.collision_count ?? '-')}`
                    : undefined,
                }))}
              />
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Space wrap size={[8, 4]}>
                  <Typography.Text strong>LLM 数据边界</Typography.Text>
                  <Tag color={row.context_gate?.success ? 'green' : 'red'}>
                    {row.context_gate?.success ? '隐私门禁已通过' : '隐私门禁未通过'}
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
                  生成输入仅包含白名单结构化字段；上下文摘要 {shortDigest(row.context_gate?.context_digest || row.context_digest)}，
                  绑定 {row.context_gate?.replay_run_id_count ?? '-'} 个巡检 Run。
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
                {(row.replay_gate?.actual_run_ids || row.replay.scope?.run_ids || []).length > 0 && (
                  <Typography.Text type="secondary" copyable>
                    巡检 Run：{compactRunIds(row.replay_gate?.actual_run_ids || row.replay.scope?.run_ids)}
                  </Typography.Text>
                )}
                {(row.replay.scope?.missing_run_ids || []).length > 0 && (
                  <Typography.Text type="warning" copyable>
                    无 Change 的基线 Run：{compactRunIds(row.replay.scope?.missing_run_ids)}
                  </Typography.Text>
                )}
                {(row.offline_job_ids || (row.offline_job_id ? [row.offline_job_id] : [])).length > 0 && (
                  <Typography.Text type="secondary" copyable>
                    离线任务：{(row.offline_job_ids || [row.offline_job_id!]).join('、')}
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
    </Space>
  )
}

export default RuleAssetBundleWorkspace
