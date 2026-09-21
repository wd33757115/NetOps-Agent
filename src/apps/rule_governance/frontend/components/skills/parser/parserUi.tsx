import React from 'react'
import { Tag } from 'antd'

export const statusColor = (status: string) => {
  if (status === 'active' || status === 'succeeded') return 'green'
  if (status === 'failed' || status === 'validation_failed' || status === 'rejected') return 'red'
  if (status === 'deprecated' || status === 'cancelled' || status === 'superseded') return 'default'
  return 'blue'
}

export const statusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '已激活',
    cancelled: '已取消',
    collecting: '收集中',
    deprecated: '已废弃',
    draft: '草稿',
    failed: '失败',
    pending: '待处理',
    pending_approval: '待审批',
    rejected: '已拒绝',
    running: '运行中',
    succeeded: '成功',
    superseded: '已替换',
    unavailable: '不可用',
    validating: '验证中',
  }
  return status ? labels[status] || status : '无'
}

export const governanceStatusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '已激活',
    deprecated: '已废弃',
    draft: '草稿',
    pending_approval: '待审批',
    rejected: '已拒绝',
    validating: '验证中',
    validation_failed: '验证失败',
    committing: '提交中',
    completed: '已完成',
  }
  return status ? labels[status] || status : '-'
}

export const codePanelStyle: React.CSSProperties = {
  background: '#f7f8fa',
  border: '1px solid #e5e7eb',
  borderRadius: 4,
  fontFamily: 'Consolas, Monaco, monospace',
  margin: 0,
  overflow: 'auto',
  padding: 14,
  whiteSpace: 'pre',
}

export const successTag = (success: unknown, okText = '通过', failText = '未通过') => {
  if (success === true) return <Tag color="green">{okText}</Tag>
  if (success === false) return <Tag color="red">{failText}</Tag>
  return <Tag>无报告</Tag>
}

export const shortDigest = (value?: string | null) => (value ? value.slice(0, 12) : '-')

export const governanceAlertLabel = (code: string, fallback: string) => {
  const labels: Record<string, string> = {
    parser_backlog_high: '生产 Parser 待办积压已超过阈值',
    parser_candidate_stale: '生产 Parser 候选已超过生成时限',
    parser_approval_stale: '生成的 Parser 模板等待审批时间过长',
    parser_generation_failed: '过去 24 小时存在生产 Parser 生成失败',
    template_activation_publish_failed: '模板激活事件投递正在重试',
    blocked_by_parser_ratio_high: '过去 24 小时因 Parser 阻断的巡检比例超过阈值',
    rule_generation_failed: '过去 24 小时存在规则资产生成或校验失败',
    rule_replay_collision: '规则历史回放发现碰撞，审批发布已阻断',
  }
  return labels[code] || fallback
}

export const queryErrorLabel = (error: unknown) => {
  const candidate = error as {
    message?: string
    status?: number
    response?: { status?: number; data?: { detail?: string } }
  } | null
  const status = candidate?.response?.status ?? candidate?.status
  if (status === 401 || status === 403) return '权限不足'
  if (status === 404) return '接口不存在'
  if (status && status >= 500) return '服务暂不可用'
  return candidate?.response?.data?.detail || candidate?.message || '网络连接失败'
}
