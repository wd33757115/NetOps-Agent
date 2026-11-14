import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Empty, Space, Spin, Typography } from 'antd'
import { ReloadOutlined, DownloadOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import { ICON_PNGS } from './iconPngs'
import { ICON_SVGS, type Kind } from './icons'

// 拓扑绘图 = 拓扑发现数据 → mxGraph 生成器 → 自托管 draw.io 画布。
// 本应用不持有画布：展示和编辑都在 draw.io 里（embed 协议注入 XML，autosave 回传 XML）。

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
  if (/AR\d/.test(text) || text.includes('ROUTER')) return 'router'
  if (/CE\d/.test(text) || /S\d{3,4}/.test(text) || text.includes('SWITCH')) return 'switch'
  return 'generic'
}

const TIER: Record<Kind, number> = { cloud: 0, router: 1, firewall: 2, switch: 3, server: 4, pc: 4, generic: 3 }
const TIER_HEIGHT = 180

const xmlEscape = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

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
    const official = ICON_PNGS[kind]
    const style =
      'shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;' +
      // draw.io 的 image 样式惯例：data:image/png,<base64>（不带 ;base64 标记）
      (official ? `image=data:image/png,${official}` : `image=data:image/svg+xml,${encodeURIComponent(ICON_SVGS[kind])}`)
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

function Page() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [networkName, setNetworkName] = useState('')
  const [snapshotId, setSnapshotId] = useState('')
  const [savedXml, setSavedXml] = useState<string | null>(null)
  const [xmlReady, setXmlReady] = useState(false)
  const [drawioReady, setDrawioReady] = useState(false)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const xmlRef = useRef<string | null>(null)

  // 同源代理（/drawio → 127.0.0.1:8080）：iframe 与宿主同源，解锁编辑器直连（视图适配）。
  const iframeSrc = '/drawio/?embed=1&proto=json&ui=min'

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
        setSnapshotId(network.current_snapshot)

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
        if (!alive) return
        xmlRef.current = buildDrawioXml(graph, modelByAddress)
        setXmlReady(true)
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

  // 编辑器桥：同源时挂钩 draw.io 的 fileLoaded/updateActionStates，首次调用即拿到
  // EditorUi：重置滚动条并 fit 页面（对照 next-ai-draw-io 的 editor-bridge）。
  const attachBridge = (frame: HTMLIFrameElement) => {
    try {
      const win = frame.contentWindow as (Window & { EditorUi?: unknown; __drawioBridged?: boolean })
      if (!win || !win.EditorUi || win.__drawioBridged) return
      win.__drawioBridged = true
      let ui: unknown = null
      const hook = (proto: unknown, method: string) => {
        const target = proto as Record<string, ((this: unknown, ...args: unknown[]) => void) | undefined>
        if (!target || typeof target[method] !== 'function') return
        const original = target[method]!
        const wrapped = function (this: unknown, ...args: unknown[]) {
          if (!ui) {
            ui = this
            const editorUi = ui as { resetScrollbars?: () => void; actions?: { get: (name: string) => { funct?: () => void } } }
            try {
              editorUi.resetScrollbars?.()
            } catch { /* 老版本缺方法就跳过 */ }
            try {
              editorUi.actions?.get('fitWindow')?.funct?.()
            } catch { /* 同上 */ }
          }
          return original.apply(this, args)
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

  // draw.io embed 协议：init → 置就绪，configure → 回配置；autosave 持续回传编辑结果。
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
        setDrawioReady(true)
      } else if (message.event === 'configure') {
        frameRef.current?.contentWindow?.postMessage(
          JSON.stringify({ action: 'configure', config: { defaultLibraries: 'networks' } }),
          window.location.origin,
        )
      } else if (message.event === 'autosave' && typeof message.xml === 'string') {
        setSavedXml(message.xml)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  const sendLoad = () => {
    if (!xmlRef.current) return
    frameRef.current?.contentWindow?.postMessage(
      JSON.stringify({ action: 'load', autosave: 1, fit: 1, xml: xmlRef.current }),
      window.location.origin,
    )
  }

  // 双条件就绪才灌图：draw.io 的 init 与数据链生成完成，谁后到谁触发。
  useEffect(() => {
    if (drawioReady && xmlReady) sendLoad()
  })

  const reload = () => sendLoad()

  const download = (text: string, name: string) => {
    const blob = new Blob([text], { type: 'application/vnd.jgraph.mxfile' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = name
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
            ? `${networkName} · 快照 ${snapshotId.slice(0, 8)} · 画布为自托管 draw.io，编辑经 autosave 回传`
            : '读取拓扑发现应用的当前快照'}
        </Typography.Text>
        <Space>
          <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={reload}>
            按当前快照重灌
          </Button>
          <Button
            icon={<DownloadOutlined aria-hidden="true" />}
            disabled={!savedXml && !xmlRef.current}
            onClick={() => download(savedXml || xmlRef.current || '', `${networkName || '拓扑'}.drawio`)}
          >
            下载 .drawio
          </Button>
        </Space>
      </div>
      <div style={{ flex: 1, minHeight: 0, marginTop: 8, border: '1px solid #e5e9f0', borderRadius: 8, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
            <Spin tip="正在生成拓扑图" />
          </div>
        ) : error ? (
          <Alert type="warning" showIcon message="没有可展示的拓扑" description={error} style={{ margin: 24 }} />
        ) : !xmlReady ? (
          <Empty description="当前快照没有节点" style={{ paddingTop: 80 }} />
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
    </div>
  )
}

export default Page
