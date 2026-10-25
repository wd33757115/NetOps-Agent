import { offlinePatrolApi, ruleAssetSourceApi } from './api'

/** Reuse frozen file references, never production conclusions or identity attestations. */
export async function reusePatrolRuleSource(patrolId: string) {
  const patrol = await offlinePatrolApi.get(patrolId)
  if (patrol.purpose !== 'offline_patrol' || patrol.status !== 'completed') throw new Error('请先完成原巡检分析')
  const comparison = patrol.job_type === 'history_compare'
  const periods = (patrol.periods || []).filter(period => comparison
    ? ['baseline', 'current'].includes(period.role) : period.role === 'current')
  const sources = (patrol.source_files || []).filter(file => periods.some(period => period.role === file.period_role))
  if (periods.length !== (comparison ? 2 : 1) || !sources.length || periods.some(period => !sources.some(source => source.period_role === period.role))) throw new Error('原任务缺少完整文件或采集周期，请补充样本')
  const created = await ruleAssetSourceApi.create({
    name: `巡检能力补齐 · ${patrol.name}`.slice(0, 200),
    job_type: comparison ? 'history_compare' : 'parser_validation',
    periods: periods.map(period => ({ role: comparison ? period.role : 'sample', label: period.label, observed_at: period.observed_at })),
    vendor: patrol.vendor || undefined, model: patrol.model || undefined,
    report_intent: 'historical', publish_events: false,
    idempotency_key: `patrol-rule-source:${patrolId}`,
  })
  const task = await ruleAssetSourceApi.get(created.id)
  for (const source of sources) {
    const role = comparison ? source.period_role : 'sample'
    if (task.source_files?.some(file => file.period_role === role && file.file_id === source.file_id && file.relative_path === source.relative_path)) continue
    if (!['draft', 'uploading', 'blocked'].includes(task.status)) throw new Error('已有制作任务进入后续阶段，请在制作任务中继续')
    await ruleAssetSourceApi.attachSource(task.id, { period_role: role, file_id: source.file_id,
      relative_path: source.relative_path, sha256: source.sha256 || undefined, size_bytes: source.size_bytes })
  }
  return task.id
}

/** A new analysis leaves the source job and its report intact, and repeats preflight. */
export async function createPatrolReanalysis(patrolId: string, requestKey: string) {
  const original = await offlinePatrolApi.get(patrolId)
  if (original.purpose !== 'offline_patrol' || original.status !== 'completed') throw new Error('只可复检已完成的巡检任务')
  const periods = (original.periods || []).filter(period => original.job_type === 'history_compare'
    ? ['baseline', 'current'].includes(period.role) : period.role === 'current')
  const sources = (original.source_files || []).filter(file => periods.some(period => period.role === file.period_role))
  if (periods.length !== (original.job_type === 'history_compare' ? 2 : 1)
    || periods.some(period => !sources.some(file => file.period_role === period.role))) throw new Error('原巡检缺少完整证据，请补充采集文件')
  const created = await offlinePatrolApi.create({
    name: `复检 · ${original.name}`.slice(0, 200), job_type: original.job_type,
    periods: periods.map(({ role, label, observed_at }) => ({ role, label, observed_at })),
    vendor: original.vendor || undefined, model: original.model || undefined,
    report_intent: 'historical', publish_events: false,
    idempotency_key: `patrol-reanalysis:${patrolId}:${requestKey}`,
  })
  const task = await offlinePatrolApi.get(created.id)
  for (const source of sources) {
    if (task.source_files?.some(file => file.period_role === source.period_role && file.file_id === source.file_id && file.relative_path === source.relative_path)) continue
    if (!['draft', 'uploading', 'blocked'].includes(task.status)) throw new Error('复检任务已进入后续阶段，请从任务列表继续')
    await offlinePatrolApi.attachSource(task.id, { period_role: source.period_role, file_id: source.file_id,
      relative_path: source.relative_path, sha256: source.sha256 || undefined, size_bytes: source.size_bytes })
  }
  return task.id
}
