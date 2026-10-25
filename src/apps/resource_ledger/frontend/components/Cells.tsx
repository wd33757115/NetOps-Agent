import React from 'react'
import type { Level, PrefixRow, Usage, ZoneRef } from '../api'

export function State({ value }: { value: string }) {
  return <span className={`rl-state s-${value}`}>{value}</span>
}

/** One quiet tint per planning level, so a column of chips reads as the hierarchy. */
export function LevelChip({ level, root }: { level: Level | null; root?: boolean }) {
  if (!level) return <span className="rl-lv lv0">{root ? '地址资源' : '未分层'}</span>
  return <span className={`rl-lv lv${Math.min(level.position, 6)}`}>{level.title}</span>
}

/** What a block is for: the object it binds (or its name), with its purpose underneath. */
export function OwnerCell({ row }: { row: Pick<PrefixRow, 'title' | 'purpose' | 'ticket'> }) {
  const note = row.purpose && row.purpose !== row.title ? row.purpose : null
  return <div className="rl-owner">
    {row.title ? <strong>{row.title}</strong> : <span className="rl-muted">未命名</span>}
    {(note || row.ticket) && <small>{[note, row.ticket && `工单 ${row.ticket}`].filter(Boolean).join('，')}</small>}
  </div>
}

/** Leaf prefixes show addresses in use; summaries show how much is planned below them. */
export function UsageCell({ usage }: { usage: Usage }) {
  const hot = usage.percent >= 80
  return <div className="rl-use" title={usage.kind === 'planned' ? '已划分出去的比例' : '已分配、保留和网关地址占可用地址的比例'}>
    <div className={`rl-bar ${hot ? 'hot' : ''}`}><i style={{ width: `${Math.min(100, usage.percent)}%` }} /></div>
    <span className={hot ? 'hot' : ''}>{usage.percent}%</span>
    <small>{usage.kind === 'planned' ? '已划分' : `${usage.used}/${usage.total}`}</small>
  </div>
}

export function ZoneCell({ zone }: { zone: ZoneRef | null }) {
  if (!zone) return <span className="rl-muted">未划到网络区域</span>
  return <div style={{ whiteSpace: 'nowrap' }}>
    <span>{zone.name}{zone.title ? ` ${zone.title}` : ''}</span>
    <small className="rl-sub">{[zone.region, zone.site].filter(Boolean).join(' ')}</small>
  </div>
}

/** Inherited bindings as a breadcrumb: 西安 › 西安机房 › region2. */
export function PathLine({ path, skipLast }: { path: PrefixRow['path']; skipLast?: boolean }) {
  const steps = skipLast ? path.slice(0, -1) : path
  if (!steps.length) return <span className="rl-muted">没有上级归属</span>
  return <span className="rl-inherit-path">{steps.map((step, index) => <React.Fragment key={step.prefix_id}>
    {index > 0 && <i aria-hidden="true">›</i>}<span title={step.level}>{step.name}</span>
  </React.Fragment>)}</span>
}
