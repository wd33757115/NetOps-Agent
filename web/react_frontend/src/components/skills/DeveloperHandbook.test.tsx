import React from 'react'
import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import DeveloperHandbook, { HANDBOOKS } from './DeveloperHandbook'

vi.mock('../../apps/generated', () => ({
  packagedApps: [{ id: 'hello-demo', name: '演示应用' }, { id: 'inspection', name: '网络巡检' }],
}))

class NoObserver {
  observe() {}
  disconnect() {}
}

describe('DeveloperHandbook', () => {
  it('lists the chapters and sections of the handbook in its outline', () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    render(<DeveloperHandbook source={HANDBOOKS.platform} />)
    const outline = screen.getByRole('navigation', { name: '手册目录' })
    expect(within(outline).getByRole('link', { name: '一、平台整体架构' })).toBeInTheDocument()
    expect(within(outline).getByRole('link', { name: '3.6 下线' })).toHaveAttribute('href', '#handbook-3.6-下线')
    expect(document.getElementById('handbook-3.6-下线')).not.toBeNull()
  })

  it('finds the headings of a Windows checkout too', () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    render(<DeveloperHandbook source={HANDBOOKS.platform.replace(/\r?\n/g, '\r\n')} />)
    const outline = screen.getByRole('navigation', { name: '手册目录' })
    expect(within(outline).getByRole('link', { name: '3.6 下线' })).toHaveAttribute('href', '#handbook-3.6-下线')
  })

  it('draws the request path and the lifecycle instead of their ASCII sketches', () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    render(<DeveloperHandbook source={HANDBOOKS.platform} />)
    const path = screen.getByRole('figure', { name: '平台进程与请求路径' })
    // The packages this build contains, not a list written by hand.
    expect(within(path).getByText('演示应用')).toBeInTheDocument()
    const lifecycle = screen.getByRole('figure', { name: '应用生命周期' })
    expect(within(lifecycle).getByText('已启用')).toBeInTheDocument()
    expect(within(lifecycle).getByText('接口 200；代码改动未升级时 503')).toBeInTheDocument()
    expect(screen.queryByText(/--install-->/)).not.toBeInTheDocument()
  })

  it('follows the section being read without losing the headings it watches', () => {
    let report: (entries: Partial<IntersectionObserverEntry>[]) => void = () => {}
    const watched: Element[] = []
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: typeof report) { report = callback }
      observe(node: Element) { watched.push(node) }
      disconnect() {}
    })
    render(<DeveloperHandbook source={HANDBOOKS.platform} />)
    const heading = document.getElementById('handbook-3.6-下线')!
    act(() => report([{ target: heading, isIntersecting: true, boundingClientRect: { top: 10 } as DOMRect }]))

    const outline = screen.getByRole('navigation', { name: '手册目录' })
    expect(within(outline).getByRole('link', { name: '3.6 下线' })).toHaveAttribute('aria-current', 'location')
    // Moving the highlight must not remount the text: every watched heading is still on the page.
    expect(watched.length).toBeGreaterThan(10)
    expect(watched.every(node => node.isConnected)).toBe(true)
  })

  it('shows the demo screenshots with their captions', () => {
    vi.stubGlobal('IntersectionObserver', NoObserver)
    render(<DeveloperHandbook source={HANDBOOKS.platform} />)
    const shot = screen.getByAltText('演示应用 0.2.0')
    expect(shot.getAttribute('src')).toMatch(/hello-demo-0\.2\.0\.jpg/)
  })
})
