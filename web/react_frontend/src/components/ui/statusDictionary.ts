export const statusLabelMap: Record<string, string> = {
  completed: '完成',
  failed: '失败',
  running: '运行中',
  pending: '待处理',
  queued: '排队中',
  parsed: '已解析',
  template_missing: '缺失模板',
  parse_failed: '解析失败',
  ignored: '忽略',
  not_applicable: '不适用',
  event_created: '事件已生成',
  no_event: '未生成事件',
  active: '启用',
  blocked: '阻塞',
  event_generated: '事件已生成',
  processing: '处理中',
  created: '创建',
  updated: '更新',
  skipped: '已跳过',
  succeeded: '成功',
  validation_failed: '校验失败',
}

export const statusDescriptionMap: Record<string, string> = {
  completed: '流程已完成',
  failed: '处理失败',
  running: '执行进行中',
  pending: '等待执行',
  queued: '等待队列',
  parsed: '已解析成功',
  template_missing: '缺少模板',
  parse_failed: '解析失败',
  ignored: '已忽略',
  not_applicable: '不适用',
  event_created: '已生成事件',
  no_event: '未生成事件',
  active: '已启用',
  blocked: '被策略阻塞',
  processing: '处理中',
  created: '已创建',
  updated: '已更新',
  skipped: '已跳过',
  succeeded: '执行成功',
  validation_failed: '校验失败',
}

export const statusDescription = (value?: string | null) => {
  return statusDescriptionMap[String(value || '').toLowerCase()] || '状态待确认'
}

export const statusColor = (value?: string | null) => {
  const status = String(value || '').toLowerCase()
  if (['completed', 'succeeded', 'parsed', 'event_created', 'event_generated', 'active', 'created'].includes(status)) return 'green'
  if (['failed', 'parse_failed', 'template_missing', 'validation_failed', 'blocked', 'rejected'].includes(status)) return 'red'
  if (['running', 'pending', 'queued', 'in_progress', 'processing'].includes(status)) return 'blue'
  if (status.includes('no_event') || status.includes('not_created') || status.includes('skipped')) return 'orange'
  return 'default'
}

export const statusLabel = (value?: string | null) => statusLabelMap[String(value || '').toLowerCase()] || value || '-'
