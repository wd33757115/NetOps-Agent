import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Space, Spin, Typography } from 'antd'
import { ReloadOutlined, DownloadOutlined, SendOutlined, UndoOutlined } from '@ant-design/icons'
import { api, getAccessToken } from '@netops/config/api'
import { ICON_PNGS } from './iconPngs'
import { ICON_SVGS, type Kind } from './icons'

// 拓扑绘图 = 拓扑发现数据 → mxGraph（短 ID）→ 自托管 draw.io 画布 + AI 对话编辑。
// 状态约定：sourceXml（短 ID 形式）是唯一权威状态；draw.io 的 autosave 经逆向解析
// 回流为短 ID；AI 编辑也产出短 ID。内嵌解析只在灌进 draw.io 的那一刻发生。

interface GraphNode {
  id: string
  name?: string
  addresses?: string[]
  identity_status?: string
  tps?: { id: string; name: string }[]
  address?: string
  freshness?: string
  model_family?: string
}
interface GraphLink {
  id: string
  source: string
  target: string
  label?: string
  link_type?: string
}
interface Graph {
  nodes: GraphNode[]
  links: GraphLink[]
}
interface RunTarget {
  address: string
  platform?: { identity?: { vendor?: string; model?: string; os_family?: string } }
}
interface RunRead {
  id: string
  snapshot_id: string | null
  targets: RunTarget[]
}
interface NetworkRow {
  id: string
  name: string
  current_snapshot: string | null
  active: boolean
}

const kindOfModel = (model?: string): Kind => {
  const text = (model || '').toUpperCase()
  if (text.includes('USG') || text.includes('FIREWALL')) return 'firewall'
  if (text.includes('CE12800')) return 'ce12800'
  if (/AR\d/.test(text) || text.includes('ROUTER')) return 'router'
  if (/\bAC\d/.test(text)) return 'ac'
  if (/\bAP\d/.test(text)) return 'ap'
  if (/CE\d/.test(text) || /S\d{3,4}/.test(text) || text.includes('SWITCH')) return 'switch'
  if (text.includes('ELB') || text.includes('LOADBALANCE') || text.includes('LOAD BALANCE')) return 'lb'
  return 'generic'
}

const TIER: Record<Kind, number> = {
  cloud: 0,
  router: 1,
  firewall: 2,
  ce12800: 3,
  switch: 3,
  access: 3,
  aggregate: 3,
  stacked: 3,
  stacked_ce: 3,
  ac: 3,
  ap: 3,
  lb: 3,
  server: 4,
  pc: 4,
  laptop: 4,
  generic: 3,
}
const TIER_HEIGHT = 180

const xmlEscape = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const resolveHuaweiStyles = (xml: string): string => {
  let resolved = xml
  for (const [kind, png] of Object.entries(ICON_PNGS)) {
    resolved = resolved.replace(
      new RegExp(`shape=huawei\\.${kind}(?=[;" ])`, 'g'),
      `shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image=data:image/png,${png}`,
    )
  }
  for (const [kind, svg] of Object.entries(ICON_SVGS)) {
    if (!ICON_PNGS[kind]) {
      resolved = resolved.replace(
        new RegExp(`shape=huawei\\.${kind}(?=[;" ])`, 'g'),
        `shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image=data:image/svg+xml,${encodeURIComponent(svg)}`,
      )
    }
  }
  return resolved
}

const unresolveHuaweiStyles = (xml: string): string => {
  let out = xml
  for (const [kind, png] of Object.entries(ICON_PNGS)) {
    out = out.replace(
      new RegExp(
        `shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image=data:image/png,${png}`,
        'g',
      ),
      `shape=huawei.${kind}`,
    )
  }
  for (const [kind, svg] of Object.entries(ICON_SVGS)) {
    if (!ICON_PNGS[kind]) {
      out = out.replace(
        new RegExp(
          `shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image=data:image/svg\\+xml,${svg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`,
          'g',
        ),
        `shape=huawei.${kind}`,
      )
    }
  }
  return out
}

const buildDrawioXml = (graph: Graph, modelByAddress: Map<string, string>): string => {
  const cells: string[] = []
  const tiers = new Map<number, string[]>()
  const kindByNode = new Map<string, Kind>()
  for (const node of graph.nodes) {
    const model = node.model_family || (node.address ? modelByAddress.get(node.address) : undefined)
    const kind: Kind = node.id.includes(':host:')
      ? 'pc'
      : node.identity_status === 'collected'
        ? kindOfModel(model || node.name)
        : 'generic'
    kindByNode.set(node.id, kind)
    const tier = TIER[kind]
    tiers.set(tier, [...(tiers.get(tier) || []), node.id])
  }
  const layout = new Map<string, { x: number; y: number }>()
  for (const [tier, ids] of [...tiers.entries()].sort((a, b) => a[0] - b[0])) {
    ids.sort((a, b) => a.localeCompare(b))
    ids.forEach((id, index) => layout.set(id, { x: 80 + index * 240, y: 60 + tier * TIER_HEIGHT }))
  }
  for (const node of graph.nodes) {
    const kind = kindByNode.get(node.id) || 'generic'
    const model = node.model_family || (node.address ? modelByAddress.get(node.address) || '' : '')
    const address = node.address || node.addresses?.[0] || ''
    const label = [node.name || node.id.split(':').pop() || node.id, model, address]
      .filter(Boolean)
      .map(xmlEscape)
      .join('&#10;')
    const style = `shape=huawei.${kind};verticalLabelPosition=bottom;verticalAlign=top;`
    const position = layout.get(node.id) || { x: 80, y: 60 }
    cells.push(
      `<mxCell id="${xmlEscape(node.id)}" value="${label}" style="${style}" vertex="1" parent="1">` +
        `<mxGeometry x="${Math.round(position.x)}" y="${Math.round(position.y)}" width="96" height="72" as="geometry"/></mxCell>`,
    )
  }
  for (const link of graph.links) {
    const label = link.label || ''
    cells.push(
      `<mxCell id="${xmlEscape(link.id)}" value="${xmlEscape(label)}" ` +
        'style="endArrow=open;html=1;rounded=0;strokeColor=#7a8ba6;" ' +
        `edge="1" parent="1" source="${xmlEscape(link.source)}" target="${xmlEscape(link.target)}">` +
        '<mxGeometry relative="1" as="geometry"/></mxCell>',
    )
  }
  return (
    '<mxfile host="netops-topology-draw"><diagram id="topology" name="拓扑">' +
    '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" ' +
    'page="1" pageScale="1" pageWidth="1169" pageHeight="826">' +
    `<root><mxCell id="0"/><mxCell id="1" parent="0"/>${cells.join('')}</root>` +
    '</mxGraphModel></diagram></mxfile>'
  )
}

interface ChatEntry {
  role: 'user' | 'ai' | 'error' | 'process'
  text: string
}

// 形状面板条目的中文名（kind → 标签）。
const PALETTE_LABELS: Record<string, string> = {
  router: '路由器',
  switch: '交换机',
  firewall: '防火墙',
  pc: 'PC',
  laptop: '笔记本',
  server: '服务器',
  cloud: '云',
  generic: '通用',
  ce12800: 'CE12800',
  ac: '无线控制器',
  ap: '无线AP',
  lb: '负载均衡',
  stacked: '堆叠交换机',
  stacked_ce: '堆叠CE',
  access: '接入交换机',
  aggregate: '汇聚交换机',
}

// 画布桥需要的最小 draw.io 接口面（same-origin 直连，缺方法就跳过）。
interface DrawSelectionModel {
  cells: { value?: string }[]
  addListener: (event: unknown, fn: () => void) => void
}
interface DrawGraph {
  model?: { getCell?: (id: string) => unknown }
  getSelectionModel?: () => DrawSelectionModel
  setSelectionCells?: (cells: unknown[]) => void
  scrollCellToVisible?: (cell: unknown) => void
}
interface DrawSidebar {
  addPalette?: (id: string, title: string, expanded: boolean, build: (content: HTMLElement) => void) => void
  createVertexTemplate?: (style: string, width: number, height: number, value: string, allowEdges: boolean) => HTMLElement
}
interface DrawEditorUi {
  editor?: { graph?: DrawGraph }
  sidebar?: DrawSidebar
  resetScrollbars?: () => void
  actions?: { get: (name: string) => { funct?: () => void } }
}

// 按单元格 id 切分 XML（保留几何等子元素），供两次画布状态对比。
const cellMap = (xml: string): Map<string, string> => {
  const map = new Map<string, string>()
  for (const chunk of xml.split(/(?=<mxCell)/)) {
    const id = chunk.match(/^<mxCell[^>]*\bid="([^"]+)"/)?.[1]
    if (id) map.set(id, chunk.replace(/^<mxCell[^>]*\bid="[^"]+"/, ''))
  }
  return map
}

// 改动高亮：新增或被修改（value/style/端点/位置变化）的单元格 id。
const diffCellIds = (before: string, after: string): string[] => {
  const prev = cellMap(before)
  const changed: string[] = []
  for (const [id, body] of cellMap(after)) {
    if (prev.get(id) !== body) changed.push(id)
  }
  return changed
}

function Page() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [networkName, setNetworkName] = useState('')
  const [networkId, setNetworkId] = useState('')
  const [snapshotId, setSnapshotId] = useState('')
  const [chat, setChat] = useState<ChatEntry[]>([])
  const [chatInput, setChatInput] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [versions, setVersions] = useState<{ id: string; label: string; source: string; createdAt: string | null }[]>([])
  const [selection, setSelection] = useState<string[]>([])
  const frameRef = useRef<HTMLIFrameElement>(null)
  const sourceXmlRef = useRef<string | null>(null)
  const highlightRef = useRef<string[]>([])

  // 画布 → 宿主的钩子：选中感知（attachBridge 在 iframe 里回调）。
  useEffect(() => {
    const host = window as unknown as Record<string, unknown>
    host.__onDrawSelection = (labels: string[]) => setSelection(labels)
    return () => {
      delete host.__onDrawSelection
    }
  }, [])

  // 面板不走插件路线（见 attachBridge 注释）：清掉此前试错写进 draw.io 批准
  // 列表的残留条目，避免哪天 ALLOW_CUSTOM_PLUGINS 打开后重复挂两份面板。
  try {
    const key = '.drawio-config'
    const config = JSON.parse(localStorage.getItem(key) || '{}')
    if (Array.isArray(config.plugins) && config.plugins.length) {
      const cleaned = config.plugins.filter((name: string) => name !== 'hw' && name !== 'plugins/hw.js')
      if (cleaned.length !== config.plugins.length) {
        localStorage.setItem(key, JSON.stringify({ ...config, plugins: cleaned }))
      }
    }
  } catch { /* 存储不可用就跳过 */ }

  const iframeSrc = '/drawio/?embed=1&proto=json&configure=1&ui=simple&spin=0&libraries=0&noSaveBtn=1&noExitBtn=1&saveAndExit=0&dark=0'

  const sendLoad = () => {
    if (!sourceXmlRef.current) return
    frameRef.current?.contentWindow?.postMessage(
      JSON.stringify({ action: 'load', autosave: 1, fit: 1, xml: resolveHuaweiStyles(sourceXmlRef.current) }),
      window.location.origin,
    )
    // 灌图是异步的（embed load 不触发 fileLoaded）：轮询到画布装好新图后执行挂起的高亮。
    const ids = highlightRef.current
    if (!ids.length) return
    highlightRef.current = []
    const started = Date.now()
    const timer = window.setInterval(() => {
      const graph = (frameRef.current?.contentWindow as (Window & { __drawioUi?: DrawEditorUi }) | undefined)
        ?.__drawioUi?.editor?.graph
      const first = graph?.model?.getCell?.(ids[0])
      if (first) {
        window.clearInterval(timer)
        const cells = ids.map(id => graph.model.getCell(id)).filter(Boolean)
        if (cells.length) {
          graph.setSelectionCells?.(cells)
          graph.scrollCellToVisible?.(cells[0])
        }
      } else if (Date.now() - started > 10000) {
        // 画布接口缺席（非同源）或灌图失败：放弃本次高亮。
        window.clearInterval(timer)
      }
    }, 200)
  }

  // 编辑器桥：iframe 同源（/drawio 代理），挂钩 draw.io 的 fileLoaded/updateActionStates，
  // 首次调用即拿到 EditorUi：重置滚动条并 fit 页面（对照 next-ai-draw-io 的 editor-bridge）。
  // 选中感知通过宿主钩子 __onDrawSelection 回传（embed 的 load 路径不触发 fileLoaded，
  // 所以改动高亮放在 sendLoad 的轮询里做）。
  const attachBridge = (frame: HTMLIFrameElement) => {
    try {
      const win = frame.contentWindow as (Window & { EditorUi?: unknown; mxEvent?: { CHANGE?: unknown }; __drawioBridged?: boolean })
      if (!win || !win.EditorUi || win.__drawioBridged) return
      win.__drawioBridged = true
      let ui: unknown = null
      const parent = win.parent as unknown as {
        __onDrawSelection?: (labels: string[]) => void
      }
      const hook = (proto: unknown, method: string) => {
        const target = proto as Record<string, ((this: unknown, ...args: unknown[]) => void) | undefined>
        if (!target || typeof target[method] !== 'function') return
        const original = target[method]!
        const wrapped = function (this: unknown, ...args: unknown[]) {
          if (!ui) {
            ui = this
            const editorUi = ui as DrawEditorUi
            try {
              ;(win as unknown as { __drawioUi?: DrawEditorUi }).__drawioUi = editorUi
            } catch { /* 只读环境就跳过 */ }
            try {
              editorUi.resetScrollbars?.()
            } catch { /* 老版本缺方法就跳过 */ }
            try {
              editorUi.actions?.get('fitWindow')?.funct?.()
            } catch { /* 同上 */ }
            // 选中感知：画布选中的单元格标签回传宿主（AI 编辑可以对着选中项说话）。
            try {
              const graph = editorUi.editor?.graph
              const selection = graph?.getSelectionModel?.()
              if (selection && win.mxEvent?.CHANGE) {
                selection.addListener(win.mxEvent.CHANGE, () => {
                  const labels = (selection.cells || [])
                    .map(cell => String(cell.value || '').split('\n').filter(Boolean).join(' · '))
                    .filter(Boolean)
                  parent.__onDrawSelection?.(labels)
                })
              }
            } catch { /* 同上 */ }
            // 华为设备形状面板：宿主直连注入（draw.io 插件机制有 registry 白名单、
            // 批准状态、脚本缓存三道坎，同源直连一并绕开）。条目样式与灌图路径同款，
            // 拖入画布后 autosave 回流能被 unresolveHuaweiStyles 还原成短 ID。
            try {
              const sidebar = editorUi.sidebar
              const paletteWin = win as unknown as { __huaweiPalette?: boolean }
              if (sidebar?.addPalette && sidebar.createVertexTemplate && !paletteWin.__huaweiPalette) {
                paletteWin.__huaweiPalette = true
                // 本版签名是 4 参（id, title, expanded, callback），无 onInit——
                // 多传 null 会占掉回调位，标题建出来后内容构建直接抛错。
                sidebar.addPalette('netopsHuawei', '华为设备', true, content => {
                  content.style.padding = '4px'
                  for (const [kind, label] of Object.entries(PALETTE_LABELS)) {
                    const png = ICON_PNGS[kind]
                    if (!png) continue
                    const style =
                      'shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;' +
                      `image=data:image/png,${png}`
                    content.appendChild(sidebar.createVertexTemplate!(style, 96, 72, label, true))
                  }
                })
                // 默认形状库有几百项且异步装载（addPalette 的标题节点延迟创建，
                // 同步查找会扑空）：轮询把华为面板顶到便笺本之前（列表前部还有
                // 搜索容器，不能插到 index 0），稳定 3 次后停止。
                try {
                  const deadline = Date.now() + 15000
                  let settled = 0
                  const timer = window.setInterval(() => {
                    try {
                      if (Date.now() > deadline) {
                        window.clearInterval(timer)
                        return
                      }
                      const doc = win.document
                      const title = [...doc.querySelectorAll('.geTitle')].find(
                        el => el.textContent?.trim() === '华为设备',
                      )
                      const list = title?.parentElement
                      if (!title || !list) return
                      const anchor = list.children[2]
                      if (anchor && anchor !== title && [...list.children].indexOf(title) > 2) {
                        const content = title.nextElementSibling
                        list.insertBefore(title, anchor)
                        if (content) list.insertBefore(content, title.nextSibling)
                        settled = 0
                      } else if (++settled >= 3) {
                        window.clearInterval(timer)
                      }
                    } catch {
                      window.clearInterval(timer)
                    }
                  }, 300)
                } catch { /* 位置调整失败不影响面板本身 */ }
              }
            } catch { /* sidebar 接口缺席就跳过 */ }
          }
          const result = original.apply(this, args)
          return result
        }
        target[method] = wrapped
      }
      const proto = (win.EditorUi as { prototype: unknown }).prototype
      hook(proto, 'updateActionStates')
      hook(proto, 'fileLoaded')
    } catch {
      // 非同源时直连不可用：退回纯 postMessage 协议
    }
  }

  // 生成基线（从发现快照重建）并落库；重灌按钮与首载共用。
  const buildBaseline = async (network: { id: string; current_snapshot: string }) => {
    const { data: graph } = await api.get<Graph>(`/apps/topology/snapshots/${network.current_snapshot}`)
    const { data: runs } = await api.get<{ id: string; snapshot_id: string | null }[]>(
      `/apps/topology/networks/${network.id}/runs`,
    )
    const run = runs.find(r => r.snapshot_id === network.current_snapshot)
    const modelByAddress = new Map<string, string>()
    if (run) {
      const { data: runRead } = await api.get<RunRead>(`/apps/topology/runs/${run.id}`)
      for (const target of runRead.targets) {
        const model = target.platform?.identity?.model
        if (target.address && model) modelByAddress.set(target.address, model)
      }
    }
    const xml = buildDrawioXml(graph, modelByAddress)
    sourceXmlRef.current = xml
    await api.post('/apps/topology-draw/diagram/save', {
      network_id: network.id,
      xml,
      label: '发现基线',
      source: 'baseline',
    })
  }

  const refreshVersions = async (id: string) => {
    const { data } = await api.get<{
      xml: string | null
      currentVersionId: string | null
      versions: { id: string; label: string; source: string; createdAt: string | null }[]
    }>(`/apps/topology-draw/diagram?network_id=${encodeURIComponent(id)}`)
    setVersions(data.versions ?? [])
    return data
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const { data: networks } = await api.get<NetworkRow[]>('/apps/topology/networks')
        const network = networks.find(n => n.active) || networks[0]
        if (!alive) return
        if (!network?.current_snapshot) {
          setError('活跃网络还没有快照：先在拓扑发现应用里完成一次发现。')
          setLoading(false)
          return
        }
        setNetworkName(network.name)
        setNetworkId(network.id)
        setSnapshotId(network.current_snapshot)

        // 图资产优先：服务端有当前图就用它（跨浏览器/刷新不丢），
        // 没有才从发现快照生成基线并落库。
        const { data: diagram } = await api.get<{
          xml: string | null
          versions: { id: string; label: string; source: string; createdAt: string | null }[]
        }>(`/apps/topology-draw/diagram?network_id=${encodeURIComponent(network.id)}`)
        if (!alive) return
        if (diagram.xml) {
          sourceXmlRef.current = diagram.xml
          setVersions(diagram.versions ?? [])
        } else {
          await buildBaseline(network)
          const { data: saved } = await refreshVersions(network.id)
          sourceXmlRef.current = saved.xml
          setVersions(saved.versions ?? [])
        }
        if (!alive) return
        setLoading(false)
      } catch (exc) {
        if (!alive) return
        setError(exc instanceof Error ? exc.message : String(exc))
        setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  // draw.io embed 协议：init 置就绪（谁后到谁触发灌图），configure 回配置，
  // autosave 持续回传手工编辑（逆向解析为短 ID 权威形式）。
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return
      if (event.origin !== window.location.origin) return
      if (typeof event.data !== 'string') return
      let message: { event?: string; action?: string; xml?: string } = {}
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.event === 'init') {
        sendLoad()
      } else if (message.event === 'configure') {
        frameRef.current?.contentWindow?.postMessage(
          JSON.stringify({
            action: 'configure',
            config: { defaultPageVisible: true, defaultGridEnabled: true, noAutoFocus: true },
          }),
          window.location.origin,
        )
      } else if (message.event === 'autosave' && typeof message.xml === 'string') {
        sourceXmlRef.current = unresolveHuaweiStyles(message.xml)
        // 程序化灌图后的首次回流不是手工编辑：抑制一次。
        if (suppressManualSave.current) {
          suppressManualSave.current = false
          return
        }
        // 手工拖拽成果落库（防抖 3s，拖拽的 autosave 很密集）。
        if (sourceXmlRef.current) scheduleManualSave(sourceXmlRef.current)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [sendLoad])

  // 程序化灌图（AI/回退/基线）后的首次 autosave 是灌图内容的回流，不是手工
  // 编辑：抑制一次，避免同内容被重复存成 manual 版本。
  const suppressManualSave = useRef(false)
  const loadIntoCanvas = (xml: string) => {
    sourceXmlRef.current = xml
    suppressManualSave.current = true
    sendLoad()
  }

  // 落库一个新版本（AI 成功/手工拖拽/重灌），并刷新服务端版本列表。
  const saveVersion = async (xml: string, label: string, source: 'ai' | 'manual' | 'baseline' | 'import') => {
    const { data } = await api.post<{ versionId: string }>('/apps/topology-draw/diagram/save', {
      network_id: networkId,
      xml,
      label,
      source,
    })
    const { data: diagram } = await api.get<{
      xml: string | null
      versions: { id: string; label: string; source: string; createdAt: string | null }[]
    }>(`/apps/topology-draw/diagram?network_id=${encodeURIComponent(networkId)}`)
    setVersions(diagram.versions ?? [])
    return data.versionId
  }

  // 恢复历史版本：服务端复制成新版本（历史不可变），前端灌回画布。
  const restoreVersion = async (versionId: string) => {
    const { data } = await api.post<{ versionId: string; xml: string }>('/apps/topology-draw/diagram/restore', {
      network_id: networkId,
      version_id: versionId,
    })
    loadIntoCanvas(data.xml)
    const { data: diagram } = await api.get<{
      versions: { id: string; label: string; source: string; createdAt: string | null }[]
    }>(`/apps/topology-draw/diagram?network_id=${encodeURIComponent(networkId)}`)
    setVersions(diagram.versions ?? [])
  }

  // 手工拖拽防抖落库：3s 无新 autosave 才存一次（拖拽的 autosave 很密集）。
  const manualSaveTimer = useRef<number | null>(null)
  const scheduleManualSave = (xml: string) => {
    if (manualSaveTimer.current) window.clearTimeout(manualSaveTimer.current)
    manualSaveTimer.current = window.setTimeout(() => {
      void saveVersion(xml, `手工拖拽 ${new Date().toLocaleTimeString()}`, 'manual').catch(() => undefined)
    }, 3000)
  }

  const sendChat = async () => {
    const instruction = chatInput.trim()
    if (!instruction || aiLoading || !sourceXmlRef.current) return
    setChat(current => [...current, { role: 'user', text: instruction }])
    setChatInput('')
    setAiLoading(true)
    // 选中感知：指令附带画布当前选中，模型可以对着选中项说话。
    const scoped = selection.length ? `${instruction}\n（画布当前选中：${selection.join('；')}）` : instruction
    const token = getAccessToken()
    let finished = false
    try {
      // SSE 流式：思考增量实时追加到"思考过程"条目，完成后灌图+落库提示。
      const response = await fetch('/api/apps/topology-draw/ai-edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ instruction: scoped, xml: sourceXmlRef.current, network_id: networkId }),
      })
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail || `编辑服务不可用（${response.status}）`)
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let reasoningLive = ''
      let explanation = ''
      const consume = (block: string) => {
        let event = 'message'
        const lines: string[] = []
        for (const line of block.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart())
        }
        if (!lines.length) return
        const data = JSON.parse(lines.join('\n')) as {
          type: string
          text?: string
          message?: string
          xml?: string
          explanation?: string
        }
        if (data.type === 'reasoning' && data.text) {
          // 思考增量：实时追加到最后一条"思考过程"条目里。
          reasoningLive += data.text
          setChat(current => {
            const next = [...current]
            const last = next[next.length - 1]
            if (last && last.role === 'process') next[next.length - 1] = { ...last, text: reasoningLive }
            else next.push({ role: 'process', text: reasoningLive })
            return next
          })
        } else if (data.type === 'done' && data.xml) {
          finished = true
          explanation = data.explanation?.trim() || ''
          // 改动高亮：对比编辑前后的单元格，灌图完成后选中展示。
          highlightRef.current = diffCellIds(sourceXmlRef.current || '', data.xml)
          loadIntoCanvas(data.xml)
          // 成功即落库为 ai 版本（后端已存，这里只刷新版本列表）。
          void api
            .get<{ versions: { id: string; label: string; source: string; createdAt: string | null }[] }>(
              `/apps/topology-draw/diagram?network_id=${encodeURIComponent(networkId)}`,
            )
            .then(({ data: diagram }) => setVersions(diagram.versions ?? []))
            .catch(() => undefined)
          setChat(current => [
            ...current,
            { role: 'ai', text: explanation || '已更新拓扑图，可在画布查看。' },
          ])
        } else if (data.type === 'error') {
          throw new Error(data.message || '本次编辑未完成')
        }
      }
      let done = false
      while (!done) {
        const { done: eof, value } = await reader.read()
        done = eof
        buffer += decoder.decode(value, { stream: !done })
        const parts = buffer.split(/\r?\n\r?\n/)
        buffer = parts.pop() || ''
        parts.forEach(consume)
        if (done) {
          if (buffer.trim()) consume(buffer)
          break
        }
      }
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
      if (!finished) throw new Error('连接已中断，本轮编辑未完成；请重试。')
    } catch (exc) {
      const detail = exc instanceof Error ? exc.message : String(exc)
      setChat(current => [...current, { role: 'error', text: `编辑失败：${detail}` }])
    } finally {
      setAiLoading(false)
    }
  }

  const exportDrawio = () => {
    const resolved = resolveHuaweiStyles(sourceXmlRef.current || '')
    const blob = new Blob([resolved], { type: 'application/vnd.jgraph.mxfile' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${networkName || '拓扑'}.drawio`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={{ height: 'calc(100vh - 96px)', display: 'flex', flexDirection: 'column' }}>
      <Typography.Title level={3} style={{ margin: '8px 0 4px' }}>
        拓扑绘图
      </Typography.Title>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Text type="secondary">
          {networkName
            ? `${networkName} · 快照 ${snapshotId.slice(0, 8)} · AI 对话编辑 + 手工拖拽`
            : '读取拓扑发现应用的当前快照'}
        </Typography.Text>
        <Space>
          {versions.length > 1 && (
            <Button
              icon={<UndoOutlined aria-hidden="true" />}
              onClick={() => {
                // 服务端版本列表按时间倒序：[0]=当前，[1]=上一个。
                const previous = versions[1]
                if (previous) void restoreVersion(previous.id)
              }}
            >
              撤销上一次 AI 修改
            </Button>
          )}
          {versions.length > 1 && (
            <Select
              value={versions[0]?.id}
              style={{ minWidth: 200, maxWidth: 280 }}
              options={versions.map(version => ({
                value: version.id,
                label: `${version.label.slice(0, 18)} ${version.createdAt ? new Date(version.createdAt).toLocaleTimeString() : ''}`,
              }))}
              onChange={(versionId: string) => {
                if (versionId !== versions[0]?.id) void restoreVersion(versionId)
              }}
            />
          )}
          <Button
            icon={<ReloadOutlined aria-hidden="true" />}
            onClick={() => {
              void (async () => {
                await buildBaseline({ id: networkId, current_snapshot: snapshotId })
                const { data: diagram } = await api.get<{
                  xml: string | null
                  versions: { id: string; label: string; source: string; createdAt: string | null }[]
                }>(`/apps/topology-draw/diagram?network_id=${encodeURIComponent(networkId)}`)
                setVersions(diagram.versions ?? [])
                if (diagram.xml) loadIntoCanvas(diagram.xml)
              })()
            }}
          >
            按当前快照重灌
          </Button>
          <Button icon={<DownloadOutlined aria-hidden="true" />} onClick={exportDrawio}>
            导出 .drawio
          </Button>
        </Space>
      </div>
      <div style={{ flex: 1, minHeight: 0, marginTop: 8, display: 'flex', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0, border: '1px solid #e5e9f0', borderRadius: 8, overflow: 'hidden' }}>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
              <Spin tip="正在生成拓扑图" />
            </div>
          ) : error ? (
            <Alert type="warning" showIcon message="没有可展示的拓扑" description={error} style={{ margin: 24 }} />
          ) : (
            <iframe
              ref={frameRef}
              title="draw.io 拓扑画布"
              src={iframeSrc}
              onLoad={event => attachBridge(event.target as HTMLIFrameElement)}
              style={{ width: '100%', height: '100%', border: 'none' }}
            />
          )}
        </div>
        <div
          style={{
            width: 320,
            flexShrink: 0,
            border: '1px solid #e5e9f0',
            borderRadius: 8,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <div style={{ padding: '8px 12px', borderBottom: '1px solid #e5e9f0', fontWeight: 600 }}>AI 编辑</div>
          <div style={{ flex: 1, overflow: 'auto', padding: '8px 12px' }}>
            {chat.length === 0 && (
              <Typography.Text type="secondary">
                用自然语言改拓扑图，例如：把 USG-1 标成红色、加一台核心交换机。
              </Typography.Text>
            )}
            {chat.map((entry, index) =>
              entry.role === 'process' ? (
                <details key={index} style={{ margin: '6px 0', fontSize: 12, color: '#5b6b85' }}>
                  <summary style={{ cursor: 'pointer', userSelect: 'none' }}>🧠 思考过程</summary>
                  <div
                    style={{
                      marginTop: 4,
                      padding: '6px 8px',
                      borderRadius: 6,
                      background: '#f4f6fa',
                      whiteSpace: 'pre-wrap',
                      maxHeight: 220,
                      overflow: 'auto',
                    }}
                  >
                    {entry.text}
                  </div>
                </details>
              ) : (
                <div
                  key={index}
                  style={{
                    margin: '6px 0',
                    padding: '6px 8px',
                    borderRadius: 6,
                    background:
                      entry.role === 'user' ? '#e6f4ff' : entry.role === 'error' ? '#fff1f0' : '#f6ffed',
                    whiteSpace: 'pre-wrap',
                    fontSize: 13,
                  }}
                >
                  {entry.text}
                </div>
              ),
            )}
            {aiLoading && <Spin size="small" />}
          </div>
          <div style={{ borderTop: '1px solid #e5e9f0' }}>
            {selection.length > 0 && (
              <div style={{ padding: '4px 12px', fontSize: 12, color: '#1677ff', background: '#f0f7ff' }}>
                已选中：{selection.join('；')}
              </div>
            )}
            <Input.TextArea
              rows={2}
              value={chatInput}
              placeholder="描述要怎么改拓扑图…"
              onChange={event => setChatInput(event.target.value)}
              onPressEnter={event => {
                if (!event.shiftKey) {
                  event.preventDefault()
                  void sendChat()
                }
              }}
              style={{ border: 'none' }}
            />
            <div style={{ padding: '4px 8px 8px', textAlign: 'right' }}>
              <Button type="primary" loading={aiLoading} onClick={() => void sendChat()}>
                发送
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Page
