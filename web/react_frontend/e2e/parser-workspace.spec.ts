import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import type { ParserTemplateItem } from '../src/services/api'

const admin = {
  id: 1,
  username: 'admin',
  email: 'admin@example.test',
  role: 'admin',
}

interface ParserPageFixtures {
  templates?: ParserTemplateItem[]
  runtimeHealth?: Record<string, unknown>
  runtimeAssembly?: Record<string, unknown>
}

const emptyRuntimeHealth = {
  items: [],
  page: 1,
  page_size: 25,
  total: 0,
  page_count: 0,
  summary: {
    scope: 'active_catalog',
    catalog_version: 1,
    check_item_count: 0,
    implementation_pair_count: 0,
    ready_count: 0,
    missing_count: 0,
    invalid_count: 0,
    ready_rate: 0,
    ready: true,
    status_counts: {},
  },
}

async function authenticatedParserPage(
  page: Page,
  fixtures: ParserPageFixtures = {},
) {
  const requests: string[] = []
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'e2e-access-token')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, admin)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    requests.push(path)

    if (path.endsWith('/auth/me/')) {
      await route.fulfill({ json: admin })
      return
    }
    if (path.endsWith('/parser/check-items/')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } })
      return
    }
    if (path.endsWith('/parser/coverage/')) {
      await route.fulfill({
        json: {
          templates_total: fixtures.templates?.length || 0,
          templates_active: fixtures.templates?.length || 0,
          jobs_total: 0,
          jobs_pending: 0,
          missing_candidates: 0,
          repair_candidates: 0,
          production_open_jobs: 0,
          pending_sanitization_samples: 0,
          sanitized_samples: 0,
          sanitization_failed_samples: 0,
          backfills_pending_approval: 0,
          backfills_failed: 0,
          alerts: [],
        },
      })
      return
    }
    if (path.endsWith('/parser/check-item-runtime-bundles/health/')) {
      await route.fulfill({ json: fixtures.runtimeHealth || emptyRuntimeHealth })
      return
    }
    if (path.endsWith('/parser/check-item-runtime-bundles/assembly-preview/')) {
      await route.fulfill({ json: fixtures.runtimeAssembly || {} })
      return
    }
    if (path.endsWith('/parser/templates/')) {
      const items = fixtures.templates || []
      await route.fulfill({
        json: {
          items,
          total: items.length,
          limit: Number(url.searchParams.get('limit') || 25),
          offset: Number(url.searchParams.get('offset') || 0),
        },
      })
      return
    }
    if (
      path.endsWith('/parser/observation-contracts/')
      || path.endsWith('/parser/change-policies/')
      || path.endsWith('/parser/event-rules/')
    ) {
      await route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } })
      return
    }
    if (path.endsWith('/parser/approval-audit/')) {
      await route.fulfill({
        json: {
          items: [],
          total: 0,
          limit: 25,
          offset: 0,
          summary: { total: 0, approved: 0, rejected: 0, deprecated: 0 },
        },
      })
      return
    }
    if (path.endsWith('/parser/dispatch-control/')) {
      await route.fulfill({ json: { auto_dispatch_enabled: false } })
      return
    }
    if (path.endsWith('/parser/runtime-assets/resolve/')) {
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
    if (path.endsWith('/devices/')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (path.endsWith('/workflows/runs/')) {
      await route.fulfill({ json: [] })
      return
    }
    if (path.endsWith('/workflows/runs/patrol-batches/')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } })
      return
    }
    await route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } })
  })

  await page.goto('/skills/parser')
  await expect(page).toHaveURL(/\/apps\/patrol-rules$/)
  await expect(page.getByText('巡检规则工作台', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '制作工作台' }))
    .toHaveAttribute('aria-current', 'page')
  return requests
}

test('loads product workspaces on demand without page overflow', async ({ page }) => {
  const requests = await authenticatedParserPage(page)

  await expect.poll(
    () => requests.some((path) => path.endsWith('/parser/asset-production/check-items/')),
  ).toBe(true)
  // The task inbox loads its persisted production groups; catalog and expert work remain lazy.
  expect(requests.some((path) => path.endsWith('/parser/check-items/'))).toBe(false)
  expect(requests.some((path) => path.includes('/parser/asset-production/') && !path.endsWith('/parser/asset-production/check-items/'))).toBe(false)
  expect(requests.some((path) => path.endsWith('/parser/approval-audit/'))).toBe(false)

  await page.getByRole('button', { name: '已发布资产' }).click()
  await expect(page.getByRole('heading', { name: '生产资产目录' })).toBeVisible()
  await expect.poll(() =>
    requests.some((path) => path.endsWith('/parser/templates/')),
  ).toBe(true)
  await expect.poll(() =>
    requests.some((path) => path.endsWith('/parser/observation-contracts/')),
  ).toBe(true)

  await page.getByRole('button', { name: '覆盖与回填' }).click()
  await expect(page.getByRole('heading', { name: '覆盖与历史回填' })).toBeVisible()
  await expect.poll(() =>
    requests.some((path) => path.endsWith('/parser/check-item-runtime-bundles/health/')),
  ).toBe(true)

  await page.getByRole('button', { name: '审计记录' }).click()
  await expect(page.getByRole('heading', { name: '规则资产审计' })).toBeVisible()
  await expect.poll(() =>
    requests.some((path) => path.endsWith('/parser/approval-audit/')),
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

test('hard-blocks runtime activation when bundle validation has blockers', async ({ page }) => {
  await authenticatedParserPage(page, {
    runtimeHealth: {
      items: [{
        check_item_id: 'interface.oper_status',
        check_item_version_id: 'check-interface-v1',
        check_item_name: '接口运行状态',
        result_mode: 'structured',
        assessment_mode: 'health',
        implementation_id: 'h3c-interface',
        implementation_version_id: 'impl-h3c-interface-v1',
        implementation_name: 'H3C 接口状态',
        commands: ['display interface brief'],
        status: 'missing',
        ready: false,
        blocking_reasons: ['active RuntimeBundle is missing'],
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
      summary: {
        scope: 'active_catalog',
        catalog_version: 1,
        check_item_count: 1,
        implementation_pair_count: 1,
        ready_count: 0,
        missing_count: 1,
        invalid_count: 0,
        ready_rate: 0,
        ready: false,
        status_counts: { missing: 1 },
      },
    },
    runtimeAssembly: {
      schema_version: 1,
      check_item: {
        id: 'interface.oper_status',
        version_id: 'check-interface-v1',
        name: '接口运行状态',
        result_mode: 'structured',
        assessment_mode: 'health',
      },
      implementation: {
        id: 'h3c-interface',
        version_id: 'impl-h3c-interface-v1',
        name: 'H3C 接口状态',
      },
      selected: {
        parser_template_ids: [],
        observation_contract_ids: [],
        change_policy_version_ids: [],
        event_rule_version_ids: [],
        event_rule_bindings: {},
      },
      candidates: {
        parsers: [],
        observation_contracts: [],
        change_policies: [],
        event_rules: [],
        comparison_profiles: [],
      },
      command_routes: [],
      compatibility: {
        expected_observation_types: ['interface.oper_status'],
        produced_observation_types: [],
        observation_projection: [],
        executable_commands: [],
        unavailable_fallbacks: ['display interface brief'],
      },
      active_bundle: null,
      drafts: [],
      operationally_ready: false,
      fallback_resilience: 'none',
      blockers: [{
        code: 'missing_active_contract',
        message: '缺少 Active ObservationContract',
        remediation: '先完成规则资产生成、验证和人工审批。',
      }],
      warnings: [],
      preview_digest: 'blocked-preview',
    },
  })

  await page.getByRole('button', { name: '覆盖与回填' }).click()
  await page.getByRole('button', { name: '校验并装配' }).click()

  await expect(page.getByText('存在 1 个发布阻断项')).toBeVisible()
  await expect(page.getByText('缺少 Active ObservationContract')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存运行包草稿' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '审批激活' })).toBeDisabled()
})

test('resolves an evidence deep link to the focused published Parser asset', async ({ page }) => {
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
  await authenticatedParserPage(page, { templates: [template] })

  await page.goto(
    '/skills/parser?focus=parser-template&id=parser-template-interface-status-v1'
    + '&workspace=parser-assets&subtab=templates',
  )

  await expect(page.getByRole('button', { name: '已发布资产', exact: true }))
    .toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('show interface status', { exact: true }).first()).toBeVisible()

  if ((page.viewportSize()?.width || 0) >= 768) {
    const focusedRow = page.getByRole('row', { name: /show interface status/ })
    await expect(focusedRow).toHaveClass(/ant-table-row-selected/)
  }
})

test('normalizes conflicting deep-link parameters to the canonical product workspace', async ({ page }) => {
  await authenticatedParserPage(page)

  await page.goto(
    '/skills/parser?focus=event-rule&id=rule-interface-down'
    + '&workspace=runtime&subtab=coverage-backfill',
  )

  await expect(page.getByText('已打开兼容视图')).toBeVisible()
  await expect(page.getByRole('button', { name: '已发布资产', exact: true }))
    .toHaveAttribute('aria-current', 'page')
  await page.getByRole('button', { name: '使用推荐视图' }).click()
  await expect(page).toHaveURL(/workspace=inventory/)
  await expect(page).toHaveURL(/subtab=published-assets/)
  await expect(page).not.toHaveURL(/focus=/)
  await expect(page.getByText('已打开兼容视图')).not.toBeVisible()
})

test('navigates between Devices and the canonical Parser control plane', async ({ page }) => {
  await authenticatedParserPage(page)
  const isMobile = (page.viewportSize()?.width || 0) < 768

  await expect(page.getByRole('button', { name: /Devices$/ })).toHaveCount(0)
  await page.goto('/admin/netops/devices')
  await expect(page).toHaveURL(/\/admin\/netops\/devices$/)

  if (isMobile) await page.getByRole('button', { name: '打开菜单' }).click()
  await page.getByRole('button', { name: /Skills$/ }).click()
  await expect(page).toHaveURL(/\/skills$/)

  await page.goto('/skills/parser')
  await expect(page).toHaveURL(/\/apps\/patrol-rules$/)
  await expect(page.getByRole('button', { name: '制作工作台' }))
    .toHaveAttribute('aria-current', 'page')
})
