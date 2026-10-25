import React, { useMemo } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Space,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import type { DeviceEvidenceTimelineItem, DeviceExecutionPlanRecord } from '@netops/services/api'
import { statusColor as mapStatusColor, statusLabel as mapStatusLabel } from '@netops/components/ui/statusDictionary'

type EvidenceNavigateKind =
  | 'workflow-run'
  | 'snapshot'
  | 'parser-template'
  | 'observation-contract'
  | 'change-policy'
  | 'event-rule'
  | 'change'

interface EvidenceTimelineProps {
  items: DeviceEvidenceTimelineItem[]
  executionPlans: DeviceExecutionPlanRecord[]
  isMobile?: boolean
  onNavigate?: (kind: EvidenceNavigateKind, id: string) => void
}

type ChangeRecord = DeviceEvidenceTimelineItem['changes'][number] & {
  collision_reason?: string | null
}

type EvidenceReasonGroup = {
  policyBlocking: string[]
  noEvent: string[]
  collision: string[]
  unknown: string[]
}

const statusLabel = (value?: string | null) => mapStatusLabel(value)
const statusColor = (value?: string | null) => mapStatusColor(value)

const severityColor = (value?: string | null) => {
  if (value === 'critical') return 'red'
  if (value === 'warning') return 'orange'
  return 'blue'
}

const eventSummary = (event: DeviceEvidenceTimelineItem['events'][number]) => {
  const entity = event.entity_key || '-'
  if (event.event_type === 'InterfaceDown') return `接口 ${entity} 已 Down`
  if (event.event_type === 'InterfaceUp') return `接口 ${entity} 已 Up`
  if (event.event_type === 'OpticalRxPowerLow') return `接口 ${entity} Rx功率偏低`
  if (event.event_type === 'OpticalTxPowerLow') return `接口 ${entity} Tx功率偏低`
  if (event.event_type === 'OpticalRecovered') return `接口 ${entity} 光衰恢复`
  return `${event.event_type || '事件'}（${event.entity_type || '-'} / ${entity}）`
}

const isNoEvent = (value?: string | null) => {
  const status = String(value || '').toLowerCase()
  return status.includes('no_event') || status.includes('not_created') || status.includes('skipped')
}

const isCollision = (value?: string | null) => {
  const status = String(value || '').toLowerCase()
  return status.includes('collision')
}

const uniq = (values: Array<string | undefined | null>) => {
  const seen = new Set<string>()
  const result: string[] = []
  values.forEach((value) => {
    const normalized = String(value || '').trim()
    if (!normalized || seen.has(normalized)) return
    seen.add(normalized)
    result.push(normalized)
  })
  return result
}

const hashReason = (value: string, seed = 2166136261) => {
  let hash = seed
  const text = value.trim().toLowerCase()
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24)
  }
  return Math.abs(hash).toString(36)
}

const normalizeReasons = (values: Array<string | undefined | null>) => (
  uniq(values
    .map((value) => String(value || '').trim())
    .filter((value) => value && value.toLowerCase() !== 'unknown'))
)

const deriveClosureReasons = (item: DeviceEvidenceTimelineItem): EvidenceReasonGroup => {
  const policyBlocking = uniq([
    item.policy_blocking_reason,
    ...(item.policy_blocking_reasons || []),
  ])

  const changes = item.changes as ChangeRecord[]
  const noEvent = normalizeReasons(changes
    .filter((change) => isNoEvent(change.event_status))
    .map((change) => change.no_event_reason || change.decision_reason))
  const collision = normalizeReasons(changes
    .filter((change) => isCollision(change.event_status))
    .map((change) => (change.collision_reason || change.no_event_reason || change.decision_reason)))

  const unknown = [
    ...(item.no_event_count && (!noEvent.length || item.no_event_count > noEvent.length)
      ? ['存在未报事件但未输出具体原因']
      : []),
    ...(item.policy_blocking_count && !policyBlocking.length
      ? ['存在策略阻塞但未输出具体原因']
      : []),
  ]

  return {
    policyBlocking,
    noEvent,
    collision,
    unknown: uniq(unknown),
  }
}

const renderValue = (value: unknown, numeric?: number | null, unit?: string | null) => {
  const resolved = numeric ?? value
  if (resolved === null || resolved === undefined || resolved === '') return '-'
  const text = typeof resolved === 'object' ? JSON.stringify(resolved) : String(resolved)
  return unit ? `${text} ${unit}` : text
}

const thresholdSummary = (snapshot?: Record<string, unknown> | null) => {
  if (!snapshot) return null
  const thresholds = snapshot.thresholds
  if (thresholds && typeof thresholds === 'object') {
    const entries = Object.entries(thresholds as Record<string, unknown>)
      .filter(([, value]) => value !== null && value !== undefined)
    if (entries.length) return entries.map(([key, value]) => `${key}=${String(value)}`).join(', ')
  }
  const entries = ['threshold', 'recovery_threshold', 'deadband']
    .filter((key) => snapshot[key] !== null && snapshot[key] !== undefined)
    .map((key) => `${key}=${String(snapshot[key])}`)
  return entries.length ? entries.join(', ') : null
}

const renderLinkedId = (
  value?: string | null,
  kind?: EvidenceNavigateKind,
  onNavigate?: (kind: EvidenceNavigateKind, id: string) => void,
  testId?: string,
) => {
  if (!value) return <Typography.Text type="secondary">-</Typography.Text>
  return (
    <Space size={4} align="center" wrap>
      <Typography.Text copyable type="secondary" code>{value}</Typography.Text>
      {onNavigate && kind && (
        <Tooltip title={`跳转到${kind}`}>
          <Button
            type="text"
            size="small"
            icon={<LinkOutlined />}
            aria-label={`跳转到${kind}`}
            onClick={() => onNavigate(kind, value)}
            data-testid={testId ? `${testId}-${value}` : undefined}
          />
        </Tooltip>
      )}
    </Space>
  )
}

const snapshotColumns = (onNavigate?: (kind: EvidenceNavigateKind, id: string) => void) => [
  { title: '快照', dataIndex: 'snapshot_id', width: 220, render: (value: string) => renderLinkedId(value, 'snapshot', onNavigate, 'evidence-snapshot-id') },
  { title: '命令', dataIndex: 'command', width: 260 },
  {
    title: '解析状态',
    dataIndex: 'parse_status',
    width: 130,
    render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag>,
  },
  {
    title: '执行状态',
    dataIndex: 'execution_status',
    width: 110,
    render: (value: string | null) => <Tag color={statusColor(value)}>{value || '-'}</Tag>,
  },
  {
    title: '结构化状态',
    dataIndex: 'fact_status',
    width: 110,
    render: (value: string | null) => <Tag color={statusColor(value)}>{value || '-'}</Tag>,
  },
  {
    title: '归一化状态',
    dataIndex: 'normalization_status',
    width: 110,
    render: (value: string | null) => <Tag color={statusColor(value)}>{value || '-'}</Tag>,
  },
  {
    title: 'Parser 模板',
    dataIndex: 'parser_template_id',
    width: 260,
    render: (value: string | null) => renderLinkedId(value, 'parser-template', onNavigate, 'evidence-parser-template-id'),
  },
  {
    title: 'ObservationContract',
    dataIndex: 'observation_contract_id',
    width: 270,
    render: (value: string | null) => renderLinkedId(value, 'observation-contract', onNavigate, 'evidence-observation-contract-id'),
  },
  { title: 'Raw Hash', dataIndex: 'raw_text_hash', width: 130, render: (value: string) => value ? <Typography.Text code>{value.slice(0, 12)}</Typography.Text> : '-' },
]

const observationColumns = (onNavigate?: (kind: EvidenceNavigateKind, id: string) => void) => [
  { title: '对象', width: 280, render: (_: unknown, row: DeviceEvidenceTimelineItem['observations'][number]) => `${row.entity_type}/${row.entity_key}` },
  { title: '类型', dataIndex: 'observation_type', width: 210, render: (value: string) => value || '-' },
  { title: '字段', dataIndex: 'field', width: 150 },
  { title: '值', width: 140, render: (_: unknown, row: DeviceEvidenceTimelineItem['observations'][number]) => renderValue(row.value, row.value_number, row.unit) },
  {
    title: 'Contract',
    dataIndex: 'observation_contract_id',
    width: 260,
    render: (value: string | null) => renderLinkedId(value, 'observation-contract', onNavigate, 'evidence-observation-contract-id'),
  },
]

const changeColumns = (onNavigate?: (kind: EvidenceNavigateKind, id: string) => void) => [
  { title: '对象', width: 280, render: (_: unknown, row: DeviceEvidenceTimelineItem['changes'][number]) => `${row.entity_type}/${row.entity_key}` },
  { title: '字段', dataIndex: 'field', width: 150 },
  { title: '规则类型', dataIndex: 'observation_type', width: 180, render: (value: string | null) => value || '-' },
  {
    title: '变化',
    width: 240,
    render: (_: unknown, row: DeviceEvidenceTimelineItem['changes'][number]) => `${renderValue(row.old_value, row.old_number)} → ${renderValue(row.new_value, row.new_number)}`,
  },
  { title: '事件状态', dataIndex: 'event_status', width: 150, render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
  {
    title: '原因',
    width: 240,
    render: (_: unknown, row: DeviceEvidenceTimelineItem['changes'][number]) => {
      const change = row as ChangeRecord
      if (isCollision(change.event_status)) return change.collision_reason || change.no_event_reason || change.decision_reason || '-'
      if (isNoEvent(change.event_status)) return change.no_event_reason || change.decision_reason || '-'
      return change.decision_reason || '-'
    },
  },
  {
    title: 'ChangePolicy',
    width: 240,
    render: (_: unknown, row: DeviceEvidenceTimelineItem['changes'][number]) => {
      const focusValue = row.change_policy_version_id || row.change_policy_id
      return (
        <Space direction="vertical" size={0}>
          {renderLinkedId(focusValue, 'change-policy', onNavigate, 'evidence-change-policy-id')}
          {row.change_policy_id && row.change_policy_id !== row.change_policy_version_id && (
            <Typography.Text type="secondary">policy {row.change_policy_id}</Typography.Text>
          )}
        </Space>
      )
    },
  },
]

const eventColumns = (onNavigate?: (kind: EvidenceNavigateKind, id: string) => void) => [
  { title: '对象', render: (_: unknown, row: DeviceEvidenceTimelineItem['events'][number]) => `${row.entity_type}/${row.entity_key}` },
  { title: '类型', dataIndex: 'event_type', width: 170 },
  { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
  { title: '严重性', dataIndex: 'severity', width: 100, render: (value: string) => <Tag color={severityColor(value)}>{value || '-'}</Tag> },
  { title: '来源 Change', dataIndex: 'source_change_id', width: 180, render: (value: string | null) => renderLinkedId(value, 'change', onNavigate, 'evidence-source-change-id') },
  {
    title: '规则',
    dataIndex: 'event_rule_id',
    width: 220,
    render: (_: unknown, row: DeviceEvidenceTimelineItem['events'][number]) => renderLinkedId(
      row.event_rule_id || null,
      'event-rule',
      onNavigate,
      'evidence-event-rule-id',
    ),
  },
  { title: '触发次数', dataIndex: 'occurrence_count', width: 100, render: (value: number | null) => (value ?? 0) },
  { title: '事件ID', dataIndex: 'event_id', width: 180, render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text> },
]

const ACTIVE_EXECUTION_STATUSES = new Set([
  'pending',
  'queued',
  'running',
  'in_progress',
  'collecting',
  'dispatching',
])

const RunEvidence: React.FC<{ item: DeviceEvidenceTimelineItem; isMobile?: boolean; onNavigate?: EvidenceTimelineProps['onNavigate'] }> = ({
  item,
  isMobile,
  onNavigate,
}) => {
  const noEventCount = item.no_event_count ?? item.changes.filter((change) => isNoEvent(change.event_status)).length
  const collisionCount = item.changes.filter((change) => isCollision(change.event_status)).length
  const reasons = deriveClosureReasons(item)
  const parseEntries = Object.entries(item.parse_counts || {}).filter(([, count]) => Number(count) > 0)
  const closureEntries: string[] = []
  if (item.policy_blocking_count) closureEntries.push(`策略阻塞 ${item.policy_blocking_count}`)
  if (noEventCount) closureEntries.push(`未报事件 ${noEventCount}`)
  if (collisionCount) closureEntries.push(`碰撞阻塞 ${collisionCount}`)
  if (!closureEntries.length) closureEntries.push('事件链条可被完整观察')
  const executionStatus = String((item.execution_status || item.workflow_status) || '').toLowerCase()
  const executionActive = ACTIVE_EXECUTION_STATUSES.has(executionStatus)
  const executionCancelled = executionStatus === 'cancelled'
  const executionFailed = executionStatus === 'failed'
  const executionCompleted = executionStatus === 'completed'
  const requiresAttention = Boolean(
    executionFailed || item.event_count || item.policy_blocking_count || noEventCount || collisionCount,
  )
  const outcomeTitle = executionActive
    ? '巡检进行中，最终结果尚未形成'
    : executionCancelled
      ? '巡检已取消，结果可能不完整'
      : executionFailed
        ? '巡检执行失败'
        : executionCompleted && item.event_count
          ? `发现 ${item.event_count} 个巡检事件`
          : executionCompleted && (item.policy_blocking_count || noEventCount || collisionCount)
            ? '巡检完成，部分结果需要关注'
            : executionCompleted
              ? '巡检完成，未发现事件'
              : '巡检状态尚未收敛，最终结果不可用'
  const outcomeType = executionFailed
    ? 'error'
    : executionCancelled || (executionCompleted && requiresAttention)
      ? 'warning'
      : executionCompleted
        ? 'success'
        : 'info'
  const outcomeDescription = executionActive
    ? '设备命令采集、解析或事件判定仍在执行，请等待任务进入终态后再判断巡检结果。'
    : executionCancelled
      ? '任务未执行完全部步骤，当前数据仅供排错，不代表完整巡检结论。'
      : executionFailed
        ? item.execution_error || item.steps.find((step) => step.error)?.error || '执行过程发生错误，请查看处理过程。'
        : executionCompleted
          ? closureEntries.join('；')
          : '当前状态不是可交付终态，请查看执行过程确认调度状态。'

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type={outcomeType}
        showIcon
        message={outcomeTitle}
        description={outcomeDescription}
      />
      <Descriptions size="small" bordered column={isMobile ? 2 : 5}>
        <Descriptions.Item label="执行状态">{statusLabel((item.execution_status || item.workflow_status))}</Descriptions.Item>
        <Descriptions.Item label="命令输出">{item.snapshot_count}</Descriptions.Item>
        <Descriptions.Item label="结构化结果">{item.observation_count}</Descriptions.Item>
        <Descriptions.Item label="状态变化">{item.change_count}</Descriptions.Item>
        <Descriptions.Item label="巡检事件">{item.event_count}</Descriptions.Item>
      </Descriptions>
      {item.events.length > 0 && (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>发现的问题</Typography.Text>
          {item.events.map((event) => (
            <Alert
              key={`outcome-${event.event_id}`}
              type={event.severity === 'critical' ? 'error' : event.severity === 'warning' ? 'warning' : 'info'}
              showIcon
              message={eventSummary(event)}
              description={(
                <Space wrap>
                  <Tag color={severityColor(event.severity)}>{event.severity || '-'}</Tag>
                  <Typography.Text>
                    当前值：{renderValue(
                      event.new_value,
                      event.new_number,
                      event.event_type.startsWith('Optical') ? 'dBm' : null,
                    )}
                  </Typography.Text>
                  <Typography.Text>阈值：{thresholdSummary(event.policy_snapshot) || '-'}</Typography.Text>
                  <Typography.Text type="secondary">{event.occurred_at || '-'}</Typography.Text>
                </Space>
              )}
            />
          ))}
        </Space>
      )}
      {!item.events.length && (noEventCount > 0 || item.policy_blocking_count) && (
        <Alert
          type="warning"
          showIcon
          message="检测到状态变化，但未形成巡检事件"
          description={closureEntries.join('；')}
        />
      )}
      <Collapse
        items={[{
          key: 'processing-evidence',
          label: '处理过程与技术证据',
          children: (
            <Timeline
              className="evidence-timeline"
              items={[
        {
          color: statusColor((item.execution_status || item.workflow_status)),
          children: (
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Typography.Text strong>巡检执行</Typography.Text>
              <Space wrap>
                <Tag color={statusColor((item.execution_status || item.workflow_status))}>{statusLabel((item.execution_status || item.workflow_status))}</Tag>
                <Typography.Text type="secondary">开始 {(item.execution_started_at || item.workflow_created_at) || '-'}</Typography.Text>
                <Typography.Text type="secondary">完成 {(item.execution_completed_at || item.workflow_completed_at) || '-'}</Typography.Text>
              </Space>
              {Boolean(item.execution_steps?.length) && (
                <Space wrap>
                  {item.execution_steps?.map((step) => (
                    <Tag key={step.phase} color={statusColor(step.status)}>
                      {step.label}：{statusLabel(step.status)}
                    </Tag>
                  ))}
                </Space>
              )}
              <Space wrap>
                {item.task_id && <Typography.Text copyable code>任务 {item.task_id}</Typography.Text>}
                {item.workflow_run_id && renderLinkedId(
                  item.workflow_run_id,
                  'workflow-run',
                  onNavigate,
                  'evidence-workflow-run-id',
                )}
                {item.evidence_fingerprint && (
                  <Typography.Text copyable type="secondary" code data-testid="evidence-fingerprint">
                    证据指纹 {item.evidence_fingerprint}
                  </Typography.Text>
                )}
              </Space>
            </Space>
          ),
        },
        {
          color: (item.policy_blocking_count || noEventCount || collisionCount) ? 'red' : 'blue',
          children: (
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Typography.Text strong>结果判定</Typography.Text>
              <Alert
                type={(item.policy_blocking_count || noEventCount || collisionCount) ? 'warning' : 'success'}
                showIcon
                message={closureEntries.join(' / ')}
                data-testid="evidence-closure-summary"
              />
              <Space direction="vertical" size={4}>
                {reasons.policyBlocking.map((reason, index) => (
                  <Typography.Text
                    key={`policy-blocking-${index}-${hashReason(reason)}`}
                    data-testid="evidence-policy-blocking"
                  >
                    策略阻塞：{reason}
                  </Typography.Text>
                ))}
                {reasons.noEvent.map((reason, index) => (
                  <Typography.Text
                    key={`no-event-${index}-${hashReason(reason)}`}
                    data-testid="evidence-no-event-reason"
                  >
                    未报事件：{reason}
                  </Typography.Text>
                ))}
                {reasons.collision.map((reason, index) => (
                  <Typography.Text
                    key={`collision-${index}-${hashReason(reason)}`}
                    data-testid="evidence-collision-reason"
                  >
                    碰撞阻塞：{reason}
                  </Typography.Text>
                ))}
                {reasons.unknown.map((reason, index) => (
                  <Typography.Text
                    key={`unknown-${index}-${hashReason(reason)}`}
                    data-testid="evidence-unknown-reason"
                    type="secondary"
                  >
                    未分类：{reason}
                  </Typography.Text>
                ))}
                {Boolean(!reasons.policyBlocking.length
                  && !reasons.noEvent.length
                  && !reasons.collision.length
                  && !reasons.unknown.length
                  && (item.policy_blocking_count || noEventCount || collisionCount)) && (
                  <Typography.Text type="secondary">
                    未见细化原因，建议补齐变更原因（策略/事件规则）并重新回放
                  </Typography.Text>
                )}
              </Space>
            </Space>
          ),
        },
        {
          color: item.execution_plan_id ? 'green' : 'gray',
          children: (
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Typography.Text strong>执行计划</Typography.Text>
              {item.execution_plan ? (
                <Descriptions size="small" column={isMobile ? 1 : 3} bordered>
                  <Descriptions.Item label="方案">{item.execution_plan.profile_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="来源">{item.execution_plan.policy_source || '-'}</Descriptions.Item>
                  <Descriptions.Item label="命令数">{item.execution_plan.command_count}</Descriptions.Item>
                </Descriptions>
              ) : <Typography.Text type="secondary">当前无可见执行计划</Typography.Text>}
            </Space>
          ),
        },
        {
          color: item.snapshot_count > 0 ? 'green' : 'gray',
          children: (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text strong>命令采集与解析</Typography.Text>
                <Tag>{item.snapshot_count} 条</Tag>
                {parseEntries.map(([status, count]) => (
                  <Tag key={`${status}-${count}`} color={statusColor(status)}>{statusLabel(status)} {count}</Tag>
                ))}
              </Space>
              <Table
                rowKey="snapshot_id"
                size="small"
                pagination={false}
                dataSource={item.snapshots || []}
                locale={{ emptyText: '暂无执行快照' }}
                columns={snapshotColumns(onNavigate)}
                scroll={{ x: 1600 }}
              />
            </Space>
          ),
        },
        {
          color: item.observation_count > 0 ? 'green' : 'gray',
          children: (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text strong>结构化巡检结果</Typography.Text>
                <Tag color={item.observation_count > 0 ? 'green' : 'default'}>{item.observation_count} 条</Tag>
                {item.observation_count === 0 && <Typography.Text type="secondary">未拿到可用结构化观测</Typography.Text>}
              </Space>
              {item.observations?.length > 0 && (
                <Table
                  rowKey="observation_id"
                  size="small"
                  pagination={false}
                  dataSource={item.observations}
                  columns={observationColumns(onNavigate)}
                  scroll={{ x: 1100 }}
                />
              )}
            </Space>
          ),
        },
        {
          color: item.change_count > 0 ? 'orange' : 'gray',
          children: (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text strong>状态变化与事件判定</Typography.Text>
                <Tag color={item.change_count > 0 ? 'orange' : 'default'}>{item.change_count} 条</Tag>
                {noEventCount > 0 && <Tag color="orange">未报事件 {noEventCount}</Tag>}
                {Boolean(item.policy_blocking_count && item.policy_blocking_count > 0) && (
                  <Tag color="red">策略阻塞 {item.policy_blocking_count}</Tag>
                )}
                {collisionCount > 0 && <Tag color="red">碰撞阻塞 {collisionCount}</Tag>}
              </Space>
              <Table
                rowKey="change_id"
                size="small"
                pagination={false}
                dataSource={item.changes || []}
                locale={{ emptyText: '暂无变化记录' }}
                columns={changeColumns(onNavigate)}
                scroll={{ x: 1500 }}
              />
            </Space>
          ),
        },
        {
          color: item.event_count > 0 ? 'red' : 'gray',
          children: (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text strong>巡检事件</Typography.Text>
                <Tag color={item.event_count > 0 ? 'red' : 'default'}>{item.event_count} 条</Tag>
                <Tag color={item.event_occurrence_count ? 'blue' : 'default'}>
                  {item.event_occurrence_count || 0} 次触发
                </Tag>
              </Space>
              {item.events.length > 0 ? item.events.map((event) => (
                <Alert
                  key={event.event_id}
                  type={event.severity === 'critical' ? 'error' : event.severity === 'warning' ? 'warning' : 'info'}
                  showIcon
                  message={eventSummary(event)}
                  description={(
                    <Space wrap>
                      <Tag color={severityColor(event.severity)}>{event.severity || '-'}</Tag>
                      <Tag>{statusLabel(event.status)}</Tag>
                      <Typography.Text>
                        当前值：{renderValue(
                          event.new_value,
                          event.new_number,
                          event.event_type.startsWith('Optical') ? 'dBm' : null,
                        )}
                      </Typography.Text>
                      <Typography.Text>
                        阈值：{thresholdSummary(event.policy_snapshot) || '-'}
                      </Typography.Text>
                      {event.change_policy_id && (
                        <Tag color="blue">策略 {event.change_policy_id}{event.change_policy_version_id ? ` v${event.change_policy_version_id}` : ''}</Tag>
                      )}
                      {event.event_rule_id && (
                        <Tag color="blue">规则 {event.event_rule_id}{event.event_rule_version ? ` v${event.event_rule_version}` : ''}</Tag>
                      )}
                      <Typography.Text type="secondary">{event.occurred_at || '-'}</Typography.Text>
                      <Typography.Text copyable code>{event.event_id}</Typography.Text>
                    </Space>
                  )}
                />
              )) : <Typography.Text type="secondary">当前未生成任何 Event</Typography.Text>}
              {item.events.length > 0 && (
                <Table
                  rowKey="event_id"
                  size="small"
                  pagination={false}
                  dataSource={item.events}
                  locale={{ emptyText: '暂无 Event 详情' }}
                  columns={eventColumns(onNavigate)}
                  scroll={{ x: 1020 }}
                />
              )}
            </Space>
          ),
        },
              ]}
            />
          ),
        }]}
      />
    </Space>
  )
}

const EvidenceTimeline: React.FC<EvidenceTimelineProps> = ({ items, executionPlans, isMobile, onNavigate }) => {
  const panels = useMemo(() => items.map((item) => ({
    key: item.workflow_run_id || item.snapshot_run_id || `evidence-${(item.execution_started_at || item.workflow_created_at)}`,
    label: (
      <Space wrap>
        <Typography.Text strong>{(item.execution_started_at || item.workflow_created_at) || '巡检运行'}</Typography.Text>
        <Tag color={statusColor((item.execution_status || item.workflow_status))}>{statusLabel((item.execution_status || item.workflow_status))}</Tag>
        <Tag>{item.snapshot_count} 快照</Tag>
        <Tag color={(item.no_event_count || item.changes.filter((change) => isNoEvent(change.event_status)).length) > 0 ? 'orange' : 'default'}>
          {(item.no_event_count ?? item.changes.filter((change) => isNoEvent(change.event_status)).length)} 未生成事件
        </Tag>
        <Tag color={item.change_count ? 'orange' : 'default'}>{item.change_count} 变化</Tag>
        <Tag color={item.event_count ? 'red' : 'default'}>{item.event_count} 事件</Tag>
        {item.event_occurrence_count ? <Tag color="blue">{item.event_occurrence_count} 次发生</Tag> : null}
      </Space>
    ),
    children: <RunEvidence item={item} isMobile={isMobile} onNavigate={onNavigate} />,
  })), [isMobile, items, onNavigate])

  if (!items.length && !executionPlans.length) {
    return <Empty description="无证据记录" />
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="这里说明设备巡检执行了什么，以及为什么得到当前结果"
        description="按巡检执行、命令采集与解析、结构化结果、状态变化和巡检事件的顺序展示；某一步缺少数据时会直接说明。"
      />
      {panels.length > 0 && (
        <Collapse
          items={panels}
          defaultActiveKey={[panels[0].key]}
          destroyOnHidden
          expandIconPosition="end"
        />
      )}
      {executionPlans.length > 0 && (
        <Collapse
          ghost
          items={[{
            key: 'technical-plans',
            label: `技术执行方案（${executionPlans.length}）`,
            children: (
              <Table<DeviceExecutionPlanRecord>
                rowKey="execution_plan_id"
                size="small"
                pagination={{ pageSize: 5 }}
                dataSource={executionPlans}
                columns={[
                  { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
                  { title: '方案', dataIndex: 'profile_name', width: 180 },
                  { title: '来源', dataIndex: 'policy_source', width: 150 },
                  { title: '命令数', dataIndex: 'command_count', width: 90 },
                  {
                    title: '方案 ID',
                    dataIndex: 'execution_plan_id',
                    render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text>,
                  },
                ]}
                scroll={{ x: 760 }}
              />
            ),
          }]}
        />
      )}
    </Space>
  )
}

export default EvidenceTimeline
