import { describe, expect, it } from 'vitest'
import { drawing, intersectsNode, layout, NODE_HEIGHT, nodeWidth, sharedLength, type LayoutNode, type Point } from './layout'
import campus from './fixtures/campus-layout.json'

const segments = (points: Point[]) => points.slice(1).map((point, index) => [points[index], point] as const)
const node = (id: string): LayoutNode => ({ id, identity_status: 'collected' })
const rects = (nodes: LayoutNode[], at: Map<string, Point>) => nodes.map(n => ({ id: n.id, ...at.get(n.id)!, width: nodeWidth(n), height: NODE_HEIGHT }))
const overlap = (a: ReturnType<typeof rects>[number], b: ReturnType<typeof rects>[number]) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y

describe('the complete observed 15-node / 24-link canvas', () => {
  const plan = drawing(campus.nodes, campus.links)

  it('keeps every relationship, including each independent parallel link', () => {
    expect(plan.positions.size).toBe(15)
    expect(plan.routes.size).toBe(24)
    expect(new Set([...plan.routes.values()].map(route => route.path)).size).toBe(24)
    expect([...plan.ports.values()].flat()).toHaveLength(48)
    for (const ports of plan.ports.values()) {
      expect(new Set(ports.map(port => port.id)).size).toBe(ports.length)
      expect(new Set(ports.map(port => `${port.point.x}:${port.point.y}`)).size).toBe(ports.length)
    }
  })

  it('never draws a line through another node or stacks node rectangles', () => {
    const boxes = rects(campus.nodes, plan.positions)
    for (const [index, a] of boxes.entries()) for (const b of boxes.slice(index + 1)) expect(overlap(a, b), `${a.id}/${b.id}`).toBe(false)
    for (const link of campus.links) {
      for (const box of boxes.filter(n => n.id !== link.source && n.id !== link.target)) {
        for (const [a, b] of segments(plan.routes.get(link.id)!.points)) expect(intersectsNode(a, b, box), `${link.id}/${box.id}`).toBe(false)
      }
    }
  })

  it('has no shared line segment hiding a second relationship', () => {
    const routes = [...plan.routes.values()]
    for (const [index, first] of routes.entries()) for (const second of routes.slice(index + 1)) {
      for (const [a, b] of segments(first.points)) for (const [c, d] of segments(second.points)) expect(sharedLength(a, b, c, d)).toBeLessThan(1)
    }
  })

  it('does not move devices or routes when API arrays arrive in another order', () => {
    const reversed = drawing([...campus.nodes].reverse(), [...campus.links].reverse())
    expect(reversed.positions).toEqual(plan.positions)
    expect(reversed.routes).toEqual(plan.routes)
  })

  it('keeps the focused port label off node rectangles', () => {
    const boxes = rects(campus.nodes, plan.positions)
    for (const route of plan.routes.values()) {
      const label = { id: 'label', x: route.label.x - 180, y: route.label.y - 18, width: 360, height: 36 }
      expect(boxes.some(box => overlap(label, box))).toBe(false)
    }
  })
})

it('routes long same-row edges around intervening devices', () => {
  const nodes = [node('a'), node('middle'), node('b')]
  const links = [{ id: 'long', source: 'a', target: 'b' }]
  const positions = new Map([['a', { x: 0, y: 0 }], ['middle', { x: 400, y: 0 }], ['b', { x: 800, y: 0 }]])
  const route = drawing(nodes, links, positions).routes.get('long')!
  expect(route.points.length).toBeGreaterThan(2)
  for (const [a, b] of segments(route.points)) expect(intersectsNode(a, b, { x: 400, y: 0, width: 192, height: 96 })).toBe(false)
})

it.each([2, 6, 8])('preserves %i parallel links with separate clickable middle lanes', count => {
  const nodes = [node('a'), node('b')]
  const links = Array.from({ length: count }, (_, i) => ({ id: `port-${i}`, source: i % 2 ? 'b' : 'a', target: i % 2 ? 'a' : 'b' }))
  const routes = [...drawing(nodes, links).routes.values()]
  expect(routes).toHaveLength(count)
  for (const [index, first] of routes.entries()) for (const second of routes.slice(index + 1)) {
    expect(first.source.id).not.toBe(second.source.id)
    for (const [a, b] of segments(first.points)) for (const [c, d] of segments(second.points)) expect(sharedLength(a, b, c, d)).toBeLessThan(1)
  }
})

it('keeps all spokes, isolated nodes, self links and old host identities', () => {
  const nodes = [node('a'), node('b'), node('isolated'), { id: 'segment', identity_status: 'segment' }, { id: 'endpoint', identity_status: 'endpoint' }]
  const links = [{ id: 'self', source: 'a', target: 'a' }, ...['a', 'b', 'endpoint'].map(id => ({ id: `shared#${id}`, source: id, target: 'segment' }))]
  const plan = drawing(nodes, links)
  expect(plan.positions.size).toBe(nodes.length)
  expect(plan.routes.size).toBe(links.length)
  expect(layout(nodes.map(n => n.identity_status === 'endpoint' ? { ...n, identity_status: 'host' } : n), links)).toEqual(plan.positions)
  expect(drawing([], []).routes.size).toBe(0)
})
