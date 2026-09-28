import React from 'react'
import { CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons'
import './StatusNotice.css'

interface StatusNoticeProps {
  type?: 'info' | 'success' | 'warning' | 'error'
  message: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  showIcon?: boolean
  style?: React.CSSProperties
  className?: string
}

/** Routine status stays quiet; errors retain an explicit icon and alert semantics. */
export default function StatusNotice({ type = 'info', message, description, action, showIcon = true, style, className = '' }: StatusNoticeProps) {
  const Icon = { info: InfoCircleOutlined, success: CheckCircleOutlined, warning: ExclamationCircleOutlined, error: CloseCircleOutlined }[type]
  return <div className={`netops-status-notice ${className}`} data-tone={type} style={style} role={type === 'error' ? 'alert' : 'status'}>
    {showIcon && <Icon className="netops-status-notice-icon" aria-hidden="true" />}
    <div className="netops-status-notice-content">
      <div className="netops-status-notice-title">{message}</div>
      {description && <div className="netops-status-notice-description">{description}</div>}
    </div>
    {action && <div className="netops-status-notice-action">{action}</div>}
  </div>
}
