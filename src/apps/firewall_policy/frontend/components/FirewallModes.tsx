import React from 'react'
import { Link } from 'react-router-dom'

export const ROOT = '/apps/firewall-policy'

/** The app's two jobs; each keeps its own history and workflow. */
export default function FirewallModes({ current }: { current: 'policy' | 'block' }) {
  const modes = [{ key: 'policy', label: '策略生成', to: ROOT }, { key: 'block', label: 'IP 封禁', to: `${ROOT}/block` }]
  return <nav className="fw-modes" aria-label="防火墙功能">
    {modes.map(mode => <Link key={mode.key} to={mode.to} className={current === mode.key ? 'active' : ''}
      aria-current={current === mode.key ? 'page' : undefined}>{mode.label}</Link>)}
  </nav>
}
