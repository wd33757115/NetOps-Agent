import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Input, Space, Spin, Typography } from 'antd'
import { ReloadOutlined, DownloadOutlined, SendOutlined, UndoOutlined } from '@ant-design/icons'
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeChange } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '@netops/config/api'
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
  if (/AR\d/.test(text) || text.includes('ROUTER')) return 'router'
  if (/CE\d/.test(text) || /S\d{3,4}/.test(text) || text.includes('SWITCH')) return 'switch'
  return 'generic'
}

const TIER: Record<Kind, number> = { cloud: 0, router: 1, firewall: 2, switch: 3, server: 4, pc: 4, generic: 3 }
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

const DeviceNodeView = ({ data }: { data: { kind: Kind; name: string; model: string; address?: string } }) => {
  const official = ICON_PNGS[data.kind]
  return (
    <div style={{ width: 150, textAlign: 'center' }}>
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        {official ? (
          <img src={`data:image/png;base64,${official}`} alt={data.kind} style={{ width: 84, height: 'auto', display: 'block' }} />
        ) : (
          <span style={{ display: 'inline-flex' }} dangerouslySetInnerHTML={{ __html: ICON_SVGS[data.kind] }} />
        )}
      </div>
      <div style={{ fontWeight: 600, fontSize: 14, color: '#1f2d3d', marginTop: 4 }}>{data.name}</div>
      {data.model && <div style={{ fontSize: 11, color: '#7a8ba6' }}>{data.model}</div>}
      {data.address && <div style={{ fontSize: 11, color: '#7a8ba6' }}>{data.address}</div>}
      <Handle type="target" position={Position.Top} style={{ visibility: 'hidden' }} />
      <Handle type="source" position={Position.Bottom} style={{ visibility: 'hidden' }} />
      <Handle type="target" position={Position.Left} style={{ visibility: 'hidden' }} />
      <Handle type="source" position={Position.Right} style={{ visibility: 'hidden' }} />
    </div>
  )
}
const nodeTypes = { device: DeviceNodeView }

interface ChatEntry {
  role: 'user' | 'ai' | 'error'
  text: string
}

function Page() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [networkName, setNetworkName] = useState('')
  const [networkId, setNetworkId] = useState('')
  const [snapshotId, setSnapshotId] = useState('')
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [chat, setChat] = useState<ChatEntry[]>([])
  const [chatInput, setChatInput] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [versions, setVersions] = useState<{ xml: string; label: string; time: string }[]>([])
  const frameRef = useRef<HTMLIFrameElement>(null)
  const sourceXmlRef = useRef<string | null>(null)

  const iframeSrc = '/drawio/?embed=1&proto=json&configure=1&ui=simple&spin=0&libraries=0&noSaveBtn=1&noExitBtn=1&saveAndExit=0&dark=0&p=huawei'

  const sendLoad = useCallback(() => {
    if (!sourceXmlRef.current) return
    frameRef.current?.contentWindow?.postMessage(
      JSON.stringify({ action: 'load', autosave: 1, fit: 1, xml: resolveHuaweiStyles(sourceXmlRef.current) }),
      window.location.origin,
    )
  }, [])

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
        sourceXmlRef.current = buildDrawioXml(graph, modelByAddress)
        setVersions([{ xml: sourceXmlRef.current, label: '发现基线', time: new Date().toLocaleTimeString() }])
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
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [sendLoad])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes(current =>
      current.map(node => {
        const move = changes.find(change => change.id === node.id && change.type === 'position')
        return move && 'position' in move && move.position ? { ...node, position: move.position } : node
      }),
    )
  }, [])

  const applyXml = (xml: string, label: string) => {
    sourceXmlRef.current = xml
    sendLoad()
    setVersions(current => [...current, { xml, label, time: new Date().toLocaleTimeString() }])
  }

  const sendChat = async () => {
    const instruction = chatInput.trim()
    if (!instruction || aiLoading || !sourceXmlRef.current) return
    setChat(current => [...current, { role: 'user', text: instruction }])
    setChatInput('')
    setAiLoading(true)
    try {
      const { data } = await api.post<{ xml: string }>('/apps/topology-draw/ai-edit', {
        instruction,
        xml: sourceXmlRef.current,
        network_id: networkId,
      })
      applyXml(data.xml, `AI: ${instruction.slice(0, 24)}`)
      setChat(current => [...current, { role: 'ai', text: '已更新拓扑图，可在画布查看。' }])
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

  const body = useMemo(
    () => (
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          nodesConnectable={false}
          minZoom={0.2}
          maxZoom={2}
          fitView
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
    ),
    [nodes, edges, onNodesChange],
  )

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
                const previous = versions[versions.length - 2]
                if (previous) applyXml(previous.xml, `回退到 ${previous.label}`)
              }}
            >
              撤销上一次 AI 修改
            </Button>
          )}
          <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={() => sendLoad()}>
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
          ) : nodes.length ? (
            body
          ) : (
            <Empty description="当前快照没有节点" style={{ paddingTop: 80 }} />
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
            {chat.map((entry, index) => (
              <div
                key={index}
                style={{
                  margin: '6px 0',
                  padding: '6px 8px',
                  borderRadius: 6,
                  background: entry.role === 'user' ? '#e6f4ff' : entry.role === 'error' ? '#fff1f0' : '#f6ffed',
                  whiteSpace: 'pre-wrap',
                  fontSize: 13,
                }}
              >
                {entry.text}
              </div>
            ))}
            {aiLoading && <Spin size="small" />}
          </div>
          <div style={{ borderTop: '1px solid #e5e9f0' }}>
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
