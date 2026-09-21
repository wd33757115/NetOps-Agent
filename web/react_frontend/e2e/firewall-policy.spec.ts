import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import { Buffer } from 'node:buffer'
import path from 'node:path'
import AxeBuilder from '@axe-core/playwright'

const user = { id: 1, username: 'operator', role: 'user', email: '', app_ids: ['firewall-policy'] }
const manifest = { ticket_id: 'CHG-001', ticket_title: '防火墙策略变更',
  scripts: [{ device_name: 'FW-A', commands: 'action permit' }, { device_name: 'FW-B', commands: 'action permit' }],
  devices: [{ device_name: 'FW-A' }, { device_name: 'FW-B' }],
  rollback: [{ device_name: 'FW-A', rollback_command: '恢复已有备份文件' }],
}
const plan = {
  ticket_id: 'CHG-001', input_name: '申请.xlsx', plan_id: 'plan-1',
  commands: { 'FW-A': 'security-policy\n rule name CHG-001\n action permit', 'FW-B': 'security-policy\n rule name CHG-001-B\n action permit' },
  rules: ['FW-A', 'FW-B'].map((device, i) => ({ id: `rule-${i}`, device, sources: ['10.0.0.1'], destinations: ['10.1.0.1'],
    source_zone: 'Trust', destination_zone: 'Untrust', protocol: 'tcp', ports: ['443'], evidence_ids: ['E1'] })),
  requests: [{ row: 2, src_ip: '10.0.0.1', dst_ip: '10.1.0.1', port: '443', proto: 'tcp' }],
  evidence: [{ id: 'E1', row: 2, path: [['FW-A', 'Trust'], ['FW-A', 'Untrust'], ['FW-B', 'Trust']], path_source: 'explicit',
    source_segments: [{ addresses: ['10.0.0.1'], prefix: '10.0.0.0/24', owner: ['FW-A', 'Trust'] }],
    destination_segments: [{ addresses: ['10.1.0.1'], prefix: '10.1.0.0/24', owner: ['FW-B', 'Untrust'] }] }],
  warnings: ['请核对设备内部安全域连接。'], optimization: { status: 'verified', before: 3, after: 2 },
}
async function setup(page: Page) {
  const errors: string[] = []
  const requests: Record<string, unknown>[] = []
  const uploads: Record<string, unknown>[] = []
  const ticketRequests: Record<string, unknown>[] = []
  const state = { fail: false, ticketFail: false, pending: false, revoked: false }
  const runs = new Map<string, Record<string, any>>()
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(account => { localStorage.setItem('access_token', 'test-token'); localStorage.setItem('auth_user', JSON.stringify(account)) }, user)
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (url.pathname.endsWith('/desktop-layout/')) return route.fulfill({ json: { schemaVersion: 1, revision: 1, nodes: [], dockAppIds: [] } })
    const base = '/api/apps/firewall-policy'
    if (url.pathname.startsWith(base) && state.revoked) return route.fulfill({ status: 403, json: { detail: '应用权限已撤销' } })
    if (url.pathname === `${base}/uploads`) {
      uploads.push({ filename: url.searchParams.get('filename'), kind: url.searchParams.get('kind') })
      return route.fulfill({ json: { id: url.searchParams.get('kind') === 'policy' ? 'a'.repeat(32) : 'b'.repeat(32) } })
    }
    if (url.pathname === `${base}/template`) return route.fulfill({ body: fs.readFileSync(path.resolve('../../src/apps/firewall_policy/resources/firewall-policy-request.xlsx')) })
    if (url.pathname.includes('/artifacts/')) {
      expect(route.request().headers().authorization).toBe('Bearer test-token')
      return route.fulfill({ body: Buffer.from('PK-fixture'), contentType: 'application/octet-stream' })
    }
    if (url.pathname === `${base}/runs`) {
      if (route.request().method() === 'GET') return route.fulfill({ json: { items: [...runs.values()].filter(r => r.kind === 'policy').reverse(), total: requests.length } })
      const body = route.request().postDataJSON(); requests.push(body)
      const id = `run-${requests.length}`
      const run = { id, kind: 'policy', status: state.pending ? 'queued' : state.fail ? 'failed' : 'completed', input: body,
        error: state.fail ? '申请表第 2 行：没有拓扑路径' : undefined,
        result: { success: true, manifest, policy_plan: { ...plan, include_business_paths: body.include_business_paths === true,
          requests: plan.requests.map(row => ({ ...row, source_system: '订单系统', destination_system: '支付系统' })) } },
        artifacts: Object.fromEntries(['config_zip', 'policy_preview', 'policy_request'].map(key => [key, { filename: key, download_url: `${base}/runs/${id}/artifacts/${key}` }])), tickets: [] }
      runs.set(id, run)
      return route.fulfill({ status: 202, json: run })
    }
    const match = url.pathname.match(/\/runs\/([^/]+)(?:\/(tickets|cancel|retry))?$/)
    if (match) {
      const run = runs.get(match[1])!
      if (match[2] === 'tickets') {
        const body = route.request().postDataJSON(); ticketRequests.push(body)
        const id = `ticket-${ticketRequests.length}`
        const child = { id, kind: 'ticket', parent_id: run.id, status: state.ticketFail ? 'failed' : 'completed', input: body,
          error: state.ticketFail ? '工单文件保存失败' : undefined,
          artifacts: { change_excel: { filename: '变更工单.xlsx', download_url: `${base}/runs/${id}/artifacts/change_excel` } } }
        runs.set(id, child); run.tickets.unshift(child)
        return route.fulfill({ status: 202, json: child })
      }
      if (match[2] === 'cancel') run.status = 'cancelled'
      if (match[2] === 'retry') {
        const next = { ...run, id: `${run.id}-retry`, status: 'completed' }
        runs.set(next.id, next)
        return route.fulfill({ status: 202, json: next })
      }
      return route.fulfill({ json: run })
    }
    return route.fulfill({ json: { items: [], unread_count: 0 } })
  })
  return { errors, requests, uploads, state, ticketRequests, runs }
}
const upload = async (page: Page) => {
  await page.locator('input[type=file]').first().setInputFiles({ name: '申请.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('fixture-workbook') })
  await page.getByLabel('工单号', { exact: true }).fill('CHG-001')
}

test('optional business paths show original system pairs and linked device rules', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByText('更多选项', { exact: false }).click()
  const toggle = page.getByRole('switch', { name: '业务系统与拓扑路径说明' })
  await expect(toggle).not.toBeChecked()
  await toggle.click()
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  const business = page.getByRole('region', { name: '业务系统与拓扑路径', exact: true })
  await expect(business.getByText('订单系统', { exact: true })).toBeVisible()
  await expect(business.getByText('支付系统', { exact: true })).toBeVisible()
  await expect(business.getByText('FW-A / Trust → FW-A / Untrust → FW-B / Trust')).toBeVisible()
  await business.getByRole('button', { name: '展开行' }).click()
  await expect(business.getByText('FW-A · Trust → Untrust · TCP 443', { exact: true })).toBeVisible()
  await expect(business.getByText('FW-B · Trust → Untrust · TCP 443', { exact: true })).toBeVisible()
  await expect(business).not.toContainText('rule-0')
  await expect(business).not.toContainText('rule-1')
  expect(state.requests[0].include_business_paths).toBe(true)
  await page.getByRole('button', { name: '修改申请', exact: true }).click()
  await page.getByText('更多选项', { exact: false }).click()
  await toggle.click()
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(business).toHaveCount(0)
  expect(state.requests[1].include_business_paths).toBe(false)
  expect(state.errors).toEqual([])
})

test('workbench launches the app and user can inspect sources, commands and downloads', async ({ page }, info) => {
  const state = await setup(page)
  await page.goto('/workbench')
  await page.getByRole('button', { name: '防火墙策略生成', exact: true }).dblclick()
  await expect(page).toHaveURL(/apps\/firewall-policy/)
  await expect(page.getByRole('button', { name: '生成策略', exact: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: '历史策略任务' })).toBeVisible()
  const screenshotDir = path.resolve('../../outputs/firewall-ui')
  fs.mkdirSync(screenshotDir, { recursive: true })
  await page.screenshot({ path: path.join(screenshotDir, `${info.project.name}-new.png`), fullPage: true })
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
  expect(state.requests[0]).toMatchObject({ ticket_id: 'CHG-001', merge_policies: true, policy_upload_id: 'a'.repeat(32) })
  await expect(page.getByText('3 → 2 条 · 合并等价校验通过')).toBeVisible()
  await page.getByRole('button', { name: '展开行' }).click()
  await expect(page.getByText('申请表第 2 行', { exact: true })).toBeVisible()
  await expect(page.getByText('FW-A / Trust · 10.0.0.0/24')).toBeVisible()
  await page.screenshot({ path: path.join(screenshotDir, `${info.project.name}-result.png`), fullPage: true })
  await page.getByRole('button', { name: /FW-B/ }).click()
  await page.getByText('配置命令', { exact: true }).click()
  await expect(page.getByLabel('设备配置命令')).toContainText('CHG-001-B')
  await expect(page.getByRole('button', { name: '下载策略包' })).toBeEnabled()
  await expect(page.getByRole('button', { name: '下载申请表' })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(state.errors).toEqual([])
})

test('custom options reach generation and failed rerun never shows stale success', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByText('更多选项', { exact: false }).click()
  await page.getByRole('switch', { name: '合并等价策略' }).click()
  await page.locator('input[type=file]').nth(1).setInputFiles({ name: '拓扑.json', mimeType: 'application/json', buffer: Buffer.from('{}') })
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
  expect(state.requests[0]).toMatchObject({ merge_policies: false, topology_upload_id: 'b'.repeat(32) })
  expect(state.uploads).toHaveLength(2)
  await page.getByRole('button', { name: '修改申请' }).click()
  state.state.fail = true
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('申请表第 2 行：没有拓扑路径')
  await expect(page.getByRole('button', { name: '下载策略包' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '返回上次生成结果' })).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('invalid files are rejected before any request and the empty template is downloadable', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await page.locator('input[type=file]').first().setInputFiles({ name: '申请.xls', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('bad') })
  await expect(page.getByRole('alert')).toContainText('.xlsx')
  expect(state.uploads).toHaveLength(0)
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载导入模板' }).click()
  expect((await downloading).suggestedFilename()).toBe('policies.xlsx')
})

test('ticket export uses all current devices and preserves rollback while updating header fields', async ({ page }, info) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await page.getByRole('button', { name: '生成工单', exact: true }).click()
  await page.getByLabel('变更标题', { exact: true }).fill('核心业务访问开通')
  await page.getByLabel('变更负责人', { exact: true }).fill('张工')
  await page.getByLabel('计划时间', { exact: true }).fill('2026-09-10T22:00')
  await page.getByLabel('变更背景', { exact: true }).fill('新增业务系统接入')
  await page.getByLabel('变更目的', { exact: true }).fill('允许业务访问指定服务')
  await page.getByText('申请与评审信息（选填）', { exact: true }).click()
  await page.getByLabel('申请人', { exact: true }).fill('李工')
  await page.screenshot({ path: path.resolve(`../../outputs/firewall-ui/${info.project.name}-ticket.png`), animations: 'disabled', fullPage: true })
  await page.getByRole('button', { name: '生成 Excel', exact: true }).click()
  await expect(page.getByRole('button', { name: '下载工单 Excel' })).toBeEnabled()
  expect(state.ticketRequests[0]).toMatchObject({ fields: {
    ticket_title: '核心业务访问开通', assignee: '张工', requester: '李工', due_date: '2026-09-10T22:00',
    change_background: '新增业务系统接入', change_purpose: '允许业务访问指定服务',
  } })
  expect(state.ticketRequests[0]).not.toHaveProperty('manifest')
  await page.reload()
  await expect(page.getByRole('button', { name: '下载工单 Excel' })).toBeEnabled()
  await page.getByRole('button', { name: '修改申请', exact: true }).click()
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page.getByRole('button', { name: '生成工单', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '下载工单 Excel' })).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('ticket title validation and failed regeneration cannot expose an old download', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await page.getByRole('button', { name: '生成工单', exact: true }).click()
  await page.getByLabel('变更标题', { exact: true }).fill('')
  await page.getByRole('button', { name: '生成 Excel', exact: true }).click()
  await expect(page.getByText('请填写变更标题')).toBeVisible()
  expect(state.ticketRequests).toHaveLength(0)
  await page.getByLabel('变更标题', { exact: true }).fill('有效标题')
  await page.getByRole('button', { name: '生成 Excel', exact: true }).click()
  await page.getByRole('button', { name: '修改工单信息' }).click()
  state.state.ticketFail = true
  await page.getByRole('button', { name: '生成 Excel', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('工单文件保存失败')
  await expect(page.getByRole('button', { name: '下载工单 Excel' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
  expect(state.errors).toEqual([])
})

test('workflow navigation opens the current ticket', async ({ page }) => {
  test.setTimeout(60000)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await setup(page)
  await page.goto('/apps/firewall-policy')
  await expect(page.getByRole('heading', { name: '新建策略' })).toBeVisible()
  const audit = await new AxeBuilder({ page }).include('.fw-window').withTags(['wcag2a', 'wcag2aa']).analyze()
  expect(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }))).toEqual([])
  const flow = page.getByRole('navigation', { name: '策略生成步骤' })
  await expect(flow.getByRole('button', { name: /核对策略/ })).toBeDisabled()
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await flow.getByRole('button', { name: /导入申请/ }).click()
  await expect(page.getByRole('heading', { name: '新建策略' })).toBeVisible()
  await flow.getByRole('button', { name: /变更工单/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: /取\s*消/ }).click()
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
})

test('pending runs survive refresh, cancellation and retry', async ({ page }) => {
  const state = await setup(page)
  state.state.pending = true
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page).toHaveURL(/run=run-1/)
  await page.reload()
  await expect(page.getByRole('button', { name: '取消任务', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await page.getByRole('button', { name: '重试原任务' }).click()
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
  await expect(page).toHaveURL(/run=run-1-retry/)
  expect(state.requests).toHaveLength(1)
})

test('downloads use current App authorization and fail after revocation', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/firewall-policy')
  await upload(page)
  await page.getByRole('button', { name: '生成策略', exact: true }).click()
  await expect(page.getByRole('heading', { name: '核对生成结果' })).toBeVisible()
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载策略包' }).click()
  expect((await downloading).suggestedFilename()).toBe('config_zip')
  state.state.revoked = true
  await page.getByRole('button', { name: '下载策略包' }).click()
  await expect(page.getByText('应用权限已撤销').first()).toBeVisible()
})
