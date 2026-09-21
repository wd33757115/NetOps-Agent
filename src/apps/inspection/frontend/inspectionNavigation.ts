export const inspectionViews = [
  { key: 'tasks', label: '全部任务', title: '巡检任务', description: '汇总在线与离线巡检，从执行进度追踪到结果与证据。' },
  { key: 'online', label: '在线巡检', title: '在线巡检', description: '从设备采集数据，核对检查项结论、覆盖范围与原始证据。' },
  { key: 'offline', label: '离线巡检', title: '离线巡检', description: '导入采集文件，完成单周期检查或双周期对比。' },
  { key: 'risks', label: '风险与整改', title: '风险与整改', description: '追踪风险事件、核查证据，持续跟进整改结果。' },
  { key: 'feedback', label: '质量反馈', title: '质量反馈', description: '记录检查项误报与遗漏，让每一次巡检都有据可查。' },
] as const

export function inspectionView(params: URLSearchParams) {
  const explicit = inspectionViews.find(view => view.key === params.get('patrol_view'))
  return explicit || inspectionViews[params.has('patrol_batch_id') ? 1 : params.has('offline_job_id') ? 2 : 0]
}
