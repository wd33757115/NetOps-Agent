import { describe, expect, it } from 'vitest'
import { layout, sides } from './layout'

// The lab: two routers linked directly, a hub segment joining both and a management host,
// and one PC on an edge port of each router.
const nodes = [
  { id: 'r1', identity_status: 'collected' },
  { id: 'r2', identity_status: 'collected' },
  { id: 'mgmt', identity_status: 'host' },
  { id: 'pc1', identity_status: 'host' },
  { id: 'pc2', identity_status: 'host' },
  { id: 'hub', identity_status: 'segment' },
]
const links = [
  { id: 'direct', source: 'r1', target: 'r2' },
  { id: 'hub#0', source: 'r1', target: 'hub' },
  { id: 'hub#1', source: 'r2', target: 'hub' },
  { id: 'hub#2', source: 'mgmt', target: 'hub' },
  { id: 'pc1', source: 'r2', target: 'pc1' },
  { id: 'pc2', source: 'pc2', target: 'r1' },
]

describe('topology layout', () => {
  const at = layout(nodes, links)
  const p = (id: string) => at.get(id)!

  it('keeps directly linked devices side by side in one row', () => {
    expect(p('r1').y).toBe(p('r2').y)
    expect(Math.abs(p('r1').x - p('r2').x)).toBeLessThanOrEqual(480)
  })

  it('puts a segment above its devices and its host above the segment', () => {
    expect(p('hub').y).toBeLessThan(p('r1').y)
    expect(p('hub').x).toBeCloseTo((p('r1').x + p('r2').x) / 2)
    expect(p('mgmt').y).toBeLessThan(p('hub').y)
  })

  it('puts an edge-port host below the device it attaches to', () => {
    expect(p('pc1').y).toBeGreaterThan(p('r2').y)
    expect(p('pc1').x).toBe(p('r2').x)
    expect(p('pc2').x).toBe(p('r1').x)
  })

  it('never stacks two nodes on the same spot', () => {
    const spots = [...at.values()].map(q => `${q.x}:${q.y}`)
    expect(new Set(spots).size).toBe(spots.length)
  })

  it('connects facing sides', () => {
    expect(sides(p('r1'), p('r2'))).toEqual(['right', 'left'])
    expect(sides(p('r2'), p('pc1'))).toEqual(['bottom', 'top'])
    expect(sides(p('r1'), p('hub'))).toEqual(['top', 'bottom'])
  })
})

it('positions unidentified endpoints while preserving legacy host snapshots', () => {
  const oldPositions = layout(nodes, links)
  const current = layout(nodes.map(node => node.identity_status === 'host' ? { ...node, identity_status: 'endpoint' } : node), links)
  expect(current).toEqual(oldPositions)
  expect(current.size).toBe(nodes.length)
})
