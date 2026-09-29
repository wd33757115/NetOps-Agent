import { expect, test, type Page } from '@playwright/test'

async function setup(page: Page, reason = 'authentication_failed') {
  const user = { id: 91, username: '拓扑验证', role: 'operator', app_ids: ['topology', 'device-access'] }
  const errors: string[] = []
  const resolutions: Record<string, unknown>[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(value => { localStorage.setItem('access_token', 'test'); localStorage.setItem('auth_user', JSON.stringify(value)) }, user)
  const run = { id: 'run-1', network_id: 'net-1', status: 'waiting_for_input', created_at: '2026-09-28T00:00:00Z', graph: {
    model_profile: 'rfc8345-base+netops-observation-v1', nodes: [
      { id: 'a', name: '10.0.0.1', address: '10.0.0.1', identity_status: 'address_scoped', freshness: 'observed', observed_at: '2026-09-28T00:00:00Z', evidence_id: 'obs-1', tps: [{ id: 'a1', name: 'Ethernet1/1' }] },
      { id: 'b', name: 'edge-b', addresses: ['10.0.0.2'], identity_status: 'candidate', freshness: 'advertised', observed_at: '2026-09-28T00:00:00Z', tps: [{ id: 'b1', name: 'Ethernet1/2' }] },
    ], links: [{ id: 'ab', source: 'a', source_tp: 'a1', target: 'b', target_tp: 'b1', freshness: 'observed', evidence_id: 'obs-1' }],
  }, targets: [
    { id: 't1', address: '10.0.0.1', status: 'succeeded', version: 1, depth: 0, evidence_id: 'obs-1' },
    { id: 't2', address: '10.0.0.2', status: 'waiting_for_input', reason, version: 1, depth: 1 },
  ] }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (path.endsWith('/desktop-layout/')) return route.fulfill({ json: { schemaVersion: 1, revision: 1, nodes: [], dockAppIds: [] } })
    if (path === '/api/apps/topology/networks') return route.fulfill({ json: [{ id: 'net-1', name: '实验室观测网络', active_run: 'run-1' }] })
    if (path.endsWith('/net-1/runs')) return route.fulfill({ json: [{ id: run.id, status: run.status, created_at: run.created_at }] })
    if (path.endsWith('/targets/t2/resolve')) {
      resolutions.push(route.request().postDataJSON()); run.targets[1].status = 'queued'; run.status = 'running'
      return route.fulfill({ json: run })
    }
    if (path.endsWith('/runs/run-1')) return route.fulfill({ json: run })
    if (path.endsWith('/evidence/obs-1')) return route.fulfill({ json: { outputs: { neighbors: 'Chassis id: synthetic-lab\nPort id: Ethernet1/2' }, complete: true } })
    if (path === '/api/apps/device-access/sessions') return route.fulfill({ json: [{ id: 's1', name: '核心交换机', host: '10.0.0.1', port: 22, username: null, uses_default: true }] })
    if (path.endsWith('/default-login')) return route.fulfill({ json: { username: 'admin', configured: true } })
    return route.fulfill({ json: { items: [], unread_count: 0 } })
  })
  return { errors, resolutions }
}

test('topology renders evidence and requires explicit credential correction', async ({ page }, info) => {
  const state = await setup(page)
  await page.goto('/apps/topology')
  await expect(page.getByText('1 个节点等待人工处理；成功节点的观测已保留')).toBeVisible()
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  // The failed node asks for a new login by itself.
  const dialog = page.getByRole('dialog', { name: '处理节点 · 10.0.0.2' })
  await expect(dialog).toBeVisible()
  await page.screenshot({ path: `test-results/topology-${info.project.name}.png`, fullPage: true })
  await dialog.getByLabel('用户名', { exact: true }).fill('new-operator')
  await dialog.getByLabel('密码', { exact: true }).fill('fixture-password')
  await dialog.getByRole('button', { name: '确认并重试一次', exact: true }).click()
  await expect.poll(() => state.resolutions.length).toBe(1)
  expect(state.resolutions[0]).toMatchObject({ action: 'retry', version: 1, credentials: { username: 'new-operator', password: 'fixture-password' } })
  await page.getByRole('button', { name: /^证\s*据$/ }).first().click()
  await expect(page.getByText('synthetic-lab', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('fixture-password')
  expect(state.errors).toEqual([])
})

test('SSH terminal lists saved sessions with the default login', async ({ page }, info) => {
  const state = await setup(page)
  await page.goto('/apps/device-access')
  await expect(page.getByRole('heading', { name: '打开一个会话', exact: true })).toBeVisible()
  await expect(page.getByText('10.0.0.1 · 默认登录', { exact: true })).toBeVisible()
  await expect(page.getByText('admin · 密码已加密保存', { exact: true })).toBeVisible()
  await page.screenshot({ path: `test-results/device-access-${info.project.name}.png`, fullPage: true })
  expect(state.errors).toEqual([])
})


test('new discovery defaults to auto and limits manual platform to seeds', async ({ page }) => {
  await setup(page)
  await page.goto('/apps/topology')
  await page.getByRole('dialog', { name: '处理节点 · 10.0.0.2' }).getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: '新建发现任务', exact: true }).click()
  await expect(page.getByText('自动识别（无需预先知道型号）', { exact: true })).toBeVisible()
  await expect(page.getByText('默认自动识别。手工选择只适用于种子；每个新邻居都独立识别，不继承种子平台。', { exact: true })).toBeVisible()
  await expect(page.getByLabel('种子设备平台（可选）')).toBeVisible()
})

test('unknown platform offers per-node correction without requiring new credentials', async ({ page }) => {
  const state = await setup(page, 'platform_unrecognized')
  await page.goto('/apps/topology')
  await expect(page.getByRole('dialog', { name: '处理节点 · 10.0.0.2' })).toBeVisible()
  await expect(page.getByLabel('为此节点输入新的用户名和密码')).not.toBeChecked()
  await page.getByText('自动识别（无需预先知道型号）', { exact: true }).click()
  await page.getByText('NX-OS · LLDP v1', { exact: true }).click()
  await page.getByRole('button', { name: '确认并重试一次', exact: true }).click()
  await expect.poll(() => state.resolutions.length).toBe(1)
  expect(state.resolutions[0]).toMatchObject({ action: 'retry', profile_id: 'nxos-lldp-v1' })
  expect(state.resolutions[0]).not.toHaveProperty('credentials')
  expect(state.errors).toEqual([])
})
