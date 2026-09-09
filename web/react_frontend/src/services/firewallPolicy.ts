import { api } from '../config/api'
import { chatApi } from './api'

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
  success: boolean
  message?: string
  error?: string
  policy_plan: PolicyPlan
  manifest?: Record<string, unknown>
  artifacts: Record<string, { download_url?: string; filename: string }>
}

const readBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result).split(',')[1])
  reader.onerror = () => reject(new Error('文件读取失败，请重新选择。'))
  reader.readAsDataURL(file)
})

export function validatePolicyFile(file: File, topology = false) {
  if (!file.name.toLowerCase().endsWith(topology ? '.json' : '.xlsx')) {
    throw new Error(topology ? '请选择 JSON 拓扑文件。' : '请选择 .xlsx 格式的策略申请表。')
  }
  if (!file.size || file.size > 10 * 1024 * 1024) throw new Error('文件应大于 0 字节，且不超过 10 MB。')
}

export async function generateFirewallPolicy(
  input: { file: File; topology?: File; ticket: string; merge: boolean; businessPaths?: boolean },
  onStage: (stage: 'uploading' | 'generating') => void,
): Promise<PolicyResult> {
  validatePolicyFile(input.file)
  if (input.topology) validatePolicyFile(input.topology, true)
  onStage('uploading')
  const thread = `firewall-${crypto.randomUUID()}`
  const upload = async (file: File) => {
    const result = await chatApi.uploadFile({ thread_id: thread, filename: file.name, file_content: await readBase64(file) })
    if (result.status !== 'uploaded' || !result.file_path) throw new Error(result.message || '文件上传失败。')
    return result.file_path
  }
  const policyPath = await upload(input.file)
  const topologyPath = input.topology ? await upload(input.topology) : undefined
  onStage('generating')
  const { data } = await api.post<{ success: boolean; result: Omit<PolicyResult, 'policy_plan'> & { output?: { policy_plan?: PolicyPlan } } }>(
    '/skills/firewall-policy-generator/test-run/',
    { params: { ticket_id: input.ticket, policy_file_url: policyPath, policy_input_name: input.file.name,
      topology_file_url: topologyPath, merge_policies: input.merge, include_policy_plan: true, include_business_paths: input.businessPaths === true } },
    { timeout: 340000 },
  )
  if (!data.success || !data.result?.success) throw new Error(data.result?.error || data.result?.message || '策略生成失败，请检查申请表。')
  const plan = data.result.output?.policy_plan
  if (!plan?.rules?.length) throw new Error('未返回可核对的策略明细，请重试。')
  return { ...data.result, policy_plan: plan }
}

export function artifactHref(result: Pick<PolicyResult, 'artifacts'>, key: string): string | undefined {
  const url = result.artifacts?.[key]?.download_url
  return url && (/^https?:\/\//i.test(url) || url.startsWith('/api/')) ? url : undefined
}

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

export async function generateChangeTicket(policy: PolicyResult, fields: ChangeTicketFields) {
  if (!Array.isArray(policy.manifest?.scripts) || !policy.manifest.scripts.length) {
    throw new Error('当前结果缺少设备配置，请重新生成策略后再制作工单。')
  }
  const { data } = await api.post<{ success: boolean; result: Pick<PolicyResult, 'success' | 'error' | 'message' | 'artifacts'> }>(
    '/skills/itsm-change-ticket-writer/test-run/',
    { params: {
      ticket_id: policy.policy_plan.ticket_id,
      // Preserve this exact plan's scripts, verification and rollback. Only header fields are editable.
      manifest: { ...policy.manifest, ...fields, due_date: fields.due_date.replace('T', ' '), ticket_id: policy.policy_plan.ticket_id },
    } },
    { timeout: 340000 },
  )
  if (!data.success || !data.result?.success) throw new Error(data.result?.error || data.result?.message || '工单生成失败，请重试。')
  const href = artifactHref(data.result, 'change_excel')
  if (!href) throw new Error('工单已生成，但下载文件未能保存，请稍后重试。')
  return href
}
