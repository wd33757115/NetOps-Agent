import { api } from '@netops/config/api'

const base = '/apps/resource-ledger'

export const SITE_KINDS = ['自建', '托管', '云', '分支', '门店', '合作方', '其他'] as const
export const SPACE_KINDS = ['生产', '办公', '管理', '互联与 DMZ', '云', '其他'] as const
export const ZONE_KINDS = ['生产', '测试', '开发', '灾备', 'DMZ', '管理', '办公', '互联', '其他'] as const
export const PREFIX_STATES = ['规划', '使用中', '保留', '待回收'] as const
export const ADDRESS_KINDS = ['主机', 'VIP', '设备管理口', '专线互联', '其他'] as const
export const IMPORTANCE = ['核心', '重要', '一般'] as const
export const ENVIRONMENTS = ['生产', '灾备', '测试', '开发', '其他'] as const
export const HOST_STATES = ['规划中', '建设中', '待投产', '已投产', '待下线', '已下线'] as const
export const HOST_KINDS = ['物理机', '虚拟机', '容器节点', '其他'] as const
export const HOST_ROLES = ['Web', '应用', '数据库', '中间件', '缓存', '负载均衡', '其他'] as const
export const PROTOCOLS = ['TCP', 'UDP', 'ICMP'] as const
export const NETWORK_KINDS = ['MPLS VPN', '互联网', '其他'] as const
export const RISK_KINDS = ['光缆', '管道', '入户', '电源', '设备', '其他'] as const
export const CIRCUIT_STATES = ['规划', '施工中', '在用', '离线', '拆除中', '已拆除'] as const
export const ROUTING = ['静态', 'BGP', 'OSPF', '其他'] as const

export type Dimension = 'region' | 'site' | 'zone' | 'system' | 'subsystem' | 'custom'
export const DIMENSIONS: Record<Dimension, string> = {
  region: '地域', site: '机房', zone: '网络区域', system: '业务系统', subsystem: '子系统', custom: '自定义名称',
}
export const BINDING_FIELD: Record<Exclude<Dimension, 'custom'>, 'region_id' | 'site_id' | 'zone_id' | 'system_id' | 'subsystem_id'> = {
  region: 'region_id', site: 'site_id', zone: 'zone_id', system: 'system_id', subsystem: 'subsystem_id',
}

export interface Region { id: string; name: string; note?: string | null }
export interface Site {
  id: string; region_id: string; name: string; kind: string; address?: string | null; note?: string | null
  core?: boolean
}
export interface Space { id: string; name: string; kind: string; note?: string | null }
export interface Zone {
  id: string; site_id: string; name: string; title?: string | null; kind: string; security_level?: string | null
  note?: string | null
}
export interface Level { id: string; position: number; dimension: Dimension; title: string; default_length: number | null }
export interface SystemRef { id: string; name: string; short_name?: string | null; importance?: string }
export interface SubsystemRef { id: string; system_id: string; name: string }
export interface Overview {
  can_edit: boolean; regions: Region[]; sites: Site[]; spaces: Space[]; zones: Zone[]
  systems: SystemRef[]; subsystems: SubsystemRef[]; levels: Level[]
}

export interface Usage { kind: 'planned' | 'addresses'; used: number; total: number; percent: number }
export interface ZoneRef {
  id: string; name: string; title?: string | null; inherited: boolean; site?: string | null; region?: string | null
}
export interface PathStep { level: string; dimension: Dimension; id: string | null; name: string; prefix_id: string }
export interface Binding { region_id: string | null; site_id: string | null; zone_id: string | null; system_id: string | null; subsystem_id: string | null }
export interface PrefixRow extends Binding {
  type: 'prefix'; id: string; space_id: string; network: string; length: number; parent_id: string | null; depth: number
  level: Level | null; title: string | null; label: string | null; purpose: string | null
  /** Every binding from the top; the last one is this block's own when it has a level. */
  path: PathStep[]
  zone: ZoneRef | null; system: { id: string; name: string } | null; subsystem: { id: string; name: string } | null
  vlan: number | null; gateway: string | null; status: string
  owner: string | null; ticket: string | null; note: string | null; child_count: number; usage: Usage
  updated_at: string; updated_by: string
}
export interface GapRow {
  type: 'gap'; parent_id: string; depth: number; first: string; last: string; size: number; unit: number; blocks: number; largest: string[]
}
export type TreeRow = PrefixRow | GapRow

export interface Resource {
  id: string; network: string; title: string | null; purpose: string | null; status: string; level: Level | null
  space: { id: string; name: string }; planned: number | null; usage: number | null
}
export interface Resources { can_edit: boolean; spaces: Space[]; levels: Level[]; resources: Resource[] }
export interface Subtree {
  space: Space; ancestors: { id: string; network: string; title: string | null }[]; usage: number | null
  rows: TreeRow[]; can_edit: boolean
}
export interface CarveOptions {
  parent: PrefixRow; levels: (Level & { suggested_length: number })[]; level_id: string | null
  length: number; count: number; candidates: string[]
}

export interface HostLink { id: string; hostname: string; status: string }
export interface CircuitLink { id: string; cid: string; side: 'A' | 'Z' }
export interface AddressRow {
  id: string; ip: string; kind: string; hostname: string | null; dns_name: string | null; note: string | null
  assigned_at: string; assigned_by: string; host: HostLink | null; circuit?: CircuitLink | null
}
export interface ReservationRow { id: string; first: string; last: string; size: number; purpose: string; created_at: string; created_by: string }
export interface PrefixDetail extends PrefixRow {
  space: { id: string; name: string }
  ancestors: { id: string; network: string; title: string | null; level: string | null }[]
  can_edit: boolean
  child_rows?: TreeRow[]
  stray_addresses?: AddressRow[]
  usable?: { first: string; last: string } | null
  first_ip?: string; last_ip?: string
  addresses?: AddressRow[]; addresses_truncated?: boolean
  reservations?: ReservationRow[]
  stats?: { usable: number; allocated: number; reserved: number; free: number }
  next_free?: string | null
}
export interface ZonePageRow extends Zone {
  where: ZoneRef | null; prefixes: { id: string; network: string; space: string }[]; subnets: number; usage: number | null
}

export interface SystemBlock { id: string; network: string; space: string; where: string[]; zone: ZoneRef | null; usage: number | null }
export interface SystemRow {
  id: string; name: string; short_name: string | null; importance: string; department: string | null
  business_owner: string | null; ops_owner: string | null; contact: string | null; note: string | null
  hosts: Record<string, number>; blocks: SystemBlock[]
  subsystems: { id: string; name: string; owner: string | null; note: string | null; hosts: Record<string, number>; blocks: SystemBlock[] }[]
}
export interface ServiceRow { id: string; protocol: string; port_first: number | null; port_last: number | null; text: string; ports: string | null; name: string | null; note: string | null }
export interface HostRow {
  id: string; hostname: string; system: { id: string; name: string }; subsystem: { id: string; name: string } | null
  environment: string; status: string; kind: string; role: string; os: string | null; owner: string | null
  online_date: string | null; offline_date: string | null; tags: string | null; note: string | null
  updated_at: string; updated_by: string
  ips: { id: string; ip: string; space_id: string; space?: string; prefix?: { id: string; network: string }; zone?: ZoneRef | null }[]
  services: ServiceRow[]
}

export interface CircuitOptions {
  can_edit: boolean
  providers: { id: string; name: string }[]
  networks: { id: string; name: string; provider_id: string; kind: string }[]
  pops: { id: string; name: string; provider_id: string }[]
  types: { id: string; name: string }[]
  risks: { id: string; name: string; kind: string }[]
  sites: { id: string; name: string; kind: string; core: boolean; region: string | null }[]
}
export interface CircuitEnd {
  place: { kind: 'site' | 'network'; id: string; name: string; detail: string } | null
  site_id: string | null; network_id: string | null; device: string | null; interface: string | null
  port_speed: number | null; xconnect: string | null; pop: { id: string; name: string } | null; pop_id: string | null
  ip: string | null; allocated: boolean; note: string | null
}
export interface CircuitRow {
  id: string; cid: string; status: string; provider: { id: string; name: string } | null; type: { id: string; name: string } | null
  bandwidth: number | null; install_date: string | null; description: string | null
  a: CircuitEnd | null; z: CircuitEnd | null; interconnect: { id: string; network: string } | null
  vlan: number | null; routing: string | null; local_as: string | null; remote_as: string | null
  risks: { id: string; name: string; kind: string }[]; note: string | null; updated_at: string; updated_by: string
  single_point_for?: string[]
}
export interface Hop { kind: 'circuit' | 'site' | 'network'; id: string; cid?: string; provider?: string; name?: string; hit?: boolean }
export interface ImpactResult {
  element: { kind: string; kind_name: string; id: string; name: string }
  circuits: { id: string; cid: string; provider: string; support_phone: string | null }[]
  sites: { site: { id: string; name: string; kind: string; core: boolean }; state: 'down' | 'single'; paths: Hop[][] }[]
  counts: Record<string, number>
  systems: { system: string; sites: { name: string; hosts: number }[] }[]
}
export interface CircuitDetail extends CircuitRow {
  support: { support_phone?: string | null; account?: string | null; contact?: string | null }
  impact: ImpactResult | null
  domains: { kind: string; kind_name: string; name: string; auto: boolean; shared_with: string[] }[]
  can_edit: boolean
}
export interface ProviderRow {
  id: string; name: string; support_phone: string | null; account: string | null; contact: string | null; note: string | null
  circuits: number
  networks: { id: string; provider_id: string; name: string; kind: string; service_id: string | null; interconnects: boolean; note: string | null; used: number }[]
  pops: { id: string; name: string; note: string | null; used: number }[]
}
export interface RiskRow { id: string; name: string; kind: string; note: string | null; circuits: string[] }
export type SiteState = 'cut' | 'single' | 'clear' | 'idle' | 'none'
export interface SiteReport {
  site: { id: string; name: string; kind: string; core: boolean; region: string | null }
  paths: Hop[][]
  single_points: { kind: string; id: string; kind_name: string; name: string }[]
  state: SiteState; state_name: string; note: string | null
}
export interface ResilienceReport {
  can_edit: boolean; core: { id: string; name: string }[]; states: Record<SiteState, string>
  counts: Partial<Record<SiteState, number>>; all: number; total: number; sites: SiteReport[]
}
export type ElementKind = 'circuit' | 'provider' | 'network' | 'pop' | 'device' | 'risk'
export interface ImpactPage {
  options: Record<ElementKind, { id: string; name: string; circuits: number }[]>
  impact?: ImpactResult
}
export interface ChangeRow {
  id: string; at: string; actor_name: string; entity: string; entity_id: string; label: string; action: string
  detail: { field: string; before: unknown; after: unknown }[] | null
}
export interface ImportReport {
  committed: boolean; errors: number
  summary: Record<string, Record<'新增' | '更新' | '无变化' | '错误', number>>
  created: Record<string, string[]>
  rows: { sheet: string; row: number; label: string; result: '新增' | '更新' | '无变化' | '错误'; message: string | null; changes: ChangeRow['detail'] }[]
}
type Where = { space: { id: string; name: string } }
export type SearchResult =
  | ({ kind: 'ip'; prefix: PrefixRow | null; address: AddressRow | null; reservation: ReservationRow | null } & Where)
  | ({ kind: 'prefix'; id: string; network: string; title: string | null; status: string; level?: string | null; relation?: string } & Where)
  | ({ kind: 'address' } & Where & AddressRow)
  | { kind: 'circuit'; space: null; id: string; cid: string; status: string }

const get = async <T,>(url: string, params?: object) => (await api.get<T>(base + url, { params })).data
const send = async <T,>(method: 'post' | 'put' | 'delete', url: string, body?: object) =>
  (await api.request<T>({ method, url: base + url, data: body })).data

async function download(url: string, name: string) {
  const { data } = await api.get<Blob>(base + url, { responseType: 'blob' })
  const href = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = href; link.download = name
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}

type Kind = 'regions' | 'sites' | 'spaces' | 'zones' | 'systems' | 'subsystems' | 'providers' | 'provider-networks' | 'pops'
  | 'circuit-types' | 'risks' | 'circuits'

export const ledger = {
  overview: () => get<Overview>('/overview'),
  resources: () => get<Resources>('/resources'),
  subtree: (id: string) => get<Subtree>(`/prefixes/${id}/tree`),
  carve: (id: string, params: { level_id?: string; length?: number }) => get<CarveOptions>(`/prefixes/${id}/carve`, params),
  prefix: (id: string) => get<PrefixDetail>(`/prefixes/${id}`),
  zones: () => get<{ can_edit: boolean; zones: ZonePageRow[] }>('/zones'),
  systems: () => get<{ can_edit: boolean; systems: SystemRow[] }>('/systems'),
  hosts: (params: { system_id?: string; subsystem_id?: string; status?: string; environment?: string; q?: string; offset?: number; limit?: number }) =>
    get<{ can_edit: boolean; total: number; hosts: HostRow[] }>('/hosts', params),
  host: (id: string) => get<HostRow & { can_edit: boolean }>(`/hosts/${id}`),
  // `taken` repeats as taken=a&taken=b, the way the backend reads a list.
  suggestIp: async (params: { system_id: string; subsystem_id?: string; taken?: string[] }) =>
    (await api.get<{ ip: string; prefix: { id: string; network: string }; space: string }>(`${base}/hosts/suggest-ip`,
      { params, paramsSerializer: { indexes: null } })).data,
  search: (q: string) => get<{ query: string; type: string; results: SearchResult[] }>('/search', { q }),
  changes: (params: { entity?: string; q?: string; offset?: number; limit?: number }) =>
    get<{ items: ChangeRow[]; more: boolean; total: number }>('/changes', params),
  save: <T extends object>(kind: Kind, body: T, id?: string) =>
    send<{ id: string; name?: string }>(id ? 'put' : 'post', id ? `/${kind}/${id}` : `/${kind}`, body),
  remove: (kind: Kind | 'prefixes' | 'reservations' | 'hosts', id: string) =>
    send<{ kept_addresses?: string[]; unlinked?: number }>('delete', `/${kind}/${id}`),
  circuitOptions: () => get<CircuitOptions>('/circuit-options'),
  circuits: (params: { q?: string; provider_id?: string; type_id?: string; status?: string; site_id?: string; single_point?: boolean; offset?: number; limit?: number }) =>
    get<{ can_edit: boolean; total: number; counts: Record<string, number>; single_points: number; circuits: CircuitRow[] }>('/circuits', params),
  circuit: (id: string) => get<CircuitDetail>(`/circuits/${id}`),
  saveCircuit: (body: object, id?: string) => send<{ id: string; cid: string }>(id ? 'put' : 'post', id ? `/circuits/${id}` : '/circuits', body),
  providers: () => get<{ can_edit: boolean; providers: ProviderRow[] }>('/providers'),
  circuitTypes: () => get<{ can_edit: boolean; types: { id: string; name: string; note: string | null; used: number }[] }>('/circuit-types'),
  risks: () => get<{ can_edit: boolean; risks: RiskRow[] }>('/risks'),
  resilience: (params: { q?: string; kind?: string; state?: string; offset?: number; limit?: number }) => get<ResilienceReport>('/resilience', params),
  impact: (kind?: string, id?: string) => get<ImpactPage>('/impact', { kind, id }),
  saveLevels: (levels: Partial<Level>[]) => send<{ levels: Level[] }>('put', '/levels', { levels }),
  createPrefix: (body: object) => send<{ id: string; network: string }>('post', '/prefixes', body),
  updatePrefix: (id: string, body: object) => send('put', `/prefixes/${id}`, body),
  saveHost: (body: object, id?: string) => send<{ id: string }>(id ? 'put' : 'post', id ? `/hosts/${id}` : '/hosts', body),
  allocate: (prefixId: string, body: object) => send<AddressRow>('post', `/prefixes/${prefixId}/addresses`, body),
  updateAddress: (id: string, body: object) => send<AddressRow>('put', `/addresses/${id}`, body),
  release: (id: string) => send('delete', `/addresses/${id}`),
  reserve: (prefixId: string, body: object) => send<ReservationRow>('post', `/prefixes/${prefixId}/reservations`, body),
  importWorkbook: async (file: File, commit: boolean) =>
    (await api.post<ImportReport>(`${base}/import/${commit ? 'commit' : 'preview'}`, file,
      { headers: { 'Content-Type': 'application/octet-stream' } })).data,
  downloadTemplate: () => download('/import/template', '资源台账导入模板.xlsx'),
  exportAll: () => download('/export', `资源台账-${new Date().toISOString().slice(0, 10)}.xlsx`),
}

/** Query keys every write may make stale. */
export const LEDGER_KEYS = ['rl-overview', 'rl-resources', 'rl-subtree', 'rl-prefix', 'rl-zones', 'rl-systems', 'rl-hosts', 'rl-search',
  'rl-circuits', 'rl-circuit', 'rl-circuit-options', 'rl-providers', 'rl-circuit-types', 'rl-risks', 'rl-resilience', 'rl-impact']

export const errorText = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请稍后重试')

/** Integer value of a dotted IPv4 address, for ordering and map positions. */
export const ipValue = (ip: string) => ip.split('.').reduce((total, part) => total * 256 + Number(part), 0)
export const ipText = (value: number) => [24, 16, 8, 0].map(shift => Math.floor(value / 2 ** shift) % 256).join('.')
export const blockSize = (length: number) => 2 ** (32 - length)
