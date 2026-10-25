// Canvas geometry only: model identities and observed port relationships stay intact.
export interface LayoutNode { id: string; identity_status: string }
export interface LayoutLink { id: string; source: string; target: string }
export interface Point { x: number; y: number }
export interface Rect extends Point { width: number; height: number }
export type Side = 'top' | 'bottom' | 'left' | 'right'
export interface Port { id: string; type: 'source' | 'target'; side: Side; offset: number; point: Point }
export interface Route { source: Port; target: Port; points: Point[]; path: string; label: Point }
export interface Drawing { positions: Map<string, Point>; ports: Map<string, Port[]>; routes: Map<string, Route> }

export const NODE_HEIGHT = 96
export const nodeWidth = (node: LayoutNode) => node.identity_status === 'segment' ? 150 : 192
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0
const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)
const centre = (node: LayoutNode, point: Point): Point => ({ x: point.x + nodeWidth(node) / 2, y: point.y + NODE_HEIGHT / 2 })
const overlaps = (a: Rect, b: Rect, gap = 0) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y

export function layout(nodes: LayoutNode[], links: LayoutLink[]): Map<string, Point> {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const adjacent = new Map(nodes.map(node => [node.id, new Set<string>()]))
  for (const link of links) {
    if (link.source === link.target || !byId.has(link.source) || !byId.has(link.target)) continue
    adjacent.get(link.source)!.add(link.target)
    adjacent.get(link.target)!.add(link.source)
  }
  const components: string[][] = [], visited = new Set<string>()
  for (const first of [...byId.keys()].sort(compare)) {
    if (visited.has(first)) continue
    const component: string[] = [], queue = [first]
    while (queue.length) {
      const id = queue.shift()!
      if (visited.has(id)) continue
      visited.add(id); component.push(id)
      queue.push(...[...adjacent.get(id)!].sort(compare).filter(next => !visited.has(next)))
    }
    components.push(component)
  }
  components.sort((a, b) => b.length - a.length || compare(a[0], b[0]))
  const boxes = components.map(component => {
    let core = component.filter(id => adjacent.get(id)!.size > 1)
    if (!core.length) core = component.slice().sort(compare)
    const remaining = new Set(core), ordered: string[] = []
    const rank = (a: string, b: string) => adjacent.get(b)!.size - adjacent.get(a)!.size || compare(a, b)
    while (remaining.size) {
      const last = ordered[ordered.length - 1]
      const nearby = last ? [...adjacent.get(last)!].filter(id => remaining.has(id)).sort(rank) : []
      const next = nearby[0] ?? [...remaining].sort(rank)[0]
      ordered.push(next); remaining.delete(next)
    }
    const local = new Map<string, Point>(), angles = new Map<string, number>()
    const rect = (id: string, p: Point): Rect => ({ ...p, width: nodeWidth(byId.get(id)!), height: NODE_HEIGHT })
    let rx = Math.max(290, core.length * 48), ry = Math.max(185, core.length * 29)
    // Check actual rectangles. Geometry never depends on device names or roles.
    for (;;) {
      local.clear()
      ordered.forEach((id, index) => {
        const angle = ordered.length === 2 ? Math.PI * index : -Math.PI / 2 + index * 2 * Math.PI / ordered.length
        angles.set(id, angle)
        const x = ordered.length === 1 ? 0 : Math.cos(angle) * rx
        const y = ordered.length < 3 ? 0 : Math.sin(angle) * ry
        local.set(id, { x: x - nodeWidth(byId.get(id)!) / 2, y: y - NODE_HEIGHT / 2 })
      })
      const collides = ordered.some((id, i) => ordered.slice(i + 1).some(other => overlaps(rect(id, local.get(id)!), rect(other, local.get(other)!), 70)))
      if (!collides) break
      rx *= 1.1; ry *= 1.1
    }
    for (const parent of ordered) {
      const leaves = [...adjacent.get(parent)!].filter(id => !core.includes(id)).sort(compare)
      const origin = centre(byId.get(parent)!, local.get(parent)!)
      leaves.forEach((id, index) => {
        const angle = core.length === 1 ? -Math.PI / 2 + index * 2 * Math.PI / leaves.length : angles.get(parent)! + (index - (leaves.length - 1) / 2) * Math.min(0.65, 1.8 / leaves.length)
        let radius = 265, point: Point
        do {
          point = { x: origin.x + Math.cos(angle) * radius - nodeWidth(byId.get(id)!) / 2, y: origin.y + Math.sin(angle) * radius * 0.78 - NODE_HEIGHT / 2 }
          radius += 35
        } while ([...local].some(([other, p]) => overlaps(rect(id, point), rect(other, p), 70)))
        local.set(id, point)
      })
    }
    const minX = Math.min(...[...local.values()].map(p => p.x)), minY = Math.min(...[...local.values()].map(p => p.y))
    const width = Math.max(...[...local].map(([id, p]) => p.x + nodeWidth(byId.get(id)!))) - minX
    const height = Math.max(...[...local.values()].map(p => p.y + NODE_HEIGHT)) - minY
    return { local, minX, minY, width, height }
  })
  const result = new Map<string, Point>()
  const rowWidth = Math.max(1000, Math.sqrt(boxes.reduce((sum, box) => sum + (box.width + 150) * (box.height + 150), 0)) * 1.4)
  let x = 0, y = 0, rowHeight = 0
  for (const box of boxes) {
    if (x && x + box.width > rowWidth) { x = 0; y += rowHeight + 150; rowHeight = 0 }
    for (const [id, p] of box.local) result.set(id, { x: p.x - box.minX + x, y: p.y - box.minY + y })
    x += box.width + 150; rowHeight = Math.max(rowHeight, box.height)
  }
  return result
}

export function sides(source: Point, target: Point, sourceNode: LayoutNode = { id: '', identity_status: '' }, targetNode: LayoutNode = sourceNode): [Side, Side] {
  const a = centre(sourceNode, source), b = centre(targetNode, target)
  if (Math.abs(b.x - a.x) / 192 >= Math.abs(b.y - a.y) / NODE_HEIGHT) return a.x <= b.x ? ['right', 'left'] : ['left', 'right']
  return a.y <= b.y ? ['bottom', 'top'] : ['top', 'bottom']
}

// Interior intersection; a route may follow an obstacle's clearance boundary.
export function intersectsNode(a: Point, b: Point, box: Rect): boolean {
  let low = 0, high = 1
  for (const [origin, delta, min, max] of [[a.x, b.x - a.x, box.x + 0.01, box.x + box.width - 0.01], [a.y, b.y - a.y, box.y + 0.01, box.y + box.height - 0.01]]) {
    if (Math.abs(delta) < 0.0001) { if (origin < min || origin > max) return false; continue }
    const t1 = (min - origin) / delta, t2 = (max - origin) / delta
    low = Math.max(low, Math.min(t1, t2)); high = Math.min(high, Math.max(t1, t2))
    if (low > high) return false
  }
  return high >= 0 && low <= 1
}

export function sharedLength(a: Point, b: Point, c: Point, d: Point): number {
  const length = distance(a, b)
  if (!length) return 0
  const cross = (p: Point) => ((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)) / length
  if (Math.abs(cross(c)) > 0.1 || Math.abs(cross(d)) > 0.1) return 0
  const project = (p: Point) => ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / length
  return Math.max(0, Math.min(length, Math.max(project(c), project(d))) - Math.max(0, Math.min(project(c), project(d))))
}

const segments = (points: Point[]) => points.slice(1).map((p, i) => [points[i], p] as [Point, Point])
const simplify = (points: Point[]) => points.filter((p, i) => !i || i === points.length - 1 || Math.abs((p.x - points[i - 1].x) * (points[i + 1].y - p.y) - (p.y - points[i - 1].y) * (points[i + 1].x - p.x)) > 0.01)
const normal: Record<Side, Point> = { top: { x: 0, y: -1 }, bottom: { x: 0, y: 1 }, left: { x: -1, y: 0 }, right: { x: 1, y: 0 } }

// Visibility grid only for blocked lanes; size depends on nodes, not pixel dimensions.
function detour(start: Point, end: Point, obstacles: Rect[], reserved: Point[][]): Point[] {
  const xs = [...new Set([start.x, end.x, ...obstacles.flatMap(r => [r.x, r.x + r.width]), ...reserved.flatMap(points => points.flatMap(p => [p.x - 22, p.x + 22]))])].sort((a, b) => a - b)
  const ys = [...new Set([start.y, end.y, ...obstacles.flatMap(r => [r.y, r.y + r.height]), ...reserved.flatMap(points => points.flatMap(p => [p.y - 22, p.y + 22]))])].sort((a, b) => a - b)
  xs.unshift(xs[0] - 70); xs.push(xs[xs.length - 1] + 70)
  ys.unshift(ys[0] - 70); ys.push(ys[ys.length - 1] + 70)
  const width = xs.length, at = (index: number): Point => ({ x: xs[index % width], y: ys[Math.floor(index / width)] })
  const first = ys.indexOf(start.y) * width + xs.indexOf(start.x), last = ys.indexOf(end.y) * width + xs.indexOf(end.x)
  const cost = new Map([[first, 0]]), previous = new Map<number, number>()
  const heap: { index: number; rank: number }[] = []
  const push = (entry: { index: number; rank: number }) => {
    heap.push(entry); let i = heap.length - 1
    while (i && heap[(i - 1) >> 1].rank > entry.rank) { heap[i] = heap[(i - 1) >> 1]; i = (i - 1) >> 1 }
    heap[i] = entry
  }
  const pop = () => {
    const result = heap[0], tail = heap.pop()!
    if (heap.length) {
      let i = 0
      while (i * 2 + 1 < heap.length) {
        let child = i * 2 + 1
        if (child + 1 < heap.length && heap[child + 1].rank < heap[child].rank) child++
        if (heap[child].rank >= tail.rank) break
        heap[i] = heap[child]; i = child
      }
      heap[i] = tail
    }
    return result.index
  }
  const closed = new Set<number>(), occupied = reserved.flatMap(segments)
  push({ index: first, rank: distance(start, end) })
  while (heap.length) {
    const current = pop()
    if (closed.has(current)) continue
    if (current === last) {
      const points = [end]; let cursor = last
      while (cursor !== first) { cursor = previous.get(cursor)!; points.push(at(cursor)) }
      return simplify(points.reverse())
    }
    closed.add(current)
    const column = current % width, row = Math.floor(current / width), a = at(current)
    const neighbors = [column > 0 ? current - 1 : -1, column + 1 < width ? current + 1 : -1, row > 0 ? current - width : -1, row + 1 < ys.length ? current + width : -1]
    for (const next of neighbors) {
      if (next < 0 || closed.has(next)) continue
      const b = at(next)
      if (obstacles.some(rect => intersectsNode(a, b, rect)) || occupied.some(([c, d]) => sharedLength(a, b, c, d) > 0.1)) continue
      const nextCost = cost.get(current)! + distance(a, b)
      if (nextCost >= (cost.get(next) ?? Infinity)) continue
      cost.set(next, nextCost); previous.set(next, current)
      push({ index: next, rank: nextCost + Math.abs(end.x - b.x) + Math.abs(end.y - b.y) })
    }
  }
  return [] // Do not replace an invalid route with a line through another node.
}

function along(points: Point[], fraction: number): Point {
  const parts = segments(points), total = parts.reduce((sum, [a, b]) => sum + distance(a, b), 0)
  let remaining = total * fraction
  for (const [a, b] of parts) {
    const length = distance(a, b)
    if (remaining <= length) return { x: a.x + (b.x - a.x) * remaining / length, y: a.y + (b.y - a.y) * remaining / length }
    remaining -= length
  }
  return points[points.length - 1]
}

export function drawing(nodes: LayoutNode[], links: LayoutLink[], positions = layout(nodes, links)): Drawing {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const ports = new Map(nodes.map(node => [node.id, [] as Port[]]))
  const pending = new Map<string, { link: LayoutLink; role: 'source' | 'target'; side: Side; order: number }[]>()
  const ordered = links.filter(link => byId.has(link.source) && byId.has(link.target)).slice().sort((a, b) => compare(a.id, b.id))
  for (const link of ordered) {
    const from = byId.get(link.source)!, to = byId.get(link.target)!
    const facing = link.source === link.target ? ['right', 'bottom'] as [Side, Side] : sides(positions.get(from.id)!, positions.get(to.id)!, from, to)
    for (const [role, node, other, side] of [['source', from, to, facing[0]], ['target', to, from, facing[1]]] as const) {
      const point = centre(other, positions.get(other.id)!), key = `${node.id}:${side}`
      const rows = pending.get(key) ?? []
      rows.push({ link, role, side, order: side === 'left' || side === 'right' ? point.y : point.x }); pending.set(key, rows)
    }
  }
  const ends = new Map<string, { source?: Port; target?: Port }>()
  for (const rows of pending.values()) {
    rows.sort((a, b) => a.order - b.order || compare(a.link.id, b.link.id))
    rows.forEach((entry, index) => {
      const node = byId.get(entry.link[entry.role])!, p = positions.get(node.id)!, width = nodeWidth(node)
      const vertical = entry.side === 'left' || entry.side === 'right'
      const offset = 14 + (index + 1) / (rows.length + 1) * ((vertical ? NODE_HEIGHT : width) - 28)
      const point = { x: p.x + (vertical ? entry.side === 'left' ? 0 : width : offset), y: p.y + (vertical ? offset : entry.side === 'top' ? 0 : NODE_HEIGHT) }
      const port: Port = { id: `${encodeURIComponent(entry.link.id)}:${entry.role}`, type: entry.role, side: entry.side, offset, point }
      ports.get(node.id)!.push(port)
      const pair = ends.get(entry.link.id) ?? {}; pair[entry.role] = port; ends.set(entry.link.id, pair)
    })
  }
  const groups = new Map<string, LayoutLink[]>()
  for (const link of ordered) { const key = JSON.stringify([link.source, link.target].sort(compare)); const group = groups.get(key) ?? []; group.push(link); groups.set(key, group) }
  const routes = new Map<string, Route>()
  for (const group of groups.values()) {
    const reserved: Point[][] = []
    group.forEach((link, index) => {
      const { source, target } = ends.get(link.id)! as { source: Port; target: Port }
      const extend = (port: Port): Point => ({ x: port.point.x + normal[port.side].x * 30, y: port.point.y + normal[port.side].y * 30 })
      const a = extend(source), b = extend(target), padding = 12 + Math.min(index, 3) * 4
      const obstacles = nodes.map(node => ({ x: positions.get(node.id)!.x - padding, y: positions.get(node.id)!.y - padding, width: nodeWidth(node) + 2 * padding, height: NODE_HEIGHT + 2 * padding }))
      const length = distance(a, b) || 1, lane = (index - (group.length - 1) / 2) * 52
      const sign = compare(link.source, link.target) <= 0 ? 1 : -1
      const middle = { x: (a.x + b.x) / 2 - (b.y - a.y) / length * lane * sign, y: (a.y + b.y) / 2 + (b.x - a.x) / length * lane * sign }
      let inner = group.length > 1 ? [a, middle, b] : [a, b]
      if (segments(inner).some(([p, q]) => obstacles.some(rect => intersectsNode(p, q, rect)) || reserved.some(points => segments(points).some(([c, d]) => sharedLength(p, q, c, d) > 0.1)))) inner = detour(a, b, obstacles, reserved)
      if (!inner.length) return
      const points = simplify([source.point, ...inner, target.point])
      reserved.push(points)
      let label = along(points, 0.5), placed = false
      // Only the focused edge displays a label. A short link may have no space
      // between its nodes, so search nearby empty space instead of covering them.
      for (let radius = 0; radius <= 600 && !placed; radius += 40) {
        for (const fraction of [0.5, 0.35, 0.65]) {
          const anchor = along(points, fraction)
          for (const [dx, dy] of [[0, -radius], [0, radius], [-radius, 0], [radius, 0]]) {
            const candidate = { x: anchor.x + dx, y: anchor.y + dy }
            const box = { x: candidate.x - 180, y: candidate.y - 18, width: 360, height: 36 }
            if (!obstacles.some(other => overlaps(box, other))) { label = candidate; placed = true; break }
          }
          if (placed) break
        }
      }
      routes.set(link.id, { source, target, points, path: points.map((p, i) => `${i ? 'L' : 'M'} ${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' '), label })
    })
  }
  return { positions, ports, routes }
}
