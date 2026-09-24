import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import RulePackagesWorkspace from './RulePackagesWorkspace'
import * as packages from '../../../services/rulePackages'

const preview: packages.PackagePreview = {
  revision: 'a'.repeat(64), sha256: 'b'.repeat(64), counts: { new_version: 1 }, requires_confirmation: true,
  sample_count: 0, recipe_count: 0, samples: [], reports: [],
  items: [{ ref: 'parser:test', kind: 'parser', action: 'new_version', local_id: 'local', version: '2', same_version_conflict: true, before: { version: '1' }, after: { version: '2' } }],
}
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: true, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }))
  vi.spyOn(packages.packageApi, 'assets').mockResolvedValue({ items: [{ ref: 'parser:test', kind: 'parser', name: 'show version', version: '1', status: 'active', scope: 'test' }], total: 1 })
  vi.spyOn(packages.packageApi, 'history').mockResolvedValue({ items: [], total: 0 })
  vi.spyOn(packages, 'fileBase64').mockResolvedValue('package-content')
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><RulePackagesWorkspace /></MemoryRouter></QueryClientProvider>)
}
async function upload() {
  await userEvent.click(screen.getByRole('tab', { name: '导入规则包' }))
  fireEvent.change(document.querySelector('input[type=file]')!, { target: { files: [new File(['zip'], 'rules.netops-rules.zip', { type: 'application/zip' })] } })
  await screen.findByRole('button', { name: '确认导入' })
}
it('requires explicit conflict confirmation and submits the preview revision', async () => {
  vi.spyOn(packages.packageApi, 'preview').mockResolvedValue(preview)
  const save = vi.spyOn(packages.packageApi, 'import').mockResolvedValue({ record_id: 'record', sha256: preview.sha256, items: preview.items, counts: { new_version: 1 }, changes: [], recipes: [], reports: [], notice: '待验证' })
  mount(); await upload()
  expect(screen.getByRole('button', { name: '确认导入' })).toBeDisabled()
  expect(save).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('checkbox', { name: /已核对冲突/ }))
  await userEvent.click(screen.getByRole('button', { name: '确认导入' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('package-content', preview.revision, true))
  expect(await screen.findByText('导入完成，新增资产等待本地验证与发布')).toBeVisible()
})
it('keeps the file and offers a fresh preview after a concurrent modification', async () => {
  vi.spyOn(packages.packageApi, 'preview').mockResolvedValue({ ...preview, requires_confirmation: false })
  vi.spyOn(packages.packageApi, 'import').mockRejectedValue({ response: { data: { detail: '资产已发生变化，请重新预检' } } })
  mount(); await upload()
  await userEvent.click(screen.getByRole('button', { name: '确认导入' }))
  expect(await screen.findByText('资产已发生变化，请重新预检')).toBeVisible()
  expect(screen.getByText('rules.netops-rules.zip')).toBeVisible()
  expect(screen.getByRole('button', { name: '重新预检' })).toBeEnabled()
})
it('previews both sides of an asset conflict without importing', async () => {
  vi.spyOn(packages.packageApi, 'preview').mockResolvedValue(preview)
  const save = vi.spyOn(packages.packageApi, 'import')
  mount(); await upload(); await userEvent.click(screen.getByRole('button', { name: '查看差异' }))
  expect(await screen.findByRole('heading', { name: '本地现有内容' })).toBeVisible()
  expect(screen.getByRole('heading', { name: '包内内容 / 目标版本' })).toBeVisible()
  expect(save).not.toHaveBeenCalled()
})
it('exports explicit selection with optional samples disabled by default', async () => {
  const exportFile = vi.spyOn(packages.packageApi, 'export').mockResolvedValue({ content_base64: '', filename: 'rules.zip', asset_count: 3, sample_count: 0 })
  vi.spyOn(packages, 'downloadPackage').mockImplementation(() => {})
  mount(); await screen.findByText('show version')
  const boxes = screen.getAllByRole('checkbox')
  await userEvent.click(boxes[1])
  await userEvent.click(screen.getByRole('button', { name: /导出选中与依赖/ }))
  await waitFor(() => expect(exportFile).toHaveBeenCalledWith({ selections: ['parser:test'], all_active: false, include_samples: false, include_drafts: false }))
})
