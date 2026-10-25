import { normalizeWallpaper, type DesktopWallpaper } from './wallpaper'
import { appRegistry } from '../../apps/appRegistry'

export interface DesktopNode {
  id: string
  kind: 'app' | 'folder'
  appId?: string
  name?: string
  containerId: string
  col?: number
  row?: number
  order?: number
}
export interface DesktopLayout {
  schemaVersion: 1
  revision: number
  nodes: DesktopNode[]
  wallpaper?: DesktopWallpaper
  dockAppIds: string[]
}
export const defaultLayout = (): DesktopLayout => ({
  schemaVersion: 1,
  revision: 0,
  nodes: appRegistry.filter(app => app.enabled && app.showOnDesktop).map((app, col) => ({
    id: app.id,
    kind: 'app',
    appId: app.id,
    containerId: 'desktop:0',
    col,
    row: 0,
  })),
  dockAppIds: appRegistry.filter(app => app.enabled && ['patrol', 'patrol-rules'].includes(app.id)).map((app) => app.id),
})
export function firstFree(nodes: DesktopNode[], columns = 6) {
  for (let index = 0; index < 20000; index++) {
    const col = index % columns,
      row = Math.floor(index / columns)
    if (
      !nodes.some(
        (n) => n.containerId === 'desktop:0' && n.col === col && n.row === row,
      )
    )
      return { col, row }
  }
  throw new Error('桌面已满')
}
export function reconcileLayout(input: DesktopLayout): DesktopLayout {
  if (
    input.schemaVersion !== 1 ||
    !Array.isArray(input.nodes) ||
    !Array.isArray(input.dockAppIds)
  )
    throw new Error('布局格式不受支持')
  const nodes: DesktopNode[] = [],
    ids = new Set<string>(),
    apps = new Set<string>()
  const folders = new Set(
    input.nodes.filter((n) => n.kind === 'folder').map((n) => `folder:${n.id}`),
  )
  for (const raw of input.nodes) {
    if (
      !raw.id ||
      ids.has(raw.id) ||
      (raw.kind !== 'app' && raw.kind !== 'folder')
    )
      continue
    if (raw.kind === 'app' && (!raw.appId || apps.has(raw.appId))) continue
    if (raw.kind === 'app' && appRegistry.some(app => app.id === raw.appId && !app.showOnDesktop)) continue
    const n = { ...raw }
    if (n.kind === 'folder' || !folders.has(n.containerId))
      n.containerId = 'desktop:0'
    if (n.containerId === 'desktop:0') {
      if (
        !Number.isInteger(n.col) ||
        !Number.isInteger(n.row) ||
        n.col! < 0 ||
        n.row! < 0 ||
        nodes.some(
          (p) =>
            p.containerId === 'desktop:0' && p.col === n.col && p.row === n.row,
        )
      )
        Object.assign(n, firstFree(nodes))
      delete n.order
    } else {
      n.order = nodes.filter((p) => p.containerId === n.containerId).length
      delete n.col
      delete n.row
    }
    nodes.push(n)
    ids.add(n.id)
    if (n.appId) apps.add(n.appId)
  }
  for (const app of appRegistry) {
    if (!app.enabled || !app.showOnDesktop) continue
    if (!apps.has(app.id)) {
      let id = `app-${app.id}`
      while (nodes.some((node) => node.id === id)) id += '-new'
      nodes.push({
        id,
        kind: 'app',
        appId: app.id,
        containerId: 'desktop:0',
        ...firstFree(nodes),
      })
    }
  }
  return {
    schemaVersion: 1,
    revision: input.revision,
    wallpaper: normalizeWallpaper(input.wallpaper),
    nodes,
    dockAppIds: [...new Set(input.dockAppIds)].filter((id) =>
      nodes.some((n) => n.appId === id),
    ),
  }
}
export function moveNode(
  layout: DesktopLayout,
  id: string,
  containerId: string,
  position?: { col: number; row: number },
  beforeId?: string,
): DesktopLayout {
  const nodes = layout.nodes.map((n) => ({ ...n })),
    node = nodes.find((n) => n.id === id)
  if (!node || (node.kind === 'folder' && containerId !== 'desktop:0'))
    return layout
  if (
    containerId !== 'desktop:0' &&
    !nodes.some((n) => n.kind === 'folder' && `folder:${n.id}` === containerId)
  )
    return layout
  if (containerId === 'desktop:0') {
    const target = position || firstFree(nodes.filter((n) => n.id !== id))
    const occupied = nodes.find(
      (n) =>
        n.id !== id &&
        n.containerId === containerId &&
        n.col === target.col &&
        n.row === target.row,
    )
    if (occupied) {
      const replacement =
        node.containerId === 'desktop:0'
          ? { col: node.col!, row: node.row! }
          : firstFree(nodes)
      Object.assign(occupied, replacement)
    }
    Object.assign(node, target, { containerId })
    delete node.order
  } else {
    const siblings = nodes
      .filter((n) => n.containerId === containerId && n.id !== id)
      .sort((a, b) => (a.order || 0) - (b.order || 0))
    const before = siblings.findIndex((n) => n.id === beforeId)
    siblings.splice(before < 0 ? siblings.length : before, 0, node)
    node.containerId = containerId
    delete node.col
    delete node.row
    siblings.forEach((n, order) => {
      n.order = order
    })
  }
  // Normalize the source folder after a cross-container move.
  for (const folder of nodes.filter((n) => n.kind === 'folder'))
    nodes
      .filter((n) => n.containerId === `folder:${folder.id}`)
      .sort((a, b) => (a.order || 0) - (b.order || 0))
      .forEach((n, order) => {
        n.order = order
      })
  return { ...layout, nodes }
}
export function dissolveFolder(layout: DesktopLayout, id: string) {
  let result = { ...layout, nodes: layout.nodes.filter((n) => n.id !== id) }
  for (const node of result.nodes.filter(
    (n) => n.containerId === `folder:${id}`,
  ))
    result = moveNode(result, node.id, 'desktop:0')
  return result
}

export function desktopColumns(width: number) {
  return Math.max(2, Math.min(100, Math.floor(width / 112)))
}
