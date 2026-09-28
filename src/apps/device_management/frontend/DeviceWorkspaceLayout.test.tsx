import React from 'react'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import DeviceWorkspaceLayout from './DeviceWorkspaceLayout'

it('places command configuration above assets with its own page heading', () => {
  render(<MemoryRouter><DeviceWorkspaceLayout activeKey="commands" title="设备管理" subtitle="资产" toolbar={<button>新增设备</button>} onImport={vi.fn()} onGroups={vi.fn()}><p>命令内容</p></DeviceWorkspaceLayout></MemoryRouter>)
  const links = within(screen.getByRole('navigation', { name: '设备管理导航' })).getAllByRole('link')
  expect(links).toHaveLength(2)
  expect(screen.queryByText('平台模块')).not.toBeInTheDocument()
  expect(links[0]).toHaveTextContent('配置命令集')
  expect(links[0]).toHaveAttribute('href', '/apps/device-management?tab=commands')
  expect(links[0]).toHaveAttribute('aria-current', 'page')
  expect(links[1]).toHaveTextContent('设备资产')
  expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('配置命令集')
  expect(screen.queryByRole('button', { name: '新增设备' })).not.toBeInTheDocument()
})
