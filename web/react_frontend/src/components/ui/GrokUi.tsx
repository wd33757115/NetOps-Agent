// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'

type ChipTone = 'default' | 'ok' | 'warn'

export function GrokChip({
  children,
  tone = 'default',
  className = '',
}: {
  children: React.ReactNode
  tone?: ChipTone
  className?: string
}) {
  return (
    <span
      className={`grok-chip${tone !== 'default' ? ` is-${tone}` : ''}${className ? ` ${className}` : ''}`}
    >
      {children}
    </span>
  )
}

interface GrokToolBtnProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: React.ReactNode
  primary?: boolean
  loading?: boolean
}

export function GrokToolBtn({
  icon,
  children,
  primary,
  loading = false,
  className = '',
  disabled,
  ...rest
}: GrokToolBtnProps) {
  return (
    <button
      type="button"
      className={`grok-tool-btn${primary ? ' is-primary' : ''}${className ? ` ${className}` : ''}`}
      disabled={disabled || loading}
      aria-busy={loading}
      {...rest}
    >
      {icon ? <span className="grok-tool-btn-icon">{icon}</span> : null}
      {children ? <span>{children}</span> : null}
    </button>
  )
}

interface GrokRowActionProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: React.ReactNode
  danger?: boolean
}

export function GrokRowAction({ icon, children, danger, className = '', ...rest }: GrokRowActionProps) {
  return (
    <button
      type="button"
      className={`grok-row-action${danger ? ' is-danger' : ''}${className ? ` ${className}` : ''}`}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
}

export function GrokInfoBar({ children }: { children: React.ReactNode }) {
  return <div className="grok-info-bar">{children}</div>
}

export function statusChipTone(status: string): ChipTone {
  if (status === 'ok' || status === 'healthy' || status === 'success') return 'ok'
  if (status === 'degraded' || status === 'warning' || status === 'down' || status === 'unhealthy') return 'warn'
  return 'default'
}
