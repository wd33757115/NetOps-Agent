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
  status: 'active',
  groups: ['core'],
}

async function mockPatrolOperations(page: Page) {
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'e2e-access-token')
    localStorage.setItem('auth_user', JSON.stringify(user))
  }, admin)

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname

    if (path.endsWith('/auth/me/')) {
      await route.fulfill({ json: admin })
      return
    }
    if (path.endsWith('/devices/')) {
      await route.fulfill({ json: { items: [device], total: 1, limit: 200, offset: 0 } })
      return
    }
    if (path.endsWith('/workflows/runs/patrol-batches/')) {
      const limit = Number(url.searchParams.get('limit') || 25)
      const offset = Number(url.searchParams.get('offset') || 0)
      await route.fulfill({
        json: {
          items: [{
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
          }],
          total: 30,
          limit,
          offset,
          page: Math.floor(offset / limit) + 1,
          page_size: limit,
          page_count: 2,
        },
      })
      return
    }
    if (path.endsWith('/workflows/runs/patrol-batches/batch-incident-1/devices')) {
      await route.fulfill({
        json: {
          patrol_batch_id: 'batch-incident-1',
          target_type: 'group',
          target_label: '设备分组：core',
          selector: { group_ids: ['core'] },
          target_count: 2,
          launched_count: 1,
          skipped_count: 1,
          total: 2,
          limit: 50,
          offset: 0,
          items: [
            {
              device_id: 'device-1',
              device_name: 'core-sw-01',
              management_ip: '10.0.0.1',
              selection_status: 'launched',
              preflight_status: 'ready',
              connection_status: 'failed',
              workflow_run_id: 'workflow-run-1',
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
      return
    }
    if (path.endsWith('/workflows/runs/')) {
      const runs = url.searchParams.get('patrol_batch_id')
        ? [{
          run_id: 'workflow-run-1',
          template_name: 'patrol-change-event',
          source: 'device-management',
          status: 'failed',
          current_step_index: 5,
          error_message: 'InterfaceDown event requires operator review',
          device_id: device.device_id,
          device_name: device.device_name,
          patrol_batch_id: 'batch-incident-1',
          created_at: '2026-07-12T09:00:00Z',
        }]
        : []
      await route.fulfill({ json: runs })
      return
    }
    if (path.endsWith('/workflows/runs/patrol-batches/batch-incident-1/summary/')) {
      await route.fulfill({
        json: {
          patrol_batch_id: 'batch-incident-1',
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
      return
    }
    if (path.endsWith('/devices/device-1/evidence-timeline/')) {
      await route.fulfill({
        json: {
          device,
          limit: 10,
          items: [{
            workflow_run_id: 'workflow-run-1',
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
            patrol_batch_id: 'batch-incident-1',
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
          }],
        },
      })
      return
    }
    if (path.endsWith('/devices/device-1/execution-plans/')) {
      await route.fulfill({ json: { device, items: [], limit: 10 } })
      return
    }
    if (path.endsWith('/devices/groups/') || path.endsWith('/devices/dynamic-groups/')
      || path.endsWith('/devices/credentials/') || path.endsWith('/parser/command-profiles/')) {
      await route.fulfill({ json: { items: [] } })
      return
    }
    if (path.endsWith('/devices/import/template/')) {
      await route.fulfill({ json: { columns: [], example_csv: '' } })
      return
    }
    await route.fulfill({ json: { items: [] } })
  })
}

test('traces a failed patrol run to its parser, change policy, and event rule', async ({ page }) => {
  await mockPatrolOperations(page)
  await page.goto('/devices?tab=patrol')

  await expect(page.getByRole('tab', { name: '巡检运行' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('batch-incident-1').first()).toBeVisible()
  await page.getByRole('button', { name: /查看批次详情/ }).first().click()

  await expect(page.getByText('设备分组：core').last()).toBeVisible()
  await expect(page.getByText('SSH authentication failed', { exact: true })).toBeVisible()
  await expect(page.getByText('credential_missing: 未绑定凭据')).toBeVisible()
  await expect(page.getByText('1 条变化未生成事件')).toBeVisible()
  await expect(page.getByText('缺少启用中的事件规则：1')).toBeVisible()
  await page.getByRole('button', { name: /巡检详情$/ }).first().click()

  await expect(page.getByRole('dialog', { name: '巡检详情 - core-sw-01' })).toBeVisible()
  await expect(page.getByText('接口 Gi1/0/1 已 Down')).toBeVisible()
  await page.getByRole('button', { name: /处理过程与技术证据/ }).click()
  await expect(page.getByText('parser-template-interface-status-v1')).toBeVisible()
  await expect(page.getByText('change-policy-interface-v1')).toBeVisible()
  await expect(page.getByText(/规则 event-rule-interface-down v1/)).toBeVisible()

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(pageOverflow).toBe(false)
})

test('paginates patrol batch history on the server and persists the page in the URL', async ({ page }) => {
  await mockPatrolOperations(page)
  await page.goto('/devices?tab=patrol')

  const pageTwo = page.locator('.ant-pagination-item-2')
  await expect(pageTwo).toBeVisible()
  await pageTwo.click()

  await expect(page).toHaveURL(/batch_page=2/)
})
