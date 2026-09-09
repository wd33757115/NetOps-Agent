import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import PatrolCommandBindingDrawer from './PatrolCommandBindingDrawer'
import { parserApi, type ParserCommandProfile, type ParserCommandProfileImpactPreview } from '../../services/api'

const profile: ParserCommandProfile = { profile_id: 'p1', profile_version_id: 'v1', profile_name: '核心交换机', status: 'active', version_status: 'active', current_version: 1, version: 1, items: [{ action: 'patrol', command: 'display version' }, { action: 'patrol', command: 'display cpu-usage' }] }
const impact: ParserCommandProfileImpactPreview = { success: true, mode: 'production', profile_name: profile.profile_name, profile_version_id: 'v1', selector: { device_id: ['d1'] }, command_count: 2, matched_device_count: 1, returned_device_count: 1, blocked_device_count: 1, ready_device_count: 0, eligible_command_count: 2, ready_command_count: 1, blocked_command_count: 1, coverage_rate: 0.5, evidence_saved: true, rows: [] }
const baseProps = { open: true, onClose: vi.fn(), onSaved: vi.fn(), selectedDeviceIds: ['d1'], currentSelector: {}, groups: ['core'], dynamicGroups: [] }
function mount(props = baseProps) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  return render(<QueryClientProvider client={client}><PatrolCommandBindingDrawer {...props} /></QueryClientProvider>)
}
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks() })

it('binds the whole explicit command set with parser gaps visible, without creating check-item selections', async () => {
  vi.spyOn(parserApi, 'commandProfiles').mockResolvedValue({ items: [profile], total: 1, limit: 200, offset: 0 })
  const preview = vi.spyOn(parserApi, 'commandProfileImpactPreview').mockResolvedValue(impact)
  const bind = vi.spyOn(parserApi, 'upsertCommandProfileBinding').mockResolvedValue({ binding_id: 'b1' })
  const enable = vi.spyOn(parserApi, 'enableCommandProfileBinding').mockResolvedValue({})
  mount()
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: '巡检命令集' }))
  await userEvent.click(await screen.findByText('核心交换机 · v1 · 2 条命令', { selector: '.ant-select-item-option-content' }))
  expect(screen.getByText(/1\. display version/)).toHaveTextContent('2. display cpu-usage')
  expect(screen.queryByText('业务检查项')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '预览设备与命令' }))
  await waitFor(() => expect(preview).toHaveBeenCalledWith(expect.objectContaining({ profile_version_id: 'v1', selector: { device_id: ['d1'] } })))
  expect(await screen.findByText(/尚未具备完整解析能力/)).toBeVisible()
  await userEvent.type(screen.getByPlaceholderText('例如：核心交换机日常巡检'), '更新命令集')
  // Editing the change reason conservatively invalidates the previous preview.
  await userEvent.click(screen.getByRole('button', { name: '预览设备与命令' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '确认绑定' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: '确认绑定' }))
  await waitFor(() => expect(enable).toHaveBeenCalledWith('b1', '更新命令集'))
  expect(bind).toHaveBeenCalledWith({ profile_version_id: 'v1', selector: { device_id: ['d1'] }, priority: 100, status: 'disabled' })
})

it('creates an ordered multiline command set and sends no check_item_ids', async () => {
  let profiles: ParserCommandProfile[] = []
  vi.spyOn(parserApi, 'commandProfiles').mockImplementation(async () => ({ items: profiles, total: profiles.length, limit: 200, offset: 0 }))
  const create = vi.spyOn(parserApi, 'upsertCommandProfile').mockImplementation(async () => { profiles = [profile]; return { profile_version_id: 'v1' } })
  mount()
  await userEvent.click(await screen.findByRole('button', { name: '新建命令集' }))
  await userEvent.type(screen.getByPlaceholderText('例如：H3C 核心交换机日常巡检'), '核心交换机')
  fireEvent.change(screen.getByRole('textbox', { name: '巡检命令' }), { target: { value: 'display version\n\ndisplay cpu-usage\ndisplay version' } })
  await userEvent.click(screen.getByRole('button', { name: '保存命令集' }))
  await waitFor(() => expect(create).toHaveBeenCalledWith({ name: '核心交换机', action: 'patrol', commands: ['display version', 'display cpu-usage'], status: 'draft' }))
  expect(await screen.findByText(/1\. display version/)).toBeVisible()
})

it('refuses empty device scope instead of implicitly binding every device', async () => {
  vi.spyOn(parserApi, 'commandProfiles').mockResolvedValue({ items: [profile], total: 1, limit: 200, offset: 0 })
  const preview = vi.spyOn(parserApi, 'commandProfileImpactPreview')
  mount({ ...baseProps, selectedDeviceIds: [] })
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: '巡检命令集' }))
  await userEvent.click(await screen.findByText('核心交换机 · v1 · 2 条命令', { selector: '.ant-select-item-option-content' }))
  await userEvent.click(screen.getByRole('button', { name: '预览设备与命令' }))
  expect(await screen.findByText('请选择设备分组')).toBeVisible()
  expect(preview).not.toHaveBeenCalled()
})

it('ignores an in-flight preview when the selected devices change', async () => {
  vi.spyOn(parserApi, 'commandProfiles').mockResolvedValue({ items: [profile], total: 1, limit: 200, offset: 0 })
  let resolvePreview!: (value: ParserCommandProfileImpactPreview) => void
  const preview = vi.spyOn(parserApi, 'commandProfileImpactPreview').mockImplementation(() => new Promise(resolve => { resolvePreview = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  const renderScope = (ids: string[]) => <QueryClientProvider client={client}><PatrolCommandBindingDrawer {...baseProps} selectedDeviceIds={ids} /></QueryClientProvider>
  const view = render(renderScope(['d1']))
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: '巡检命令集' }))
  await userEvent.click(await screen.findByText('核心交换机 · v1 · 2 条命令', { selector: '.ant-select-item-option-content' }))
  await userEvent.click(screen.getByRole('button', { name: '预览设备与命令' }))
  await waitFor(() => expect(preview).toHaveBeenCalled())
  view.rerender(renderScope(['d2']))
  await act(async () => { resolvePreview(impact) })
  expect(screen.getByRole('button', { name: '确认绑定' })).toBeDisabled()
  expect(screen.queryByText(/尚未具备完整解析能力/)).not.toBeInTheDocument()
})
