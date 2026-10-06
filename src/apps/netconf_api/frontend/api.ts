import { api } from '@netops/config/api'

const BASE = '/apps/netconf-api'

export interface DefinitionSummary {
  id: string
  check_item: string | null
  vendor: string | null
  platforms: string[]
  description: string
  module: string | null
  published_version: number | null
  has_draft: boolean
  revision: number
}

export interface VersionView {
  version: number
  sha256: string
  check_item: string | null
  published_by: string | null
  published_at: string | null
  note: string
}

export type Body = Record<string, unknown>

export interface ContractValue { name: string; value_type: string | null; required: boolean; identity: boolean }

export interface DefinitionDetail extends DefinitionSummary {
  draft: Body | null
  draft_by: string | null
  draft_at: string | null
  published: (VersionView & { body: Body }) | null
  versions: VersionView[]
}

export interface Module { name: string; revision: string | null; namespace: string }

export interface CapabilityView {
  device_id: string
  status: 'available' | 'unavailable'
  failure_code: string | null
  checked_at: string | null
  capability_count: number
  module_count: number
  definitions: string[]
  modules?: Module[]
  unavailable?: { id: string; module: string }[]
}

export interface Device {
  device_id: string
  device_name: string | null
  ip: string | null
  vendor: string | null
  platform: string | null
  model: string | null
  capability: CapabilityView | null
}

export interface Problem {
  row: number
  key: string
  value: string
  kind: string
  raw: string | null
  table: string
}

export interface TrialView {
  id: string
  device_id: string
  definition_id: string
  version: number | null
  body_sha256: string
  status: 'succeeded' | 'failed'
  failure_code: string | null
  message: string | null
  read_count: number | null
  row_count: number | null
  problem_count: number | null
  reply_bytes: number | null
  started_at: string | null
  finished_at: string | null
  rows?: Record<string, string | number>[]
  problems?: Problem[]
  filters?: { definition: string; filter: string }[]
}

/** A refused request: the backend's own words, and for a publish the coverage gaps. */
export class Refusal extends Error {
  status: number
  gaps: string[]
  constructor(status: number, message: string, gaps: string[] = []) {
    super(message)
    this.status = status
    this.gaps = gaps
  }
}

export const FORBIDDEN = '没有使用 NETCONF 接口应用的权限，请联系管理员开通。'

function refusal(status: number, data: unknown): Refusal {
  if (status === 403) return new Refusal(403, FORBIDDEN)
  const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const { message, gaps } = detail as { message?: unknown; gaps?: unknown }
    return new Refusal(status, typeof message === 'string' ? message : JSON.stringify(detail),
      Array.isArray(gaps) ? gaps.map(String) : [])
  }
  if (Array.isArray(detail)) {
    // FastAPI request validation: [{loc, msg}]
    return new Refusal(status, detail.map(item => (item as { msg?: string }).msg || JSON.stringify(item)).join('；'))
  }
  return new Refusal(status, typeof detail === 'string' && detail ? detail : `请求失败（${status}）`)
}

/** Any error as a Refusal: an ApiError the shared client raised keeps its status. */
export function asRefusal(error: unknown): Refusal {
  if (error instanceof Refusal) return error
  const status = (error as { status?: number; response?: { status?: number } })?.status
    ?? (error as { response?: { status?: number } })?.response?.status ?? 0
  if (status === 403) return new Refusal(403, FORBIDDEN)
  return new Refusal(status, error instanceof Error && error.message ? error.message : '请求失败，请稍后重试')
}

// Requests whose 4xx answer the page shows itself (validation, conflicts, coverage).
async function send<T>(method: 'get' | 'post' | 'put', url: string, body?: unknown): Promise<T> {
  const config = { validateStatus: (status: number) => status < 500 }
  try {
    const response = method === 'get'
      ? await api.get<T>(`${BASE}${url}`, config)
      : await api[method]<T>(`${BASE}${url}`, body, config)
    const status = response?.status ?? 200
    if (status >= 400) throw refusal(status, response.data)
    return response.data
  } catch (error) {
    throw asRefusal(error)
  }
}

export const listDefinitions = () => send<DefinitionSummary[]>('get', '/definitions')
export const readDefinition = (id: string) => send<DefinitionDetail>('get', `/definitions/${encodeURIComponent(id)}`)
export const readVersion = (id: string, version: number) =>
  send<VersionView & { body: Body }>('get', `/definitions/${encodeURIComponent(id)}/versions/${version}`)
export const readContract = (checkItem: string, vendor: string, platform: string) =>
  send<{ check_item: string; vendor: string; platform: string; values: ContractValue[] }>('get',
    `/check-items/${encodeURIComponent(checkItem)}/contract?vendor=${encodeURIComponent(vendor)}&platform=${encodeURIComponent(platform)}`)
export const saveDraft = (id: string, body: Body, revision: number) =>
  send<{ id: string; revision: number }>('put', `/definitions/${encodeURIComponent(id)}/draft`, { body, revision })
export const discardDraft = (id: string, revision: number) =>
  send<{ id: string; revision: number }>('post', `/definitions/${encodeURIComponent(id)}/discard`, { revision })
export const publishDraft = (id: string, revision: number, note: string) =>
  send<VersionView & { id: string; revision: number }>('post', `/definitions/${encodeURIComponent(id)}/publish`, { revision, note })
export interface BrowseResult {
  status: 'succeeded' | 'failed'
  filter: string
  datastore?: 'state' | 'running'
  data?: string
  reply_bytes: number
  elapsed_ms: number
  failure_code?: string
  message?: string
}

export const listDevices = () => send<Device[]>('get', '/devices')
export const browse = (deviceId: string, namespace: string, path: string[], datastore: 'state' | 'running') =>
  send<BrowseResult>('post', `/devices/${encodeURIComponent(deviceId)}/browse`, { namespace, path, datastore })
export const discover = (deviceId: string) =>
  send<CapabilityView>('post', `/devices/${encodeURIComponent(deviceId)}/capabilities`)
export const runTrial = (deviceId: string, definitionId: string, draft: boolean) =>
  send<TrialView>('post', '/trials', { device_id: deviceId, definition_id: definitionId, draft })
export const listTrials = (definitionId?: string) =>
  send<TrialView[]>('get', `/trials?limit=50${definitionId ? `&definition_id=${encodeURIComponent(definitionId)}` : ''}`)
export const readTrial = (id: string) => send<TrialView>('get', `/trials/${encodeURIComponent(id)}`)

// Device access failure codes (platform contracts) in the words the page shows.
// A code not listed here is shown as it is.
export const FAILURE_TEXT: Record<string, string> = {
  authentication_failed: '登录失败（用户名或凭据不对）',
  connection_failed: '连接失败（地址不通或设备未开 NETCONF）',
  connection_closed: '会话被设备关闭',
  host_key_rejected: '830 端口主机密钥与记录不一致，需在 SSH 终端确认',
  credential_unavailable: '凭据不可用',
  credential_expired: '凭据已过期',
  not_authorized: '平台未授权访问该设备',
  policy_denied: '访问策略拒绝',
  timeout: '等待回包超时',
  output_limit: '回包超过大小上限',
  netconf_rpc_error: '设备返回 RPC 错误',
  reply_unreadable: '设备回包无法解析',
  definition_invalid: '定义不可用',
}

export const failureText = (code: string | null) => code ? `${FAILURE_TEXT[code] || '采集失败'}（${code}）` : ''

export const PROBLEM_TEXT: Record<string, string> = {
  unmapped_code: '未映射的代码',
  lookup_miss: '关联未命中',
  lookup_ambiguous: '关联不唯一',
  lookup_unexpected: '关联到的行不符合预期',
  type_mismatch: '类型不符',
  unexpected_value: '行不符合预期（未输出）',
  required_missing: '必填值缺失（未输出）',
}
