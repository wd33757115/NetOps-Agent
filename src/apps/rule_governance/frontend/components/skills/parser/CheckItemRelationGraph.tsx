import React, { useState } from 'react'
import { Popover, Tooltip, Typography } from 'antd'
import { useQuery } from 'react-query'
import type { CheckItemEventTally, CheckItemRelations } from '@netops/services/api'
import { parserApi } from '@netops/services/api'
import { queryErrorLabel } from './parserUi'

type Row = CheckItemRelations['rows'][number]
type Outcome = Row['outcomes'][number]
type Change = NonNullable<Outcome['change']>

// One outcome is one node; the wires are drawn from these two numbers.
const NODE = 46
const GAP = 6

const severityLabel: Record<string, string> = {
  info: '提示', warning: '警告', minor: '次要', major: '重要', critical: '严重',
}
const flagLabel: Record<Change, string> = { same: '', added: '新增', changed: '变更', removed: '停用' }

const groupOf = (value: CheckItemRelations['reference']) => (
  Array.isArray(value) ? { fields: value, added: [], removed: [] } : value
)

const someOf = (names: string[], limit = 4) => names.length > limit
  ? `${names.slice(0, limit).join('、')} 等 ${names.length} 个`
  : names.join('、')

/** An event type is prefixed with its check item; inside that check item the prefix says nothing. */
const shortEvent = (eventType: string, checkItemId: string) => (
  eventType.startsWith(`${checkItemId}.`) ? eventType.slice(checkItemId.length + 1) : eventType
)

const describeEvent = (event: Pick<Outcome, 'event_class' | 'severity' | 'judges_current' | 'resolves'>, checkItemId: string) => {
  const severity = severityLabel[event.severity || ''] || event.severity || ''
  if (event.event_class === 'recovery') {
    return event.resolves?.length
      ? `恢复事件，关闭 ${event.resolves.map((item) => shortEvent(item, checkItemId)).join('、')}`
      : '恢复事件'
  }
  if (event.event_class === 'change') return event.severity === 'info' ? '提示事件' : `变化事件，${severity}`
  return `故障事件，${severity}${event.judges_current ? '，也计入当前异常' : ''}`
}

const entityLabel = (key: string) => {
  try {
    const parsed: unknown = JSON.parse(key)
    return Array.isArray(parsed) ? parsed.join(' / ') : key
  } catch {
    return key
  }
}

const valueLabel = (value: unknown) => (value === null || value === undefined || value === '' ? '空' : String(value))

/** The latest stored events of one type, opened from its count. */
const EventPeek: React.FC<{ checkItemId: string; eventType: string; children: React.ReactElement }> = ({
  checkItemId, eventType, children,
}) => {
  const [open, setOpen] = useState(false)
  const query = useQuery(
    ['check-item-events', checkItemId, eventType],
    () => parserApi.checkItemEvents(checkItemId, eventType),
    { enabled: open },
  )
  const content = query.isError
    ? <Typography.Text type="danger">{queryErrorLabel(query.error)}</Typography.Text>
    : !query.data
      ? <Typography.Text type="secondary">正在读取…</Typography.Text>
      : (
        <div className="relation-events">
          {query.data.items.map((item) => (
            <div key={item.id} className="relation-events__item">
              <Typography.Text strong>{item.device_id}</Typography.Text>
              <Typography.Text>{entityLabel(item.entity_key)}</Typography.Text>
              <Typography.Text type="secondary">
                {item.old !== undefined || item.new !== undefined
                  ? `${valueLabel(item.old)} 变为 ${valueLabel(item.new)}，`
                  : ''}
                {item.occurrence_count > 1 ? `共 ${item.occurrence_count} 次，` : ''}
                {item.last_seen_at ? new Date(item.last_seen_at).toLocaleString('zh-CN', { hour12: false }) : ''}
                {item.status === 'resolved' ? `，${item.closed_note ? `已关闭（${item.closed_note}）` : '已恢复'}` : ''}
              </Typography.Text>
            </div>
          ))}
          {query.data.total > query.data.items.length && (
            <Typography.Text type="secondary">只列出最近 {query.data.items.length} 条，共 {query.data.total} 条。</Typography.Text>
          )}
        </div>
      )
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="leftTop"
      title={shortEvent(eventType, checkItemId)}
      content={content}
    >
      {children}
    </Popover>
  )
}

const Flag: React.FC<{ change?: Change; label?: string }> = ({ change, label }) => (
  change && change !== 'same'
    ? <span className={`relation-node__flag relation-node__flag--${change}`}>{label || flagLabel[change]}</span>
    : null
)

const nodeClass = (kind: string, change?: Change) => (
  `relation-node relation-node--${kind}${change && change !== 'same' ? ` relation-node--${change}` : ''}`
)

const OutcomeNode: React.FC<{ outcome: Outcome; checkItemId: string; diff: boolean; draft?: boolean }> = ({
  outcome, checkItemId, diff, draft,
}) => {
  if (outcome.kind !== 'event') {
    return (
      <div className={nodeClass('result', outcome.change)}>
        <span className="relation-node__title relation-node__title--plain">
          {outcome.text}
          <Flag change={outcome.change} />
        </span>
      </div>
    )
  }
  const eventType = outcome.event_type || ''
  const fault = outcome.event_class === 'fault'
  const number = fault ? outcome.open || 0 : outcome.total || 0
  const closing = diff && outcome.change === 'removed' && Boolean(outcome.open)
  // A draft has produced nothing yet, and a comparison only counts what is on record.
  const counted = !draft && outcome.event_class !== 'recovery' && (number > 0 || !diff)
  const tip = outcome.before_text ? `${outcome.text}（原为：${outcome.before_text}）` : outcome.text
  return (
    <div className={nodeClass('event', outcome.change)}>
      <Tooltip title={tip} placement="topLeft">
        <div className="relation-node__text">
          <span className="relation-node__title">
            {shortEvent(eventType, checkItemId)}
            <Flag change={outcome.change} />
          </span>
          <span className="relation-node__meta">
            {closing ? `发布时关闭 ${outcome.open} 条` : describeEvent(outcome, checkItemId)}
            {!diff && outcome.state === 'missing' && <span className="relation-node__state relation-node__state--missing">，未生效</span>}
          </span>
        </div>
      </Tooltip>
      {counted && !closing && (
        number > 0 ? (
          <EventPeek checkItemId={checkItemId} eventType={eventType}>
            <button
              type="button"
              className={`relation-count${fault ? ' relation-count--open' : ''}`}
              aria-label={`${shortEvent(eventType, checkItemId)} ${fault ? '未恢复' : '已记录'} ${number} 条，查看`}
            >
              <span className="relation-count__number">{number}</span>
              <span className="relation-count__label">{fault ? '未恢复' : '已记录'}</span>
            </button>
          </EventPeek>
        ) : (
          <span className="relation-count relation-count--none">
            <span className="relation-count__number">0</span>
            <span className="relation-count__label">{fault ? '未恢复' : '已记录'}</span>
          </span>
        )
      )}
    </div>
  )
}

const wireClass = (change?: Change) => (
  `relation-wire${change === 'added' ? ' relation-wire--added' : change === 'removed' ? ' relation-wire--removed' : ''}`
)

const RelationRow: React.FC<{ row: Row; checkItemId: string; diff: boolean; patrolled: boolean }> = ({
  row, checkItemId, diff, patrolled,
}) => {
  const count = row.outcomes.length
  const height = count * NODE + (count - 1) * GAP
  const middle = height / 2
  const { rule, field } = row
  const ruleTip = [
    ...rule.lines,
    ...(rule.before ? [`原为：${rule.before.title}，${rule.before.detail}`] : []),
  ].join(' ')
  return (
    <div className="relation-row" role="listitem" aria-label={`${field.name}：${rule.title}，${rule.detail}`}>
      <svg className="relation-row__wires" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" aria-hidden="true">
        <path className={wireClass(rule.change)} d={`M28 ${middle} H34`} />
        {row.outcomes.map((outcome, index) => {
          const y = index * (NODE + GAP) + NODE / 2
          return (
            <path
              key={outcome.key}
              className={wireClass(outcome.change)}
              d={`M60 ${middle} C63 ${middle} 63 ${y} 66 ${y}`}
            />
          )
        })}
      </svg>
      <div className={nodeClass('field', field.change)}>
        <span className="relation-node__title">
          {field.name}
          <Flag change={field.change} label={field.change === 'removed' ? '删除' : undefined} />
        </span>
        {field.description && <span className="relation-node__meta" title={field.description}>{field.description}</span>}
      </div>
      <Tooltip title={ruleTip || undefined} placement="topLeft">
        <div className={nodeClass('rule', rule.change)}>
          <span className="relation-node__title">
            {rule.title}
            <Flag change={rule.change} />
          </span>
          <span className="relation-node__meta">
            {rule.detail}
            {rule.before && `，原为${rule.before.title}`}
            {!diff && patrolled && rule.state && (
              <span className={`relation-node__state relation-node__state--${rule.state}`}>
                {rule.state === 'active' ? '生效' : '未生效'}
              </span>
            )}
          </span>
        </div>
      </Tooltip>
      <div className="relation-row__outcomes">
        {row.outcomes.map((outcome) => (
          <OutcomeNode key={outcome.key} outcome={outcome} checkItemId={checkItemId} diff={diff} draft={!patrolled} />
        ))}
      </div>
    </div>
  )
}

const RetiredRow: React.FC<{ event: CheckItemEventTally; checkItemId: string }> = ({ event, checkItemId }) => (
  <div className="relation-row" role="listitem" aria-label={`${event.field || event.event_type}：规则已停用`}>
    <svg className="relation-row__wires" viewBox={`0 0 100 ${NODE}`} preserveAspectRatio="none" aria-hidden="true">
      <path className="relation-wire relation-wire--removed" d={`M28 ${NODE / 2} H34`} />
      <path className="relation-wire relation-wire--removed" d={`M60 ${NODE / 2} H66`} />
    </svg>
    <div className="relation-node relation-node--field relation-node--removed">
      <span className="relation-node__title">{event.field || '-'}</span>
    </div>
    <div className="relation-node relation-node--rule relation-node--removed">
      <span className="relation-node__title">规则已停用</span>
      <span className="relation-node__meta">不再产生事件</span>
    </div>
    <div className="relation-row__outcomes">
      <div className="relation-node relation-node--event relation-node--removed">
        <div className="relation-node__text">
          <span className="relation-node__title">{shortEvent(event.event_type, checkItemId)}</span>
          <span className="relation-node__meta">{describeEvent(event, checkItemId)}</span>
        </div>
        <EventPeek checkItemId={checkItemId} eventType={event.event_type}>
          <button
            type="button"
            className="relation-count relation-count--open"
            aria-label={`${shortEvent(event.event_type, checkItemId)} 未关闭 ${event.open} 条，查看`}
          >
            <span className="relation-count__number">{event.open}</span>
            <span className="relation-count__label">未关闭</span>
          </button>
        </EventPeek>
      </div>
    </div>
  </div>
)

const GroupRow: React.FC<{
  label: string
  group: { fields: string[]; added: string[]; removed: string[] }
  children: React.ReactNode
}> = ({ label, group, children }) => (
  group.fields.length + group.removed.length === 0 ? null : (
    <div className="relation-row relation-row--group" role="listitem" aria-label={`${label} ${group.fields.length} 个字段`}>
      <div className="relation-node relation-node--field relation-node--group" title={group.fields.join('、')}>
        <span className="relation-node__title">{label}，{group.fields.length} 个字段</span>
        <span className="relation-node__meta">{someOf(group.fields)}</span>
      </div>
      <div className="relation-row__note">
        {children}
        {group.added.length > 0 && <span className="relation-row__delta">新增 {group.added.join('、')}。</span>}
        {group.removed.length > 0 && <span className="relation-row__delta">删除 {group.removed.join('、')}。</span>}
      </div>
    </div>
  )
)

interface CheckItemRelationGraphProps {
  checkItemId: string
  graph: CheckItemRelations
  /** Comparing two versions: leave out the rows where nothing differs. */
  onlyChanges?: boolean
  /** Shown under the rows of rules that no longer run. */
  retiredAction?: React.ReactNode
}

/** A check item as wiring: each field, the judgment it gets, and the events and results that follow. */
const CheckItemRelationGraph: React.FC<CheckItemRelationGraphProps> = ({ checkItemId, graph, onlyChanges, retiredAction }) => {
  const diff = graph.mode === 'diff'
  const differs = (row: Row) => (
    (row.field.change || 'same') !== 'same'
    || (row.rule.change || 'same') !== 'same'
    || row.outcomes.some((outcome) => (outcome.change || 'same') !== 'same')
  )
  const rows = onlyChanges ? graph.rows.filter(differs) : graph.rows
  const hidden = graph.rows.length - rows.length
  const reference = groupOf(graph.reference)
  const evidence = groupOf(graph.evidence)
  const retired = graph.retired || []
  const users = graph.rows.filter((row) => row.rule.kind === 'health').map((row) => row.field.name)
  const quietChange = evidence.added.length + evidence.removed.length + reference.added.length + reference.removed.length > 0

  if (onlyChanges && rows.length === 0 && !quietChange) return null
  return (
    <div className="relation-graph__scroll">
      <div className="relation-graph" role="list" aria-label="字段、规则与事件的关系">
        {diff && (
          <div className="relation-graph__legend" aria-hidden="true">
            <span className="relation-legend relation-legend--added">新增</span>
            <span className="relation-legend relation-legend--changed">变更</span>
            <span className="relation-legend relation-legend--removed">停用</span>
            <span className="relation-legend">不变</span>
          </div>
        )}
        {rows.length > 0 && (
          <div className="relation-graph__head" aria-hidden="true">
            <span>字段</span>
            <span>判断</span>
            <span>事件与结果</span>
          </div>
        )}
        {rows.map((row) => (
          <RelationRow
            key={row.field.name}
            row={row}
            checkItemId={checkItemId}
            diff={diff}
            patrolled={graph.patrolled !== false && graph.mode !== 'draft'}
          />
        ))}
        {!diff && graph.rows.length === 0 && evidence.fields.length + reference.fields.length > 0 && (
          <Typography.Text type="secondary">没有字段需要判断，所以不产生规则和事件。</Typography.Text>
        )}
        {hidden > 0 && (
          <Typography.Text type="secondary" className="relation-graph__rest">
            其余 {hidden} 个字段的规则和事件不变。
          </Typography.Text>
        )}
        {(!onlyChanges || reference.added.length + reference.removed.length > 0) && (
          <GroupRow label="阈值参考" group={reference}>
            {users.length ? `供 ${someOf(users)} 判断时作阈值，本身不产生规则。` : '作阈值使用，本身不产生规则。'}
          </GroupRow>
        )}
        {(!onlyChanges || evidence.added.length + evidence.removed.length > 0) && (
          <GroupRow label="仅展示" group={evidence}>只进报告，不判断，不产生规则和事件。</GroupRow>
        )}
        {retired.length > 0 && (
          <>
            <div className="relation-graph__divider">已停用的规则留下的事件</div>
            {retired.map((event) => <RetiredRow key={event.event_type} event={event} checkItemId={checkItemId} />)}
            {retiredAction}
          </>
        )}
      </div>
    </div>
  )
}

export default CheckItemRelationGraph
