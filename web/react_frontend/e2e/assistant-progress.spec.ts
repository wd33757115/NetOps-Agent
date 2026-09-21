import { test, expect } from '@playwright/test'

test('chat restores live calls, stops, and retains the completed process on reload', async ({ page }) => {
  const user = { id: 83, username: '验证用户', role: 'viewer', app_ids: ['assistant'] }
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const started = new Date(Date.now() - 4000).toISOString()
  const progress = { run_id: 'turn', status: 'running', message: '正在综合资料与工具结果', started_at: started,
    ended_at: undefined as string | undefined,
    steps: [
      { id: 'glob', label: '查找资料文件', detail: '', status: 'completed', started_at: started, ended_at: started },
      { id: 'read', label: '阅读原文', detail: '华为云 Stack 8.2.1 / BGP EVPN / RR 配置', status: 'completed', started_at: started, ended_at: started },
    ] }
  let stopped = false
  await page.addInitScript(user => {
    localStorage.setItem('access_token', 'assistant-progress-test')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, user)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const ok = (data: unknown) => route.fulfill({ json: { success: true, data, error: null } })
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: user })
    if (path === '/api/conversations/') return ok([{ id: 'chat', title: 'BGP EVPN 配置要求', message_count: stopped ? 2 : 1 }])
    if (path.endsWith('/turns/turn/cancel')) {
      stopped = true
      progress.status = 'cancelled'; progress.message = '已停止本次问答'; progress.ended_at = new Date().toISOString()
      return ok({ progress })
    }
    if (path.endsWith('/assistant/conversations/chat')) return ok({
      messages: [{ id: 'q', role: 'user', content: '查找资料中关于 BGP EVPN 的配置要求', references: [] },
        ...(stopped ? [{ id: 'a', role: 'assistant', content: '本次问答未完成，没有提交业务任务。', references: [], progress }] : [])],
      latest_turn: { id: 'turn', status: progress.status, result_message_id: stopped ? 'a' : null, progress },
    })
    return ok({ items: [], total: 0 })
  })
  await page.goto('/chat')
  await expect(page.getByText('华为云 Stack 8.2.1 / BGP EVPN / RR 配置')).toBeVisible()
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled()
  await page.screenshot({ path: `test-results/assistant-progress-${test.info().project.name}.png`, fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: '停止', exact: true }).click()
  await expect(page.getByText('已停止本次问答')).toBeVisible()
  await page.reload()
  await expect(page.getByText('已停止本次问答')).toBeVisible()
  await page.getByRole('button', { expanded: false, name: /已停止本次问答/ }).click()
  await expect(page.getByText('华为云 Stack 8.2.1 / BGP EVPN / RR 配置')).toBeVisible()
  expect(errors).toEqual([])
})
