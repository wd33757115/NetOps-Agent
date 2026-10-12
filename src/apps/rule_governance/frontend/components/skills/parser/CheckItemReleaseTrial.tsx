import React, { useState } from 'react'
import { Alert, Button, Typography } from 'antd'
import type { CheckItemReleaseTrial as Trial } from '@netops/services/api'

type TrialItem = NonNullable<Trial['added']>[number]

const SHOWN = 5

const severityLabel: Record<string, string> = {
  info: '提示', warning: '警告', minor: '次要', major: '重要', critical: '严重',
}

const unavailable: Record<string, string> = {
  new_check_item: '这是新检查项，还没有巡检数据。发布后的第一次巡检才有结果。',
  not_structured: '这个检查项不按字段解析，没有可以重新判断的值。',
  no_run: '还没有巡检采集过这个检查项的值，无法试算。',
}

const entityLabel = (key: string) => {
  try {
    const parsed: unknown = JSON.parse(key)
    return Array.isArray(parsed) ? parsed.join('，') : key
  } catch {
    return key
  }
}

const valueLabel = (value: unknown) => (value === null || value === undefined || value === '' ? '空' : String(value))

const when = (iso?: string | null) => {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const two = (value: number) => String(value).padStart(2, '0')
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${two(date.getHours())}:${two(date.getMinutes())} 的巡检，`
}

/** What every listed item has in common, stated only when it is true of all of them. */
const inCommon = (items: TrialItem[], total: number) => {
  if (items.length < 2 || items.length < total) return ''
  const same = (pick: (item: TrialItem) => string) => new Set(items.map(pick)).size === 1
  const facts = [
    same((item) => `${item.field}=${valueLabel(item.value)}`) ? `全部是 ${items[0].field} = ${valueLabel(items[0].value)}` : '',
    same((item) => item.device_id) ? `都在 ${items[0].device_id} 上` : '',
  ].filter(Boolean)
  return facts.length ? `${facts.join('，')}。` : ''
}

const Tile: React.FC<{ label: string; now: number; after: number; note: string; attention?: boolean }> = ({
  label, now, after, note, attention,
}) => (
  <div className={`release-trial__tile${attention ? ' release-trial__tile--attention' : ''}`}>
    <span className="release-trial__tile-label">{label}</span>
    <span className="release-trial__tile-figure">
      {now === after
        ? <><b>{now}</b> 项，发布后不变</>
        : <><b>{now}</b> 项，发布后 <b>{after}</b> 项</>}
    </span>
    <span className="release-trial__tile-note">{note}</span>
  </div>
)

const Items: React.FC<{ title: string; items: TrialItem[]; total: number }> = ({ title, items, total }) => {
  const [all, setAll] = useState(false)
  if (!total) return null
  const shown = all ? items : items.slice(0, SHOWN)
  const common = inCommon(items, total)
  return (
    <div className="release-trial__group">
      <Typography.Text strong>{title}</Typography.Text>
      {common && <Typography.Text type="secondary" className="release-trial__common">{common}</Typography.Text>}
      <ul className="release-trial__items" aria-label={title}>
        {shown.map((item) => (
          <li key={`${item.device_id}|${item.entity_key}|${item.field}`}>
            <span className="release-trial__object">
              <Typography.Text>{entityLabel(item.entity_key)}</Typography.Text>
              <Typography.Text type="secondary">{item.device_id}</Typography.Text>
            </span>
            <Typography.Text type="secondary">
              {item.field} = {valueLabel(item.value)}，{severityLabel[item.severity] || item.severity}
            </Typography.Text>
          </li>
        ))}
      </ul>
      {items.length > SHOWN && (
        <Button size="small" onClick={() => setAll(!all)}>
          {all ? '收起' : `查看全部 ${items.length} 项`}
        </Button>
      )}
      {total > items.length && (
        <Typography.Text type="secondary" className="release-trial__common">
          只列出前 {items.length} 项，共 {total} 项。
        </Typography.Text>
      )}
    </div>
  )
}

/** The last patrol's values judged again under the version about to be published. */
const CheckItemReleaseTrial: React.FC<{ trial?: Trial }> = ({ trial }) => {
  if (!trial) return null
  if (!trial.available) {
    if (trial.reason === 'error') {
      return <Alert type="warning" showIcon message="试算没有完成，不影响发布" description={trial.detail} />
    }
    return (
      <section className="release-trial">
        <Typography.Text strong>按上次巡检的数据试算</Typography.Text>
        <Typography.Text type="secondary" className="release-trial__lead">
          {unavailable[trial.reason || 'no_run'] || unavailable.no_run}
        </Typography.Text>
      </section>
    )
  }
  const now = trial.now || { abnormal: 0, undetermined: 0 }
  const after = trial.after || { abnormal: 0, undetermined: 0 }
  const added = trial.added_count || 0
  const removed = trial.removed_count || 0
  const moved = (trial.rules || []).filter((rule) => rule.now !== rule.after)
  const steady = (trial.rules || []).length - moved.length
  const missing = trial.no_data_fields || []

  return (
    <section className="release-trial" aria-label="按上次巡检的数据试算">
      <div className="release-trial__head">
        <Typography.Text strong>按上次巡检的数据试算</Typography.Text>
        {trial.run && (
          <Typography.Text type="secondary">
            {when(trial.run.observed_at)}{trial.run.devices} 台设备，{trial.run.values} 个值
          </Typography.Text>
        )}
      </div>
      <Typography.Text type="secondary" className="release-trial__lead">
        用上次采集到的值，按新版本重新判断一遍。不重新采集，不改任何数据。
      </Typography.Text>

      <div className="release-trial__tiles">
        <Tile
          label="异常"
          now={now.abnormal}
          after={after.abnormal}
          attention={added + removed > 0}
          note={added + removed > 0 ? `多报 ${added} 项，少报 ${removed} 项` : '上次巡检报的异常一项不多、一项不少'}
        />
        <Tile
          label="无法判定"
          now={now.undetermined}
          after={after.undetermined}
          note="缺少阈值或预期状态依据的值"
        />
      </div>

      {moved.length > 0 && (
        <div className="release-trial__group">
          <Typography.Text strong>变化来自哪条判断</Typography.Text>
          <ul className="release-trial__rules" aria-label="变化来自哪条判断">
            {moved.map((rule) => (
              <li key={`${rule.field}|${rule.event_type}`}>
                <Typography.Text strong>{rule.field}</Typography.Text>
                <Typography.Text type="secondary">
                  {rule.text}
                  {rule.change === 'added' && <span className="release-trial__flag release-trial__flag--added">新增</span>}
                  {rule.change === 'removed' && <span className="release-trial__flag">停用</span>}
                </Typography.Text>
                <Typography.Text className="release-trial__count">{rule.now} 变为 {rule.after}</Typography.Text>
              </li>
            ))}
          </ul>
          {steady > 0 && <Typography.Text type="secondary">其余 {steady} 条判断的结果不变。</Typography.Text>}
        </div>
      )}

      <Items title={`多报的 ${added} 项`} items={trial.added || []} total={added} />
      <Items title={`少报的 ${removed} 项`} items={trial.removed || []} total={removed} />

      {missing.length > 0 && (
        <Typography.Text type="secondary">
          上次巡检没有采集到 {missing.join('、')} 的值，这部分无法试算。
        </Typography.Text>
      )}
      <Typography.Text type="secondary">两次巡检之间的变化事件不在这次试算里。</Typography.Text>
    </section>
  )
}

export default CheckItemReleaseTrial
