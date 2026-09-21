import { test, expect, type Page } from '@playwright/test'

async function prepare(page: Page, allowed: boolean) {
  const user = { id: 81, username: '验证用户', role: 'viewer', app_ids: allowed ? ['delivery-probe'] : [] }
  await page.addInitScript(user => {
    localStorage.setItem('access_token', 'delivery-test')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, user)
  let submitted = false
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const ok = (data: unknown) => route.fulfill({ json: { success: true, data, error: null } })
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (path.includes('/apps/delivery-probe/runs')) {
      if (request.method() === 'POST') {
        expect(request.postDataJSON().text).toBe('NetOps copied package')
        submitted = true
        return ok({ id: 'run-1', text: 'NetOps copied package', status: 'queued', result: null })
      }
      return ok({ id: 'run-1', text: 'NetOps copied package', status: 'completed', result: 'NETOPS COPIED PACKAGE' })
    }
    return ok({ items: [], total: 0 })
  })
  return () => submitted
}

test('copied package route submits and reopens its durable result', async ({ page }) => {
  const submitted = await prepare(page, true)
  await page.goto('/apps/delivery-probe')
  await page.getByLabel('验证文本').fill('NetOps copied package')
  await page.getByRole('button', { name: '提交验证' }).click()
  await expect(page.getByText('结果：NETOPS COPIED PACKAGE')).toBeVisible()
  expect(submitted()).toBe(true)
  await page.reload()
  await expect(page.getByText('结果：NETOPS COPIED PACKAGE')).toBeVisible()
  await page.screenshot({ path: 'test-results/package-delivery.png', fullPage: true })
})

test('copied package remains inaccessible without an App grant', async ({ page }) => {
  const submitted = await prepare(page, false)
  await page.goto('/apps/delivery-probe')
  await expect(page.getByText('应用未启用或你没有使用权限')).toBeVisible()
  expect(submitted()).toBe(false)
})
