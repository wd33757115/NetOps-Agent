import { expect, test, type Page } from '@playwright/test'

const account = { id: 1, username: 'admin', role: 'admin', email: '' }
const job = { id: 'patrol-history', purpose: 'offline_patrol', job_type: 'single_patrol', report_intent: 'historical',
  name: '核心交换机日检', status: 'completed', created_at: '2026-09-03T00:00:00Z', preview_totals: { device_count: 1, file_count: 1, command_count: 2 },
  blocker_count: 0, warning_count: 1, progress: {}, archived: false,
  periods: [{ id: 'period-1', role: 'current', label: '采集期', observed_at: '2026-09-03T00:00:00Z', dataset_id: 'dataset-1' }],
  source_files: [{ id: 'source-file-1', period_role: 'current', file_id: 'file-1', relative_path: 'switch.log', size_bytes: 50, status: 'ready' }],
}
const report = { title: '历史分析', generated_at: '2026-09-05T00:00:00Z',
  assessment: { code: 'partial', status: '结论不完整', basis: '接口检查缺少规则', trust: '中', conclusion_allowed: false },
  metrics: { parser_ready_count: 1, parser_eligible_count: 2, parser_ready_rate: 0.5, template_missing_count: 1 },
  risk_summary: { total: 0 }, event_summary: { fact_count: 0 }, risks: [], correlated_evidence: [], data_quality: { status: 'partial' }, evidence: {},
}

async function setup(page: Page) {
  const errors: string[] = []
  const writes: Array<{ path: string; body: any }> = []
  let sources: typeof job.source_files = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(user => { localStorage.setItem('access_token', 'fixture-token'); localStorage.setItem('auth_user', JSON.stringify(user)) }, account)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'POST') writes.push({ path, body: route.request().postDataJSON() })
    if (path.endsWith('/auth/me/')) return route.fulfill({ json: account })
    if (path.endsWith('/devices/patrol/readiness/')) return route.fulfill({ json: { status: 'ready', can_launch: true, checked_at: '2026-09-05T00:00:00Z', blocking_count: 0, warning_count: 0,
      checks: [{ id: 'postgres', name: 'PostgreSQL', status: 'ok', message: '已连接' }] } })
    if (path.endsWith('/patrol/offline-jobs/') && route.request().method() === 'POST') return route.fulfill({ json: { id: 'new-analysis' } })
    if (path.endsWith('/patrol/offline-jobs/new-analysis/sources/')) {
      sources = [...sources, { ...route.request().postDataJSON(), id: 'copy-1', status: 'ready' }]
      return route.fulfill({ json: sources.at(-1) })
    }
    if (path.endsWith('/patrol/offline-jobs/new-analysis/')) return route.fulfill({ json: { ...job, id: 'new-analysis', name: '复检 · 核心交换机日检', status: 'uploading', source_files: sources } })
    if (path.endsWith('/patrol/offline-jobs/')) return route.fulfill({ json: { items: [job], total: 1 } })
    if (path.endsWith('/patrol/offline-jobs/patrol-history/')) return route.fulfill({ json: job })
    if (path.endsWith('/replay-summary/')) return route.fulfill({ json: path.includes('validation-job')
      ? { ...report, assessment: { ...report.assessment, code: 'rule_replay_only', status: '规则样本验证，不形成网络健康结论' } } : report })
    if (path.endsWith('/parser/asset-source-jobs/') && route.request().method() === 'POST') return route.fulfill({ json: { id: 'reused-source' } })
    if (path.endsWith('/parser/asset-source-jobs/reused-source/sources/')) {
      sources = [...sources, { ...route.request().postDataJSON(), id: 'attached-1', status: 'ready' }]
      return route.fulfill({ json: sources.at(-1) })
    }
    if (path.endsWith('/parser/asset-source-jobs/reused-source/')) return route.fulfill({ json: { ...job, id: 'reused-source', purpose: 'rule_asset_source', job_type: 'parser_validation', status: 'draft', source_files: sources } })
    if (path.endsWith('/parser/asset-source-jobs/validation-job/')) return route.fulfill({ json: { ...job, id: 'validation-job', purpose: 'rule_asset_source', job_type: 'history_compare' } })
    return route.fulfill({ json: { items: [], total: 0, conversations: [], unread_count: 0 } })
  })
  return { errors, writes }
}

test('new inspection chooses the source and keeps healthy infrastructure collapsed', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/patrol')
  await expect(page.getByRole('tab', { name: '全部任务' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('button', { name: /新建巡检/ }).click()
  await page.getByRole('button', { name: '在线采集 · 从设备读取数据' }).click()
  await expect(page.getByText('巡检运行环境已就绪')).toBeVisible()
  await expect(page.getByText('PostgreSQL', { exact: true })).not.toBeVisible()
  await expect(page.getByRole('button', { name: /发起正式巡检/ })).toBeDisabled()
  await expect(page.getByRole('button', { name: '配置命令集', exact: true })).toBeVisible()
  await page.getByRole('dialog', { name: '发起正式巡检', exact: true }).getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('button', { name: /新建巡检/ }).click()
  await page.getByRole('button', { name: '离线文件 · 单周期检查或双周期对比' }).click()
  await expect(page.getByRole('dialog', { name: '新建离线巡检任务' })).toBeVisible()
  await expect(page.getByLabel('巡检采集时间')).toBeVisible()
  expect(state.writes).toEqual([])
  expect(state.errors).toEqual([])
})

test('historical inspection opens results and reuses evidence in an idempotent rule task', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/patrol')
  await page.getByRole('button', { name: '查看结果', exact: true }).click()
  await expect(page.getByRole('tab', { name: '巡检结果', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('离线证据 · 历史分析')).toBeVisible()
  await expect(page.getByText('尚不能形成完整风险结论，请查看覆盖缺口和证据')).toBeVisible()
  await page.getByRole('button', { name: /补齐检查能力/ }).click()
  await expect(page.getByRole('heading', { name: '复用巡检样本' })).toBeVisible()
  await page.getByRole('button', { name: '创建或继续制作任务' }).click()
  await expect(page).toHaveURL(/source-jobs\/reused-source\?tab=overview/)
  expect(state.writes[0].body).toMatchObject({ idempotency_key: 'patrol-rule-source:patrol-history', publish_events: false, report_intent: 'historical', job_type: 'parser_validation' })
  expect(state.writes[1].body).toMatchObject({ file_id: 'file-1', period_role: 'sample' })
  await page.getByRole('button', { name: '返回原巡检', exact: true }).click()
  await expect(page).toHaveURL(/offline_job_id=patrol-history/)
  expect(state.errors).toEqual([])
})

test('validation reports never show a healthy-device empty state', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/patrol-rules/source-jobs/validation-job?tab=result')
  await expect(page.getByRole('button', { name: /下载验证报告/ })).toBeVisible()
  await expect(page.getByText('分析期风险', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/未发现.*风险/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: /下载巡检报告/ })).toHaveCount(0)
  await page.screenshot({ path: `test-results/rule-validation-${test.info().project.name}.png`, fullPage: true })
  expect(state.errors).toEqual([])
})

test('reanalysis creates a separate historical task and repeats preflight', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/apps/patrol')
  await expect(page.getByRole('button', { name: '查看结果', exact: true })).toBeVisible()
  await page.screenshot({ path: `test-results/patrol-tasks-${test.info().project.name}.png`, fullPage: true })
  await page.getByRole('button', { name: '查看结果', exact: true }).click()
  await page.getByRole('button', { name: '复用文件新建分析', exact: true }).last().click()
  await expect(page.getByRole('dialog', { name: '继续离线巡检任务' })).toBeVisible()
  await expect(page.getByText('switch.log', { exact: true }).last()).toBeVisible()
  await expect(page.getByRole('dialog', { name: '继续离线巡检任务' }).getByRole('button', { name: /执行巡检/ })).toBeDisabled()
  expect(state.writes).toHaveLength(2)
  expect(state.writes[0].body).toMatchObject({ name: '复检 · 核心交换机日检', report_intent: 'historical', publish_events: false,
    periods: [{ role: 'current', label: '采集期', observed_at: '2026-09-03T00:00:00Z' }] })
  expect(state.writes[0].body.idempotency_key).toMatch(/^patrol-reanalysis:patrol-history:/)
  expect(state.writes[1].path).toContain('/new-analysis/sources/')
  expect(state.errors).toEqual([])
})
