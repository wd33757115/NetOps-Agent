import { expect, test, type Page } from '@playwright/test'

const admin = {
  id: 1,
  username: 'admin',
  email: 'admin@example.test',
  role: 'admin',
}

const device = {
  device_id: 'device-1',
  device_name: 'core-sw-01',
  management_ip: '10.0.0.1',
  vendor: 'Cisco',
  model: 'C9300-48P',
  site: 'DC-A',
  role: 'core',
  status: 'active',
  last_connection_status: 'connected',
  groups: ['core'],
  patrol_readiness: {
    status: 'ready',
    mode: 'production',
    command_count: 2,
    eligible_count: 2,
    ready_count: 2,
    blocked_count: 0,
    profile_name: 'golden-patrol-interface-optical',
  },
}

const batch = {
  patrol_batch_id: 'batch-incident-1',
  workflow_name: 'patrol-change-event',
  source: 'device-management',
  total: 1,
  status_counts: { failed: 1 },
  failed_count: 1,
  running_count: 0,
  completed_count: 0,
  pending_count: 0,
  cancelled_count: 0,
  sample_devices: ['core-sw-01'],
  target_type: 'group',
  target_label: '设备分组：core',
  selector: { group_ids: ['core'] },
  target_count: 2,
  launched_count: 1,
  skipped_count: 1,
  connection_succeeded_count: 0,
  connection_failed_count: 1,
  connection_pending_count: 0,
  manifest_available: true,
  archived: false,
  created_at: '2026-07-12T09:00:00Z',
}

const failedRun = {
  run_id: 'workflow-run-1',
  template_name: 'patrol-change-event',
  source: 'device-management',
  status: 'failed',
  current_step_index: 5,
  error_message: 'InterfaceDown event requires operator review',
  device_id: device.device_id,
  device_name: device.device_name,
  patrol_batch_id: batch.patrol_batch_id,
  created_at: '2026-07-12T09:00:00Z',
}

const evidenceItem = {
  workflow_run_id: failedRun.run_id,
  workflow_status: 'failed',
  workflow_created_at: '2026-07-12T09:00:00Z',
  workflow_completed_at: '2026-07-12T09:01:00Z',
  execution_plan_id: 'plan-1',
  execution_plan: {
    execution_plan_id: 'plan-1',
    device_id: device.device_id,
    action: 'patrol',
    mode: 'production',
    profile_name: 'golden-patrol-interface-optical',
    policy_source: 'command-profile-binding',
    command_count: 2,
    plan_sha256: 'plan-digest',
    status: 'completed',
  },
  patrol_batch_id: batch.patrol_batch_id,
  snapshot_run_id: 'snapshot-run-1',
  snapshot_count: 1,
  observation_count: 1,
  change_count: 1,
  event_count: 1,
  parse_counts: { parsed: 1 },
  steps: [],
  snapshots: [{
    snapshot_id: 'snapshot-1',
    command: 'show interface status',
    parse_status: 'parsed',
    parser_template_id: 'parser-template-interface-status-v1',
    observation_contract_id: 'contract-interface-status-v1',
    raw_text_hash: '1234567890abcdef',
  }],
  observations: [{
    observation_id: 'observation-1',
    snapshot_id: 'snapshot-1',
    entity_type: 'interface_l2',
    entity_key: 'Gi1/0/1',
    field: 'status',
    observation_type: 'interface.oper_status',
    value: 'down',
    observation_contract_id: 'contract-interface-status-v1',
  }],
  changes: [{
    change_id: 'change-1',
    entity_type: 'interface_l2',
    entity_key: 'Gi1/0/1',
    field: 'status',
    change_type: 'modified',
    observation_type: 'interface.oper_status',
    old_value: 'up',
    new_value: 'down',
    event_status: 'event_created',
    change_policy_id: 'interface-oper-status',
    change_policy_version_id: 'change-policy-interface-v1',
    decision_reason: 'active interface changed from up to down',
  }],
  events: [{
    event_id: 'event-interface-down-1',
    event_type: 'InterfaceDown',
    severity: 'critical',
    entity_type: 'interface_l2',
    entity_key: 'Gi1/0/1',
    status: 'active',
    old_value: 'up',
    new_value: 'down',
    event_rule_id: 'event-rule-interface-down',
    event_rule_version: 1,
    occurred_at: '2026-07-12T09:01:00Z',
  }],
}

async function installAuthenticatedApi(page: Page) {
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'visual-access-token')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, admin)

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname

    if (path.endsWith('/auth/me/')) return route.fulfill({ json: admin })
    if (path.endsWith('/health/')) {
      return route.fulfill({
        json: {
          status: 'healthy',
          services: { postgres: true, redis: true, celery: true, minio: true },
          timestamp: '2026-07-12T10:00:00Z',
        },
      })
    }
    if (path.endsWith('/skills/stats/')) {
      return route.fulfill({
        json: {
          total_skills: 24,
          enabled_skills: 22,
          disabled_skills: 2,
          categories: { network: 12, governance: 6, general: 6 },
        },
      })
    }
    if (path.endsWith('/devices/')) {
      return route.fulfill({ json: { items: [device], total: 1, limit: 200, offset: 0 } })
    }
    if (path.endsWith('/workflows/runs/patrol-batches/')) {
      const limit = Number(url.searchParams.get('limit') || 25)
      const offset = Number(url.searchParams.get('offset') || 0)
      return route.fulfill({
        json: {
          items: [batch],
          total: 30,
          limit,
          offset,
          page: Math.floor(offset / limit) + 1,
          page_size: limit,
          page_count: 2,
        },
      })
    }
    if (path.endsWith('/workflows/runs/')) {
      return route.fulfill({
        json: url.searchParams.get('patrol_batch_id') ? [failedRun] : [],
      })
    }
    if (path.endsWith('/workflows/runs/patrol-batches/batch-incident-1/summary/')) {
      return route.fulfill({
        json: {
          patrol_batch_id: batch.patrol_batch_id,
          run_count: 1,
          device_count: 1,
          change_count: 2,
          event_count: 1,
          status_counts: { failed: 1 },
          pipeline: {
            total_devices: 1,
            stage_count: 4,
            bottleneck_stage_key: '0:patrol',
            bottleneck_stage_label: '命令采集与解析',
            stages: [
              ['0:patrol', 0, 'patrol', 'device-patrol', '命令采集与解析', '登录设备、执行巡检命令并保存原始输出', 'failed', 0, 1, 0, 0, 12.4],
              ['1:state', 1, 'state', 'state-normalizer', '状态归一化', '将解析结果转换为统一的设备状态', 'pending', 0, 0, 0, 1, null],
              ['2:change', 2, 'change', 'change-detector', '变化检测', '与历史状态比较并识别有效变化', 'pending', 0, 0, 0, 1, null],
              ['3:event', 3, 'event', 'event-builder', '事件判定', '使用活动规则判断是否生成巡检事件', 'pending', 0, 0, 0, 1, null],
            ].map(([key, step_index, step_name, skill_name, label, description, status, completed_count, failed_count, active_count, pending_count, p95_seconds]) => ({
              key, step_index, step_name, skill_name, label, description, status,
              total: 1,
              status_counts: { [String(status)]: 1 },
              completed_count, failed_count, active_count, pending_count,
              completion_rate: Number(completed_count),
              duration: { sample_count: p95_seconds === null ? 0 : 1, average_seconds: p95_seconds, p95_seconds, max_seconds: p95_seconds },
              error_examples: status === 'failed' ? [{ device_id: 'device-1', device_name: 'core-sw-01', message: 'SSH authentication failed' }] : [],
            })),
          },
          event_types: { InterfaceDown: 1 },
          severities: { critical: 1 },
          quality: {
            status: 'degraded',
            command_count: 25,
            execution_success_count: 25,
            parser_eligible_count: 7,
            parser_ready_count: 5,
            observation_count: 32,
          },
          no_event_summary: {
            total: 1,
            by_reason: { no_active_event_rules: 1 },
          },
          runs: [],
          sample_events: [],
          sample_changes: [],
          deliverables: { csv_url: '', report_url: '' },
        },
      })
    }
    if (path.endsWith('/workflows/runs/patrol-batches/batch-incident-1/devices')) {
      return route.fulfill({
        json: {
          patrol_batch_id: batch.patrol_batch_id,
          target_type: batch.target_type,
          target_label: batch.target_label,
          selector: batch.selector,
          target_count: 2,
          launched_count: 1,
          skipped_count: 1,
          total: 2,
          limit: 50,
          offset: 0,
          items: [
            {
              device_id: device.device_id,
              device_name: device.device_name,
              management_ip: device.management_ip,
              selection_status: 'launched',
              preflight_status: 'ready',
              connection_status: 'failed',
              workflow_run_id: failedRun.run_id,
              workflow_status: 'failed',
              error_message: 'SSH authentication failed',
            },
            {
              device_id: 'device-2',
              device_name: 'core-sw-02',
              management_ip: '10.0.0.2',
              selection_status: 'skipped',
              preflight_status: 'failed',
              connection_status: 'not_attempted',
              failure_code: 'credential_missing',
              error_message: '未绑定凭据',
            },
          ],
        },
      })
    }
    if (path.endsWith('/devices/device-1/evidence-timeline/')) {
      return route.fulfill({ json: { device, items: [evidenceItem], limit: 10 } })
    }
    if (path.endsWith('/devices/device-1/execution-plans/')) {
      return route.fulfill({ json: { device, items: [], limit: 10 } })
    }
    if (path.endsWith('/parser/coverage/')) {
      return route.fulfill({
        json: {
          templates_total: 8,
          templates_active: 8,
          jobs_total: 0,
          jobs_pending: 0,
          missing_candidates: 0,
          repair_candidates: 0,
          production_open_jobs: 0,
          pending_sanitization_samples: 0,
          sanitized_samples: 12,
          backfills_pending_approval: 0,
          backfills_failed: 0,
          alerts: [],
        },
      })
    }
    if (path.endsWith('/parser/asset-production/check-items/')) {
      return route.fulfill({
        json: {
          items: [],
          page: 1,
          page_size: 25,
          total: 0,
          page_count: 0,
          summary: {
            total: 0,
            action_required: 0,
            in_progress: 0,
            awaiting_approval: 0,
            published: 0,
            not_applicable: 0,
            exception: 0,
            by_bucket: {},
            by_status: {},
          },
        },
      })
    }
    if (path.endsWith('/parser/asset-production/automation-control/')) {
      return route.fulfill({
        json: {
          settings: {
            mode: 'disabled',
            max_concurrency: 1,
            max_generation_attempts: 3,
            stale_run_seconds: 3600,
            retry_delay_seconds: 300,
            min_period_count: 2,
            min_device_count: 2,
            min_record_count: 10,
            max_estimated_daily_events: 500,
            allow_critical: false,
            last_run: {},
          },
          scheduler: {
            status: 'ok',
            recorded_at: '2026-07-29T10:00:00Z',
            age_seconds: 5,
            worker_id: 'celery@visual',
          },
          active_generation_runs: 0,
          command_units: {},
        },
      })
    }
    if (path.endsWith('/parser/check-item-runtime-bundles/health/')) {
      return route.fulfill({
        json: {
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
        },
      })
    }
    if (path.endsWith('/parser/dispatch-control/')) {
      return route.fulfill({ json: { auto_dispatch_enabled: false } })
    }
    if (path.endsWith('/parser/runtime-assets/resolve/')) {
      return route.fulfill({
        json: {
          ready: true,
          observation_contracts: {},
          change_policies: {},
          event_rules: [],
          missing_observation_contracts: [],
          missing_change_policies: [],
        },
      })
    }
    if (path.endsWith('/devices/import/template/')) {
      return route.fulfill({ json: { columns: [], example_csv: '' } })
    }
    return route.fulfill({ json: { items: [] } })
  })
}

async function settle(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready
  })
}

test('login visual baseline', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'NetOps Agent' })).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('login.png')
})

test('devices visual baseline', async ({ page }) => {
  await installAuthenticatedApi(page)
  await page.goto('/devices')
  await expect(page.getByText('core-sw-01')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('devices.png')
})

test('parser control plane visual baseline', async ({ page }) => {
  await installAuthenticatedApi(page)
  await page.goto('/skills/parser')
  await expect(page.getByText('巡检规则资产', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '生产工作台', exact: true }))
    .toHaveAttribute('aria-current', 'page')
  await settle(page)
  await expect(page).toHaveScreenshot('parser-control-plane.png')
})

test('patrol batch and evidence visual baselines', async ({ page }) => {
  await installAuthenticatedApi(page)
  await page.goto('/devices?tab=patrol')
  await expect(page.getByText(batch.patrol_batch_id).first()).toBeVisible()
  await page.getByRole('button', { name: /查看批次详情/ }).first().click()
  await expect(page.getByText('SSH authentication failed', { exact: true })).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('patrol-batch.png')

  await page.getByRole('button', { name: /巡检详情$/ }).first().click()
  await expect(page.getByText('接口 Gi1/0/1 已 Down')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('evidence-timeline.png')
})

test('status visual baseline', async ({ page }) => {
  await installAuthenticatedApi(page)
  await page.goto('/status')
  await expect(page.getByText('Skill 总数')).toBeVisible()
  await settle(page)
  await expect(page).toHaveScreenshot('status.png')
})
