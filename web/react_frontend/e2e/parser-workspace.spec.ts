import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import type { ParserRuleGenerationJob, ParserTemplateItem } from '../src/services/api'

const admin = {
  id: 1,
  username: 'admin',
  email: 'admin@example.test',
  role: 'admin',
}

async function authenticatedParserPage(
  page: Page,
  ruleGenerationJobs: ParserRuleGenerationJob[] = [],
  templates: ParserTemplateItem[] = [],
) {
  const requests: string[] = []
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'e2e-access-token')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, admin)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    requests.push(url.pathname)
    if (url.pathname.endsWith('/auth/me/')) {
      await route.fulfill({ json: admin })
      return
    }
    if (url.pathname.endsWith('/parser/coverage/')) {
      await route.fulfill({
        json: {
          templates_total: 0,
          templates_active: 0,
          jobs_pending: 0,
          missing_candidates: 0,
          repair_candidates: 0,
          production_open_jobs: 0,
          pending_sanitization_samples: 0,
          sanitized_samples: 0,
          backfills_pending_approval: 0,
          backfills_failed: 0,
          alerts: [],
        },
      })
      return
    }
    if (url.pathname.endsWith('/parser/dispatch-control/')) {
      await route.fulfill({ json: { auto_dispatch_enabled: false } })
      return
    }
    if (url.pathname.endsWith('/parser/runtime-assets/resolve/')) {
      await route.fulfill({
        json: {
          ready: true,
          observation_contracts: {},
          change_policies: {},
          event_rules: [],
          missing_observation_contracts: [],
          missing_change_policies: [],
        },
      })
      return
    }
    if (url.pathname.endsWith('/parser/rule-generation-jobs/')) {
      await route.fulfill({ json: { items: ruleGenerationJobs } })
      return
    }
    if (url.pathname.endsWith('/parser/templates/')) {
      await route.fulfill({ json: { items: templates } })
      return
    }
    if (url.pathname.endsWith('/devices/')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (url.pathname.endsWith('/workflows/runs/')) {
      await route.fulfill({ json: [] })
      return
    }
    if (url.pathname.endsWith('/workflows/runs/patrol-batches/')) {
      await route.fulfill({ json: { items: [] } })
      return
    }
    await route.fulfill({ json: { items: [] } })
  })
  await page.goto('/skills/parser')
  await expect(page).toHaveURL(/\/skills\/parser$/)
  await expect(page.getByRole('tab', { name: /今日待办/ })).toBeVisible()
  return requests
}

test('loads Parser workspaces on demand without page overflow', async ({ page }) => {
  const requests = await authenticatedParserPage(page)

  await expect.poll(() => requests.some((path) => path.endsWith('/parser/jobs/'))).toBe(true)
  expect(requests.some((path) => path.endsWith('/parser/templates/'))).toBe(false)
  expect(requests.some((path) => path.endsWith('/parser/observation-contracts/'))).toBe(false)

  await page.getByRole('tab', { name: 'Parser 资产', exact: true }).click()
  await expect.poll(() => requests.some((path) => path.endsWith('/parser/templates/'))).toBe(true)
  expect(requests.some((path) => path.endsWith('/parser/observation-contracts/'))).toBe(false)

  await page.getByRole('tab', { name: '规则资产', exact: true }).click()
  await expect.poll(() =>
    requests.some((path) => path.endsWith('/parser/observation-contracts/')),
  ).toBe(true)

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(pageOverflow).toBe(false)
})

test('keeps the Parser control plane free of detectable accessibility violations', async ({ page }) => {
  await authenticatedParserPage(page)

  const results = await new AxeBuilder({ page })
    .include('main')
    .analyze()

  expect(results.violations).toEqual([])
})

test('hard-blocks approval when historical replay reports collisions', async ({ page }) => {
  await authenticatedParserPage(page, [{
    id: 'rule-job-collision-1',
    job_type: 'EVENT_RULE',
    status: 'pending_approval',
    source_ref: 'parser_template:interface-status-v1',
    asset_ref: 'event-rule-interface-down:v1',
    asset_type: 'EVENT_RULE',
    asset_id: 'event-rule-interface-down',
    asset_version_id: 'event-rule-interface-down-v1',
    asset_digest: 'collision-digest',
    payload_json: {},
    validation_report_json: {
      success: true,
      synthetic_validation: { success: true },
      historical_replay: {
        success: true,
        matched_change_count: 3,
        collision_count: 2,
      },
    },
    approval_required: true,
    created_by: 'llm-rule-generator',
    updated_at: '2026-07-12T10:00:00Z',
  }])

  await page.getByRole('tab', { name: '规则资产任务 (1)', exact: true }).click()
  await expect(page.getByText('审批阻断：历史回放存在碰撞')).toBeVisible()

  const approve = page.getByRole('button', { name: /审批激活$/ })
  await expect(approve).toBeDisabled()
  await expect(approve).toHaveAttribute('title', '历史回放存在碰撞')
})

test('resolves an evidence deep link to the focused Parser asset', async ({ page }) => {
  const template: ParserTemplateItem = {
    id: 'parser-template-interface-status-v1',
    vendor: 'Cisco',
    profile: 'IOS',
    canonical_command: 'show interface status',
    category: 'interface',
    version: '1',
    status: 'active',
    created_at: '2026-07-12T10:00:00Z',
  }
  await authenticatedParserPage(page, [], [template])

  await page.goto(
    '/skills/parser?focus=parser-template&id=parser-template-interface-status-v1&workspace=parser-assets&subtab=templates',
  )

  await expect(page.getByRole('tab', { name: 'Parser 资产', exact: true }))
    .toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('正在定位 parser-template')).toBeVisible()
  const focusedRow = page.getByRole('row', { name: /Cisco IOS show interface status/ })
  await expect(focusedRow).toHaveClass(/ant-table-row-selected/)
})

test('normalizes conflicting deep-link parameters to the canonical workspace', async ({ page }) => {
  await authenticatedParserPage(page)

  await page.goto(
    '/skills/parser?focus=event-rule&id=rule-interface-down&workspace=parser-assets&subtab=templates',
  )

  await expect(page.getByText('深链参数已安全回退')).toBeVisible()
  await expect(page.getByRole('tab', { name: '规则资产', exact: true }))
    .toHaveAttribute('aria-selected', 'true')
  await page.getByRole('button', { name: '使用推荐视图' }).click()
  await expect(page).toHaveURL(/workspace=rule-assets/)
  await expect(page).toHaveURL(/subtab=event-rules/)
  await expect(page).not.toHaveURL(/focus=/)
  await expect(page.getByText('深链参数已安全回退')).not.toBeVisible()
})

test('navigates between Devices and the canonical Parser control plane', async ({ page }) => {
  await authenticatedParserPage(page)
  const isMobile = (page.viewportSize()?.width || 0) < 768

  if (isMobile) await page.getByRole('button', { name: '打开菜单' }).click()
  await page.getByRole('button', { name: /Devices$/ }).click()
  await expect(page).toHaveURL(/\/devices$/)

  if (isMobile) await page.getByRole('button', { name: '打开菜单' }).click()
  await page.getByRole('button', { name: /Skills$/ }).click()
  await expect(page).toHaveURL(/\/skills$/)

  await page.goto('/skills/parser')
  await expect(page).toHaveURL(/\/skills\/parser$/)
})
