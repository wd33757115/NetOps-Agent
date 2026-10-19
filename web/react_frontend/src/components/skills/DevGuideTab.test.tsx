import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import DevGuideTab from './DevGuideTab'

vi.mock('../../apps/generated', () => ({ packagedApps: [] }))
vi.mock('./WorkflowRunMonitor', () => ({ default: () => null }))
vi.mock('../../services/api', () => ({
  workflowApi: {
    listRuns: vi.fn(async () => [{ run_id: 'run-1', template_name: 'demo-text-report', ticket_id: 'DEMO-3', status: 'completed' }]),
  },
}))

class NoObserver {
  observe() {}
  disconnect() {}
}

const renderTab = () => render(
  <QueryClientProvider client={new QueryClient()}>
    <DevGuideTab />
  </QueryClientProvider>,
)

const outline = () => screen.getByRole('navigation', { name: '手册目录' })

describe('DevGuideTab', () => {
  afterEach(() => window.history.replaceState(null, '', '/'))

  it('switches between the platform, skill and workflow handbooks, and keeps the choice in the address', async () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    renderTab()
    expect(screen.getByRole('heading', { level: 1, name: /平台/ })).toBeInTheDocument()

    fireEvent.click(screen.getByText('Skill 开发手册'))
    expect(screen.getByRole('heading', { level: 1, name: 'Skill 开发手册' })).toBeInTheDocument()
    expect(within(outline()).getByRole('link', { name: '2.4 入口脚本约定' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#skill')

    fireEvent.click(screen.getByText('Workflow 开发手册'))
    expect(screen.getByRole('heading', { level: 1, name: 'Workflow 开发手册' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#workflow')
    expect(await screen.findByText('DEMO-3')).toBeInTheDocument()
  })

  it('opens the handbook named in the address, with the recent runs under the workflow one', async () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    window.history.replaceState(null, '', '/skills/guide#workflow')
    renderTab()
    expect(screen.getByRole('heading', { level: 1, name: 'Workflow 开发手册' })).toBeInTheDocument()
    const runs = screen.getByRole('region', { name: 'Workflow 运行记录' })
    expect(await within(runs).findByText('DEMO-3')).toBeInTheDocument()
  })
})
