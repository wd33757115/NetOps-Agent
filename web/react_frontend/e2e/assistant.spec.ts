import { test, expect, type Page } from '@playwright/test'

async function prepare(page: Page, allowed: boolean) {
  const user = { id: 83, username: '验证用户', role: 'viewer', app_ids: allowed ? ['assistant'] : [] }
  const reference = { id: 'doc-demo', title_path: ['接口检查'], revision: 'a'.repeat(64), excerpt: '旧修订：执行 display interface 检查接口。', start_line: 2, end_line: 4 }
  let answered = false
  await page.addInitScript(user => {
    localStorage.setItem('access_token', 'assistant-test')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, user)
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const ok = (data: unknown) => route.fulfill({ json: { success: true, data, error: null } })
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (path.endsWith('/assistant/documents')) return ok([{ id: 'manual', title: '接口手册', revision: 'b'.repeat(64), sections: 2, metadata: { software_version: '8.2.1' } }])
    if (path.endsWith('/assistant/conversations')) return ok(request.method() === 'POST' ? { id: 'chat', title: '接口检查' } : [{ id: 'chat', title: '接口检查' }])
    if (path.endsWith('/questions')) { answered = true; return ok({ id: 'turn', status: 'completed' }) }
    if (path.includes('/citations/')) return ok(reference)
    if (path.endsWith('/conversations/chat')) return ok({ messages: answered ? [{ id: 'm', role: 'assistant', content: '请根据手册检查接口状态。', references: [reference] }] : [], latest_turn: answered ? { id: 'turn', status: 'completed' } : null })
    return ok({ items: [], total: 0 })
  })
}

test('assistant answers and opens the cited revision', async ({ page }) => {
  await prepare(page, true)
  await page.goto('/apps/assistant')
  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByLabel('问题', { exact: true }).fill('如何检查接口？')
  await page.getByLabel('发送', { exact: true }).click()
  await expect(page.getByText('请根据手册检查接口状态。')).toBeVisible()
  await page.getByRole('button', { name: '接口检查', exact: true }).click()
  await expect(page.getByText('旧修订：执行 display interface 检查接口。')).toBeVisible()
  await expect.poll(() => page.locator('.ant-drawer-content-wrapper').evaluate(el => getComputedStyle(el).transform)).toBe('none')
  await page.screenshot({ path: `test-results/assistant-${test.info().project.name}.png`, fullPage: true })
})

test('assistant route requires application permission', async ({ page }) => {
  await prepare(page, false)
  await page.goto('/apps/assistant')
  await expect(page.getByText('应用未启用或你没有使用权限')).toBeVisible()
})
