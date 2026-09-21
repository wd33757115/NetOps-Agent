import { api } from '@netops/config/api'

export interface PolicyRule {
  id: string
  device: string
  source_zone: string
  destination_zone: string
  protocol: string
  sources: string[]
  destinations: string[]
  ports: string[]
  evidence_ids: string[]
}
export interface PolicyEvidence {
  id: string
  row: number
  path: string[][]
  path_source: string
  source_segments: { addresses: string[]; prefix: string; owner: string[] }[]
  destination_segments: { addresses: string[]; prefix: string; owner: string[] }[]
}
export interface PolicyPlan {
  include_business_paths?: boolean
  ticket_id: string
  input_name: string
  plan_id: string
  rules: PolicyRule[]
  commands: Record<string, string>
  warnings: string[]
  requests: { row: number; src_ip: string; dst_ip: string; port: string; proto: string; source_system?: string; destination_system?: string }[]
  evidence: PolicyEvidence[]
  optimization: { status: string; before: number; after: number; reason?: string }
}
export interface PolicyResult {
  run_id: string
  success: boolean
  message?: string
  error?: string
  policy_plan: PolicyPlan
  manifest?: Record<string, unknown>
  artifacts: Record<string, { download_url?: string; filename: string }>
}

export interface FirewallRun {
  id: string
  kind: 'policy' | 'ticket'
  parent_id?: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  error?: string
  input: { ticket_id?: string; fields?: ChangeTicketFields }
  result?: Omit<PolicyResult, 'run_id' | 'artifacts'>
  artifacts: PolicyResult['artifacts']
  tickets?: FirewallRun[]
  created_at: string
}

const base = '/apps/firewall-policy'
export const activeRun = (run?: FirewallRun) => run?.status === 'queued' || run?.status === 'running'
export const runResult = (run: FirewallRun): PolicyResult | undefined => run.status === 'completed' && run.result?.policy_plan
  ? { ...run.result, run_id: run.id, artifacts: run.artifacts } : undefined
export async function readRun(id: string) { return (await api.get<FirewallRun>(`${base}/runs/${id}`)).data }
export async function listRuns() { return (await api.get<{ items: FirewallRun[]; total: number }>(`${base}/runs`)).data }
export async function cancelRun(id: string) { return (await api.post<FirewallRun>(`${base}/runs/${id}/cancel`)).data }
export async function retryRun(id: string, requestId: string) {
  return (await api.post<FirewallRun>(`${base}/runs/${id}/retry`, { request_id: requestId })).data
}

export function validatePolicyFile(file: File, topology = false) {
  if (!file.name.toLowerCase().endsWith(topology ? '.json' : '.xlsx')) {
    throw new Error(topology ? '请选择 JSON 拓扑文件。' : '请选择 .xlsx 格式的策略申请表。')
  }
  if (!file.size || file.size > 10 * 1024 * 1024) throw new Error('文件应大于 0 字节，且不超过 10 MB。')
}

const uploadRequests = new WeakMap<File, string>()
async function upload(file: File, kind: string) {
  let requestId = uploadRequests.get(file)
  if (!requestId) { requestId = crypto.randomUUID(); uploadRequests.set(file, requestId) }
  const { data } = await api.post<{ id: string }>(`${base}/uploads`, file, {
    params: { filename: file.name, kind, request_id: requestId }, headers: { 'Content-Type': 'application/octet-stream' },
  })
  return data.id
}
const submissions = new Map<string, string>()
export async function generateFirewallPolicy(
  input: { file: File; topology?: File; ticket: string; merge: boolean; businessPaths?: boolean },
  onStage: (stage: 'uploading' | 'generating') => void,
): Promise<FirewallRun> {
  validatePolicyFile(input.file)
  if (input.topology) validatePolicyFile(input.topology, true)
  onStage('uploading')
  const policy = await upload(input.file, 'policy')
  const topology = input.topology ? await upload(input.topology, 'topology') : null
  onStage('generating')
  const body = { policy_upload_id: policy, topology_upload_id: topology, ticket_id: input.ticket,
    merge_policies: input.merge, include_business_paths: input.businessPaths === true }
  const key = JSON.stringify(body)
  const requestId = submissions.get(key) || crypto.randomUUID()
  submissions.set(key, requestId)
  const { data } = await api.post<FirewallRun>(`${base}/runs`, { ...body, request_id: requestId })
  submissions.delete(key)
  return data
}

export function artifactHref(result: Pick<PolicyResult, 'artifacts'>, key: string): string | undefined {
  const url = result.artifacts?.[key]?.download_url
  return url?.startsWith('/api/apps/firewall-policy/') ? url : undefined
}

export async function downloadFile(url: string, filename: string) {
  if (!url.startsWith('/api/apps/firewall-policy/')) throw new Error('无效下载地址')
  const { data } = await api.get<Blob>(url.slice(4), { responseType: 'blob' })
  const objectUrl = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = objectUrl; link.download = filename
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}
export async function downloadArtifact(result: Pick<PolicyResult, 'artifacts'>, key: string) {
  const url = artifactHref(result, key)
  if (!url) throw new Error('产物不存在')
  await downloadFile(url, result.artifacts[key].filename)
}
export const downloadTemplate = () => downloadFile('/api/apps/firewall-policy/template', 'policies.xlsx')

export interface ChangeTicketFields {
  ticket_title: string
  assignee: string
  due_date: string
  change_background: string
  change_purpose: string
  requester: string
  requester_dept: string
  technical_reviewer: string
  reviewer: string
}

export async function generateChangeTicket(policy: PolicyResult, fields: ChangeTicketFields, requestId: string) {
  return (await api.post<FirewallRun>(`${base}/runs/${policy.run_id}/tickets`, {
    request_id: requestId, fields,
  })).data
}
