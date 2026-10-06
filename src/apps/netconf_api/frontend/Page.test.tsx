import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Page from './Page'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api }))
vi.mock('@netops/context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'operator' } }) }))

const BASE = '/apps/netconf-api'
const arpBody = {
  id: 'h3c-comware7.arp', vendor: 'H3C', platforms: ['comware'], check_item: 'topology.arp',
  namespace: 'http://www.h3c.com/netconf/data:1.0', datastore: 'state', module: 'ARP',
  path: 'top/ARP/ArpTable/ArpEntry', keys: ['Ipv4Address'],
  values: { IP_ADDRESS: 'Ipv4Address', TYPE: { leaf: 'Type', codes: { 1: 'Static', 2: 'Dynamic' } } },
}
const summaries = [
  { id: 'h3c-comware7.arp', check_item: 'topology.arp', vendor: 'H3C', platforms: ['comware'], description: 'ARP 表', module: 'ARP', published_version: 2, has_draft: true, revision: 5 },
  { id: 'h3c-comware7.interfaces', check_item: null, vendor: 'H3C', platforms: ['comware'], description: '接口表', module: 'Ifmgr', published_version: 1, has_draft: false, revision: 1 },
]
const detail = {
  ...summaries[0], draft: { ...arpBody, description: 'ARP 表（改）' }, draft_by: 'alice', draft_at: '2026-10-06T08:00:00+00:00',
  published: { version: 2, sha256: 'a'.repeat(64), check_item: 'topology.arp', published_by: 'alice', published_at: '2026-10-05T08:00:00+00:00', note: '第二版', body: arpBody },
  versions: [
    { version: 2, sha256: 'a'.repeat(64), check_item: 'topology.arp', published_by: 'alice', published_at: '2026-10-05T08:00:00+00:00', note: '第二版' },
    { version: 1, sha256: 'b'.repeat(64), check_item: 'topology.arp', published_by: 'system', published_at: '2026-10-04T08:00:00+00:00', note: 'seed' },
  ],
}
const contract = {
  check_item: 'topology.arp', vendor: 'H3C', platform: 'comware', values: [
    { name: 'IP_ADDRESS', value_type: 'ip_or_unassigned', required: true, identity: true },
    { name: 'MAC_ADDRESS', value_type: 'text', required: true, identity: false },
    { name: 'TYPE', value_type: 'text', required: false, identity: false },
  ],
}
const firstBody = { ...arpBody, description: '第一版' }
const devices = [
  { device_id: 'br1', device_name: 'BR1', ip: '10.10.1.11', vendor: 'H3C', platform: 'comware', model: 'VSR1000', capability: null },
]
const capability = {
  device_id: 'br1', status: 'available', failure_code: null, checked_at: '2026-10-06T08:00:00+00:00', capability_count: 384, module_count: 2,
  definitions: ['h3c-comware7.arp'], modules: [{ name: 'ARP', revision: '2021-06-03', namespace: 'http://www.h3c.com/netconf/data:1.0-ARP' }, { name: 'Ifmgr', revision: null, namespace: 'urn:x' }],
  unavailable: [{ id: 'h3c-comware7.pbr-rules', module: 'PBR' }],
}
const trial = {
  id: 't1', device_id: 'br1', definition_id: 'h3c-comware7.arp', version: 2, body_sha256: 'c'.repeat(64), status: 'succeeded',
  failure_code: null, message: null, read_count: 3, row_count: 2, problem_count: 1, reply_bytes: 2048,
  started_at: '2026-10-06T08:00:00.000+00:00', finished_at: '2026-10-06T08:00:01.250+00:00',
  rows: [{ ref: 1, IP_ADDRESS: '10.0.13.2', TYPE: 'Dynamic' }, { ref: 2, IP_ADDRESS: '10.0.12.2', TYPE: 'Static' }],
  problems: [{ row: 3, key: 'Ipv4Address=10.0.14.2', value: 'TYPE', kind: 'unmapped_code', raw: '9', table: 'ARP/ArpTable/ArpEntry' }],
}

const ok = (data: unknown) => Promise.resolve({ status: 200, data })
const refused = (status: number, detail: unknown) => Promise.resolve({ status, data: { detail } })

function routes(overrides: Record<string, () => Promise<unknown>> = {}) {
  api.get.mockImplementation((url: string) => {
    const path = url.replace(BASE, '')
    if (overrides[`GET ${path}`]) return overrides[`GET ${path}`]()
    if (path === '/definitions') return ok(summaries)
    if (path === '/definitions/h3c-comware7.arp') return ok(detail)
    if (path === '/devices') return ok(devices)
    if (path.startsWith('/check-items/topology.arp/contract?')) return ok(contract)
    if (path === '/definitions/h3c-comware7.arp/versions/1') return ok({ ...detail.versions[1], body: firstBody })
    if (path.startsWith('/trials?')) return ok([])
    return refused(404, '不存在')
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  routes()
  api.post.mockImplementation(() => refused(404, '不存在'))
  api.put.mockImplementation(() => refused(404, '不存在'))
})
afterEach(() => cleanup())

const view = () => render(<MemoryRouter><Page /></MemoryRouter>)

async function openArp() {
  view()
  fireEvent.click(await screen.findByRole('button', { name: 'h3c-comware7.arp' }))
  return screen.findByText('top/ARP/ArpTable/ArpEntry')
}

it('lists the definitions with their state and filters them', async () => {
  view()
  expect(await screen.findByRole('button', { name: 'h3c-comware7.arp' })).toBeInTheDocument()
  expect(screen.getByText('已发布 v2')).toBeInTheDocument()
  expect(screen.getByText('有草稿')).toBeInTheDocument()
  expect(screen.getByText('仅供关联')).toBeInTheDocument()
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '按模块筛选' }))
  fireEvent.click(await screen.findByText('Ifmgr', { selector: '.ant-select-item-option-content' }))
  expect(screen.queryByRole('button', { name: 'h3c-comware7.arp' })).not.toBeInTheDocument()
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '按模块筛选' }))
  fireEvent.click(await screen.findByText('ARP', { selector: '.ant-select-item-option-content' }))
  expect(screen.getByRole('button', { name: 'h3c-comware7.arp' })).toBeInTheDocument()
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '按模块筛选' }))
  fireEvent.click(await screen.findByText('Ifmgr', { selector: '.ant-select-item-option-content' }))
  fireEvent.change(screen.getByLabelText('搜索定义'), { target: { value: '接口' } })
  expect(screen.queryByRole('button', { name: 'h3c-comware7.arp' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'h3c-comware7.interfaces' })).toBeInTheDocument()
})

it('shows a definition and switches between draft and published version', async () => {
  await openArp()
  expect(screen.getByText('top/ARP/ArpTable/ArpEntry')).toBeInTheDocument()
  expect(screen.getByText(/→ Dynamic/)).toBeInTheDocument() // code table
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '查看的版本' }))
  fireEvent.click(await screen.findByText('v2（使用中）', { selector: '.ant-select-item-option-content' }))
  fireEvent.click(screen.getByText('源码'))
  expect(screen.getByLabelText('定义源码').textContent).not.toContain('ARP 表（改）')
  fireEvent.click(screen.getByText('版本历史'))
  expect(screen.getByText('第二版')).toBeInTheDocument()
  expect(screen.getByText('使用中')).toBeInTheDocument()
  fireEvent.click(screen.getByText('差异', { selector: '.ant-segmented-item-label' }))
  // Viewing the version in use, it is compared with the draft.
  expect(screen.getByLabelText('版本差异').textContent).toContain('-   "description": "ARP 表（改）"')
})

it('shows the check item Values and those the definition leaves unmapped', async () => {
  await openArp()
  const panel = await screen.findByLabelText('检查项的值')
  expect(await within(panel).findByText('未映射的必填值：MAC_ADDRESS')).toBeInTheDocument()
  expect(within(panel).getByText('ip_or_unassigned')).toBeInTheDocument()
  expect(api.get).toHaveBeenCalledWith(`${BASE}/check-items/topology.arp/contract?vendor=H3C&platform=comware`, expect.anything())
  // In the editor the check follows the text being edited.
  fireEvent.click(screen.getByRole('button', { name: /编辑草稿/ }))
  const withMac = { ...detail.draft, values: { ...detail.draft.values, MAC_ADDRESS: 'MacAddress' } }
  fireEvent.change(await screen.findByLabelText('定义 JSON'), { target: { value: JSON.stringify(withMac) } })
  expect(await screen.findByText('comware：覆盖了检查项的全部必填值')).toBeInTheDocument()
})

it('loads an older version, compares any two and starts a draft from one', async () => {
  await openArp()
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '查看的版本' }))
  fireEvent.click(await screen.findByText('v1', { selector: '.ant-select-item-option-content' }))
  fireEvent.click(screen.getByText('源码'))
  await waitFor(() => expect(screen.getByLabelText('定义源码').textContent).toContain('第一版'))
  expect(api.get).toHaveBeenCalledWith(`${BASE}/definitions/h3c-comware7.arp/versions/1`, expect.anything())
  fireEvent.click(screen.getByText('差异', { selector: '.ant-segmented-item-label' }))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '比较的版本' }))
  await waitFor(() => expect(screen.getAllByText('v2（使用中）', { selector: '.ant-select-item-option-content' }).length).toBeGreaterThan(1))
  // The last list opened is the comparison's.
  fireEvent.click(screen.getAllByText('v2（使用中）', { selector: '.ant-select-item-option-content' }).at(-1)!)
  expect(screen.getByLabelText('版本差异').textContent).toContain('+   "description": "第一版"')
  fireEvent.click(screen.getByRole('button', { name: '以此版本为草稿' }))
  expect(((await screen.findByLabelText('定义 JSON')) as HTMLTextAreaElement).value).toContain('第一版')
})

it('shows the validation error of a draft as the backend wrote it', async () => {
  api.put.mockImplementation(() => refused(422, '定义不可用：h3c-comware7.arp.values.TYPE has unknown fields [\'code\']'))
  await openArp()
  fireEvent.click(screen.getByRole('button', { name: /编辑草稿/ }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /保存草稿/ }))
  expect(await screen.findByText("定义不可用：h3c-comware7.arp.values.TYPE has unknown fields ['code']")).toBeInTheDocument()
  expect(api.put).toHaveBeenCalledWith(`${BASE}/definitions/h3c-comware7.arp/draft`, { body: detail.draft, revision: 5 }, expect.anything())
})

it('rejects text that is not JSON before sending it', async () => {
  await openArp()
  fireEvent.click(screen.getByRole('button', { name: /编辑草稿/ }))
  fireEvent.change(await screen.findByLabelText('定义 JSON'), { target: { value: '{ id: ' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /保存草稿/ }))
  expect(await screen.findByText(/JSON 格式错误/)).toBeInTheDocument()
  expect(api.put).not.toHaveBeenCalled()
})

it('reports a revision conflict and offers to refresh', async () => {
  api.put.mockImplementation(() => refused(409, '定义已被修改，请刷新后重试'))
  await openArp()
  fireEvent.click(screen.getByRole('button', { name: /编辑草稿/ }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /保存草稿/ }))
  expect(await screen.findByText('版本冲突')).toBeInTheDocument()
  expect(screen.getByText('定义已被修改，请刷新后重试')).toBeInTheDocument()
  const reads = api.get.mock.calls.length
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /刷\s*新/ }))
  await waitFor(() => expect(api.get.mock.calls.length).toBeGreaterThan(reads))
})

it('publishes a draft with its note', async () => {
  api.post.mockImplementation((url: string) => url.endsWith('/publish')
    ? ok({ id: 'h3c-comware7.arp', revision: 6, version: 3, sha256: 'd'.repeat(64), check_item: 'topology.arp', published_by: 'operator', published_at: null, note: '修正类型' })
    : refused(404, '不存在'))
  await openArp()
  fireEvent.click(screen.getByRole('button', { name: /^发\s*布$/ }))
  fireEvent.change(await screen.findByLabelText('发布说明'), { target: { value: '修正类型' } })
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^发\s*布$/ }))
  expect(await screen.findByText('已发布 v3')).toBeInTheDocument()
  expect(api.post).toHaveBeenCalledWith(`${BASE}/definitions/h3c-comware7.arp/publish`, { revision: 5, note: '修正类型' }, expect.anything())
})

it('shows the coverage check when publishing is refused', async () => {
  api.post.mockImplementation(() => refused(409, { message: '定义与检查项的值不一致', gaps: ['required_value_unmapped:IP_ADDRESS:comware'] }))
  await openArp()
  fireEvent.click(screen.getByRole('button', { name: /^发\s*布$/ }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^发\s*布$/ }))
  expect(await screen.findByText('定义与检查项的值不一致')).toBeInTheDocument()
  expect(screen.getByText('required_value_unmapped:IP_ADDRESS:comware')).toBeInTheDocument()
})

it('creates a definition for a new device family by copying one', async () => {
  api.put.mockImplementation(() => ok({ id: 'h3c-comware5.arp', revision: 1 }))
  view()
  fireEvent.click(await screen.findByRole('button', { name: /新建定义/ }))
  fireEvent.change(screen.getByLabelText('定义 id'), { target: { value: 'h3c-comware5.arp' } })
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '复制自' }))
  fireEvent.click(await screen.findByText('h3c-comware7.arp', { selector: '.ant-select-item-option-content' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /创建草稿/ }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith(
    `${BASE}/definitions/h3c-comware5.arp/draft`, { body: { ...detail.draft, id: 'h3c-comware5.arp' }, revision: 0 }, expect.anything()))
})

it('runs capability discovery and shows modules and definitions', async () => {
  let discovered = false
  routes({ 'GET /devices': () => ok(discovered ? [{ ...devices[0], capability }] : devices) })
  api.post.mockImplementation(() => { discovered = true; return ok(capability) })
  view()
  fireEvent.click(await screen.findByRole('button', { name: /设备能力/ }))
  fireEvent.click(await screen.findByRole('button', { name: '能力发现 BR1' }))
  expect(await screen.findByText('能力发现完成')).toBeInTheDocument()
  expect(api.post).toHaveBeenCalledWith(`${BASE}/devices/br1/capabilities`, undefined, expect.anything())
  expect(await screen.findByText('2021-06-03')).toBeInTheDocument()
  expect(screen.getByText('未声明')).toBeInTheDocument()
  expect(screen.getByText('设备未通告模块 PBR')).toBeInTheDocument()
})

it('browses a subtree read only, from a definition, and narrows the path from the tree', async () => {
  const data = '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><top xmlns="http://www.h3c.com/netconf/data:1.0"><ARP><ArpTable><ArpEntry><Ipv4Address>10.0.13.2</Ipv4Address><Type>2</Type></ArpEntry></ArpTable></ARP></top></data>'
  api.post.mockImplementation((url: string) => url.endsWith('/browse')
    ? ok({ status: 'succeeded', filter: '<top/>', datastore: 'state', data, reply_bytes: 300, elapsed_ms: 42 })
    : refused(404, '不存在'))
  view()
  fireEvent.click(await screen.findByRole('button', { name: /设备能力/ }))
  fireEvent.click(await screen.findByText('BR1'))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '从定义带入' }))
  fireEvent.click(await screen.findByText('h3c-comware7.arp', { selector: '.ant-select-item-option-content' }))
  await waitFor(() => expect((screen.getByLabelText('路径') as HTMLInputElement).value).toBe('top/ARP/ArpTable/ArpEntry'))
  expect((screen.getByLabelText('命名空间') as HTMLInputElement).value).toBe('http://www.h3c.com/netconf/data:1.0')
  fireEvent.click(screen.getByRole('button', { name: /读\s*取/ }))
  const result = await screen.findByLabelText('浏览结果')
  expect(api.post).toHaveBeenCalledWith(`${BASE}/devices/br1/browse`,
    { namespace: 'http://www.h3c.com/netconf/data:1.0', path: ['top', 'ARP', 'ArpTable', 'ArpEntry'], datastore: 'state' }, expect.anything())
  expect(within(result).getByText('10.0.13.2')).toBeInTheDocument()
  expect(within(result).getByText('42 ms')).toBeInTheDocument()
  // A node of the tree becomes the path, read again.
  fireEvent.click(within(result).getAllByRole('button', { name: '设为路径' })[1])
  await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(`${BASE}/devices/br1/browse`,
    { namespace: 'http://www.h3c.com/netconf/data:1.0', path: ['top', 'ARP'], datastore: 'state' }, expect.anything()))
})

it('shows the failure code of a discovery that failed', async () => {
  const failed = { ...capability, status: 'unavailable', failure_code: 'authentication_failed', definitions: [], modules: [], unavailable: [] }
  api.post.mockImplementation(() => ok(failed))
  view()
  fireEvent.click(await screen.findByRole('button', { name: /设备能力/ }))
  fireEvent.click(await screen.findByRole('button', { name: '能力发现 BR1' }))
  expect(await screen.findByText('能力发现失败：登录失败（用户名或凭据不对）（authentication_failed）')).toBeInTheDocument()
})

it('shows the rows and the problems of a trial', async () => {
  api.post.mockImplementation(() => ok(trial))
  view()
  await screen.findByRole('button', { name: 'h3c-comware7.arp' })
  fireEvent.click(screen.getByRole('button', { name: /试运行/ }))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择定义' }))
  fireEvent.click(await screen.findByText('h3c-comware7.arp', { selector: '.ant-select-item-option-content' }))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择设备' }))
  fireEvent.click(await screen.findByText('BR1 10.10.1.11', { selector: '.ant-select-item-option-content' }))
  fireEvent.click(screen.getByRole('switch', { name: '用草稿' }))
  fireEvent.click(screen.getByRole('button', { name: /^试运行$/ }))
  const result = await screen.findByLabelText('试运行结果')
  expect(api.post).toHaveBeenCalledWith(`${BASE}/trials`, { device_id: 'br1', definition_id: 'h3c-comware7.arp', draft: true }, expect.anything())
  expect(within(result).getByText('10.0.13.2')).toBeInTheDocument()
  expect(within(result).getAllByText('IP_ADDRESS').length).toBeGreaterThan(0)
  expect(within(result).getByText('未映射的代码')).toBeInTheDocument()
  expect(within(result).getByText('1250 ms')).toBeInTheDocument()
  expect(within(result).getByText('2.0 KB')).toBeInTheDocument()
  expect(within(result).getByText(/1 个问题/)).toBeInTheDocument()
})

it('says when the user may not use the App', async () => {
  routes({ 'GET /definitions': () => refused(403, 'Forbidden') })
  view()
  expect(await screen.findByText('没有使用 NETCONF 接口应用的权限，请联系管理员开通。')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /设备能力/ })).toBeDisabled()
})
