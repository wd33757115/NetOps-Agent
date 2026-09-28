import React from 'react'
import { render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { DevicePatrolPreviewRow } from '@netops/services/api'
import PatrolPlanPreview from './PatrolPlanPreview'

it('shows the actual ordered commands even when a device has check items', () => {
  render(<PatrolPlanPreview rows={[
    { device_id: 'd1', device_name: 'Core-1', ip: '192.168.1.1', plan: { profile_name: '核心巡检', check_item_ids: ['device.version'] }, commands: ['display version', 'display cpu-usage'], command_count: 2 },
    { device_id: 'd2', device_name: 'Core-2', ip: '192.168.1.2', plan: { profile_name: '另一命令集' }, commands: ['show version'], command_count: 1, coverage_violations: [{ command: 'show version' }] },
  ] as DevicePatrolPreviewRow[]} />)
  const first = screen.getByText('Core-1').closest('tr')!
  expect(within(first).getByText('192.168.1.1')).toBeVisible()
  expect(within(first).getByLabelText('Core-1 的执行命令').textContent).toBe('1. display version\n2. display cpu-usage')
  expect(within(first).queryByText('show version')).not.toBeInTheDocument()
  const second = screen.getByText('Core-2').closest('tr')!
  expect(within(second).getByLabelText('Core-2 的执行命令')).toHaveTextContent('1. show version')
  expect(within(second).getByText('解析缺口 1')).toBeVisible()
})
