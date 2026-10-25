import React from 'react'
import { Link } from 'react-router-dom'
import { ROOT } from '../Layout'
import type { Hop } from '../api'

const short = (provider?: string) => (provider || '').replace(/^中国/, '')

/** A path as hops: each circuit, then the site or provider network it reaches; struck-out circuits are the failed ones. */
export function RouteLine({ hops }: { hops: Hop[] }) {
  return <div className="rl-route">{hops.map((hop, index) => <span key={index} className={`rl-route-node ${index === hops.length - 1 ? 'last' : ''}`}>
    {index > 0 && <i aria-hidden="true">→</i>}
    {hop.kind === 'circuit'
      ? <Link to={`${ROOT}/circuits/${hop.id}`} className={`rl-hop ${hop.hit ? 'hit' : ''}`} title={hop.hit ? '这条线受故障影响' : undefined}>
          {short(hop.provider)} {hop.cid}</Link>
      : hop.kind === 'network' ? <span className="rl-hop net">{hop.name}</span> : <span className="rl-route-site">{hop.name}</span>}
  </span>)}</div>
}

export const CIRCUIT_STATE_CLASS: Record<string, string> = {
  在用: 's-使用中', 施工中: 's-规划', 规划: 's-规划', 离线: 's-保留', 拆除中: 's-待回收', 已拆除: 's-待回收',
}
