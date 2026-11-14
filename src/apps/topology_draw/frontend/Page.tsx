import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Empty, Spin, Typography } from 'antd'
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeChange } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '@netops/config/api'
import { DeviceIcon, type Kind } from './icons'

// 展示型画布：读拓扑发现应用的公开接口（活跃网络的当前快照），按设备身份的
// model 字段映射华为图标。手工拖拽的位置存 localStorage（按网络 id），刷新不丢。

interface GraphNode {
  id: string
  name?: string
  addresses?: string[]
  identity_status?: string
  tps?: { id: string; name: string }[]
  address?: string
  freshness?: string
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
  network_id?: string
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

type ModelKind = Kind

const kindOfModel = (model?: string): ModelKind => {
  const text = (model || '').toUpperCase()
  if (text.includes('USG') || text.includes('FIREWALL')) return 'firewall'
  if (/AR\d/.test(text) || text.includes('ROUTER')) return 'router'
  if (/CE\d/.test(text) || /S\d{3,4}/.test(text) || text.includes('SWITCH')) return 'switch'
  if (text.includes('SERVER')) return 'server'
  return 'generic'
}

const TIER: Record<ModelKind, number> = {
  cloud: 0,
  router: 1,
  firewall: 2,
  switch: 3,
  server: 4,
  pc: 4,
  generic: 3,
}
const TIER_HEIGHT = 180

const positionsKey = (networkId: string) => `topology-draw:layout:${networkId}`
const loadPositions = (networkId: string): Record<string, { x: number; y: number }> => {
  try {
    return JSON.parse(localStorage.getItem(positionsKey(networkId)) || '{}')
  } catch {
    return {}
  }
}

const DeviceNodeView = ({ data }: { data: { kind: ModelKind; name: string; model: string; address?: string } }) => (
  <div style={{ width: 150, textAlign: 'center', background: 'transparent' }}>
    <div style={{ display: 'flex', justifyContent: 'center' }}>
      <DeviceIcon kind={data.kind} />
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
const nodeTypes = { device: DeviceNodeView }

function Page() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [networkName, setNetworkName] = useState('')
  const [networkId, setNetworkId] = useState('')
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [snapshotId, setSnapshotId] = useState('')

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

        const saved = loadPositions(network.id)
        const kindByNode = new Map<string, ModelKind>()
        for (const node of graph.nodes) {
          const host = node.id.includes(':host:')
          const model = node.address ? modelByAddress.get(node.address) : undefined
          const kind: ModelKind = host
            ? 'pc'
            : node.identity_status === 'collected'
              ? kindOfModel(model || node.name)
              : 'generic'
          kindByNode.set(node.id, kind)
        }

        // 分层布局：按类型排层级，层内按名称横向铺开；人工拖过的位置优先。
        const tiers = new Map<number, string[]>()
        for (const node of graph.nodes) {
          const tier = TIER[kindByNode.get(node.id) || 'generic']
          tiers.set(tier, [...(tiers.get(tier) || []), node.id])
        }
        const layout = new Map<string, { x: number; y: number }>()
        for (const [tier, ids] of [...tiers.entries()].sort((a, b) => a[0] - b[0])) {
          ids.sort((a, b) => a.localeCompare(b))
          ids.forEach((id, index) => layout.set(id, { x: 80 + index * 230, y: 60 + tier * TIER_HEIGHT }))
        }

        const flowNodes: Node[] = graph.nodes.map(node => {
          const kind = kindByNode.get(node.id) || 'generic'
          const position = saved[node.id] || layout.get(node.id) || { x: 80, y: 60 }
          const address = node.address || node.addresses?.[0]
          return {
            id: node.id,
            type: 'device',
            position,
            data: {
              kind,
              name: node.name || node.id.split(':').pop() || node.id,
              model: node.address ? modelByAddress.get(node.address) || '' : '',
              address: node.identity_status === 'endpoint' ? node.name : address,
            },
          }
        })
        const flowEdges: Edge[] = graph.links.map(link => ({
          id: link.id,
          source: link.source,
          target: link.target,
          label: link.label || undefined,
          type: 'smoothstep',
          style: { stroke: link.link_type === 'p2p' ? '#7a8ba6' : '#c7000b', strokeWidth: 2 },
        }))
        if (!alive) return
        setNodes(flowNodes)
        setEdges(flowEdges)
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

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes(current => {
        const next = current.map(node => {
          const move = changes.find(change => change.id === node.id && change.type === 'position')
          return move && 'position' in move && move.position ? { ...node, position: move.position } : node
        })
        if (networkId) {
          const positions = Object.fromEntries(next.map(node => [node.id, node.position]))
          try {
            localStorage.setItem(positionsKey(networkId), JSON.stringify(positions))
          } catch {
            // 私有模式下的持久化失败不阻塞展示。
          }
        }
        return next
      })
    },
    [networkId],
  )

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
      <Typography.Text type="secondary">
        {networkName ? `${networkName} · 快照 ${snapshotId.slice(0, 8)} · 拖拽后的位置自动保存` : '读取拓扑发现应用的当前快照'}
      </Typography.Text>
      <div style={{ flex: 1, minHeight: 0, marginTop: 8, border: '1px solid #e5e9f0', borderRadius: 8, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
            <Spin tip="正在读取当前快照" />
          </div>
        ) : error ? (
          <Alert type="warning" showIcon message="没有可展示的拓扑" description={error} style={{ margin: 24 }} />
        ) : nodes.length ? (
          body
        ) : (
          <Empty description="当前快照没有节点" style={{ paddingTop: 80 }} />
        )}
      </div>
    </div>
  )
}

export default Page
