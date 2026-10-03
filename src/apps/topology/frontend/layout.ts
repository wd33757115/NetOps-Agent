// Deterministic canvas layout for the SIMAP view; no graph-layout dependency.
// Devices form the middle row(s), ordered so that directly linked devices sit side by side.
// A shared segment sits above the devices it joins; a host sits below the device port it
// attaches to, or above its segment. Edges then use the facing sides of their two nodes.

export interface LayoutNode { id: string; identity_status: string }
export interface LayoutLink { id: string; source: string; target: string }
export interface Point { x: number; y: number }
export type Side = 'top' | 'bottom' | 'left' | 'right'

const DEVICE_GAP = 480  // wide enough for a "port ⇄ port" label between two devices
const DEVICES_PER_ROW = 6
const ROW_GAP = 560
const TIER = 200
const SIBLING_GAP = 230
const WIDTH: Record<string, number> = { segment: 150 }
const NODE_WIDTH = 192
const NODE_HEIGHT = 96

const isEndpoint = (node: LayoutNode) => ['host', 'endpoint'].includes(node.identity_status)
const isDevice = (node: LayoutNode) => !isEndpoint(node) && node.identity_status !== 'segment'

export function layout(nodes: LayoutNode[], links: LayoutLink[]): Map<string, Point> {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const neighbours = new Map<string, string[]>(nodes.map(node => [node.id, []]))
  for (const link of links) {
    if (!byId.has(link.source) || !byId.has(link.target)) continue
    neighbours.get(link.source)!.push(link.target)
    neighbours.get(link.target)!.push(link.source)
  }
  // Breadth-first over device-to-device links keeps adjacent devices next to each other.
  const devices = nodes.filter(isDevice)
  const ordered: string[] = []
  for (const start of devices) {
    if (ordered.includes(start.id)) continue
    const queue = [start.id]
    while (queue.length) {
      const id = queue.shift()!
      if (ordered.includes(id)) continue
      ordered.push(id)
      for (const next of neighbours.get(id)!) {
        if (isDevice(byId.get(next)!) && !ordered.includes(next)) queue.push(next)
      }
    }
  }
  const positions = new Map<string, Point>()
  ordered.forEach((id, index) => positions.set(id, {
    x: (index % DEVICES_PER_ROW) * DEVICE_GAP,
    y: Math.floor(index / DEVICES_PER_ROW) * ROW_GAP,
  }))
  const taken: Point[] = [...positions.values()]
  const place = (id: string, x: number, y: number) => {
    // Shift sideways until no node already occupies the spot.
    let offset = 0
    let candidate = { x, y }
    while (taken.some(p => Math.abs(p.y - candidate.y) < NODE_HEIGHT && Math.abs(p.x - candidate.x) < SIBLING_GAP - 10)) {
      offset = offset <= 0 ? -offset + SIBLING_GAP : -offset
      candidate = { x: x + offset, y }
    }
    positions.set(id, candidate)
    taken.push(candidate)
  }
  const mean = (ids: string[]) => ids.reduce((sum, id) => sum + positions.get(id)!.x, 0) / ids.length
  for (const segment of nodes.filter(node => node.identity_status === 'segment')) {
    const members = neighbours.get(segment.id)!.filter(id => positions.has(id) && isDevice(byId.get(id)!))
    const anchor = members.length ? members : ordered.slice(0, 1)
    const top = anchor.length ? Math.min(...anchor.map(id => positions.get(id)!.y)) : 0
    place(segment.id, anchor.length ? mean(anchor) : 0, top - TIER)
  }
  const fallbackY = (ordered.length ? Math.max(...ordered.map(id => positions.get(id)!.y)) : 0) + TIER * 2
  let loose = 0
  for (const host of nodes.filter(isEndpoint)) {
    const attached = neighbours.get(host.id)!.find(id => positions.has(id))
    if (!attached) {
      place(host.id, loose++ * SIBLING_GAP, fallbackY)
      continue
    }
    const anchor = positions.get(attached)!
    const above = byId.get(attached)!.identity_status === 'segment'
    place(host.id, anchor.x, anchor.y + (above ? -TIER : TIER))
  }
  return positions
}

const centre = (id: string, node: LayoutNode | undefined, p: Point) => ({
  x: p.x + (WIDTH[node?.identity_status ?? ''] ?? NODE_WIDTH) / 2,
  y: p.y + NODE_HEIGHT / 2,
  id,
})

// Facing sides: same row connects left/right, otherwise top/bottom.
export function sides(source: Point, target: Point, sourceNode?: LayoutNode, targetNode?: LayoutNode): [Side, Side] {
  const a = centre('s', sourceNode, source)
  const b = centre('t', targetNode, target)
  if (Math.abs(a.y - b.y) < NODE_HEIGHT / 2) return a.x <= b.x ? ['right', 'left'] : ['left', 'right']
  return a.y < b.y ? ['bottom', 'top'] : ['top', 'bottom']
}
