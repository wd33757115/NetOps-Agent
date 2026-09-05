import { expect, test, type Page } from '@playwright/test'

async function setup(page: Page, role: string, permissions: string[] = []) {
  const user = { id: 10, username: role, role, permissions, email: 'fixture@example.test' }
  const errors: string[] = []
  const requests: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(account => {
    localStorage.setItem('access_token', 'fixture-token')
    localStorage.setItem('auth_user', JSON.stringify(account))
  }, user)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    requests.push(path)
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (path.endsWith('/desktop-layout/')) return route.fulfill({ json: { schemaVersion: 1, revision: 0,
      nodes: ['patrol', 'patrol-rules', 'device-configuration'].map((id, col) => ({ id, appId: id, kind: 'app', containerId: 'desktop:0', col, row: 0 })),
      dockAppIds: ['patrol', 'patrol-rules', 'device-configuration'],
    } })
    return route.fulfill({ json: { items: [], total: 0, conversations: [], unread_count: 0, summary: {} } })
  })
  return { errors, requests }
}

for (const role of ['operator', 'viewer']) {
  test(`${role} sees patrol but cannot open configuration pages`, async ({ page }) => {
    const state = await setup(page, role)
    await page.goto('/workbench')
    await expect(page.getByText('网络巡检', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('设备配置', { exact: true })).toHaveCount(0)
    await expect(page.getByText('巡检规则工作台', { exact: true })).toHaveCount(0)
    await page.goto('/apps/patrol-rules')
    await expect(page.getByText('需要设备与规则配置权限', { exact: true })).toBeVisible()
    await page.goto('/admin/netops/devices')
    await expect(page.getByText('需要设备与规则配置权限', { exact: true })).toBeVisible()
    expect(state.requests.some(path => path.includes('/parser/asset-production/') || path.endsWith('/devices/'))).toBe(false)
    await page.goto('/devices')
    await expect(page).toHaveURL(/\/apps\/patrol$/)
    await expect(page.getByRole('tab', { name: '全部任务' })).toBeVisible()
    await expect(page.getByRole('button', { name: '巡检方案', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Devices$/ })).toHaveCount(0)
    expect(state.errors).toEqual([])
    await page.screenshot({ path: `test-results/patrol-${role}-${test.info().project.name}.png`, fullPage: true })
  })
}
for (const account of [{ role: 'admin', permissions: [] }, { role: 'operator', permissions: ['netops.configure'] }]) {
  test(`${account.role} with configuration access gets both management applications`, async ({ page }) => {
    const state = await setup(page, account.role, account.permissions)
    await page.goto('/workbench')
    await expect(page.getByText('设备配置', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('巡检规则工作台', { exact: true }).first()).toBeVisible()
    await page.goto('/apps/patrol-rules')
    await expect(page.getByRole('heading', { name: '制作任务', exact: true })).toBeVisible()
    await page.goto('/admin/netops/devices')
    await expect(page.getByRole('button', { name: /新增设备/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Devices$/ })).toHaveCount(0)
    expect(state.errors).toEqual([])
  })
}
