import { describe, expect, it } from 'vitest'
import {
  defaultLayout,
  dissolveFolder,
  moveNode,
  reconcileLayout,
} from './desktopLayout'

describe('desktop layout', () => {
  it('swaps occupied cells without losing applications', () => {
    const layout = moveNode(defaultLayout(), 'patrol', 'desktop:0', {
      col: 1,
      row: 0,
    })
    expect(layout.nodes.find((n) => n.id === 'patrol-rules')?.col).toBe(0)
    expect(layout.nodes.find((n) => n.id === 'patrol')?.col).toBe(1)
    expect(layout.nodes).toHaveLength(3)
  })
  it('moves in and out of folders and dissolves without touching Dock', () => {
    const layout = defaultLayout()
    layout.nodes.push({
      id: 'network',
      kind: 'folder',
      name: '网络',
      containerId: 'desktop:0',
      col: 3,
      row: 0,
    })
    const moved = moveNode(
      moveNode(layout, 'patrol', 'folder:network'),
      'patrol-rules',
      'folder:network',
      undefined,
      'patrol',
    )
    expect(moved.nodes.find((n) => n.id === 'patrol-rules')?.order).toBe(0)
    expect(moved.nodes.find((n) => n.id === 'patrol')?.order).toBe(1)
    const dissolved = dissolveFolder(moved, 'network')
    expect(dissolved.nodes.every((n) => n.containerId === 'desktop:0')).toBe(
      true,
    )
    expect(dissolved.dockAppIds).toEqual(layout.dockAppIds)
    expect(new Set(dissolved.nodes.map((n) => `${n.col}:${n.row}`)).size).toBe(
      3,
    )
  })
  it('retains unknown app references and reconciles new apps and duplicates', () => {
    const layout = defaultLayout()
    layout.nodes = [
      {
        id: 'future',
        kind: 'app',
        appId: 'future',
        containerId: 'desktop:0',
        col: 0,
        row: 0,
      },
    ]
    layout.nodes.push({ ...layout.nodes[0] })
    const result = reconcileLayout(layout)
    expect(result.nodes).toHaveLength(4)
    expect(result.nodes[0].appId).toBe('future')
  })
  it('rejects nested or missing folders', () => {
    const layout = defaultLayout()
    expect(moveNode(layout, 'patrol', 'folder:missing')).toBe(layout)
  })
})
