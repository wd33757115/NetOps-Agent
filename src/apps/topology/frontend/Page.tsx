import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { Alert, Button, Checkbox, ConfigProvider, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, PlusOutlined } from '@ant-design/icons'
import { Background, BaseEdge, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow, useUpdateNodeInternals, type EdgeProps } from '@xyflow/react'
import { drawing, NODE_HEIGHT, nodeWidth, type Port, type Route, type Side } from './layout'
import '@xyflow/react/dist/style.css'
import { api } from '@netops/config/api'
import { useAuth } from '@netops/context/AuthContext'
import DeviceTerminal, { acceptHostKey, readHostKey, type HostKey } from '@netops/platform/terminal/DeviceTerminal'
import Located, { highlightIds, type LocateResult } from './Located'
import './topology.css'

// `active`: the owner's one active topology, the only network the AI diagnosis queries.
interface Network { id: string; name: string; active_run?: string; current_snapshot?: string; active?: boolean }
interface TopologyNode { id: string; name: string; address?: string; addresses?: string[]; identity_status: string; freshness: string; observed_at?: string; evidence_id?: string; members?: number; subnets?: string[]; tps: { id: string; name: string }[] }
interface Link { id: string; source: string; target: string; source_tp: string; target_tp: string; freshness: string; observed_at?: string; evidence_id?: string; link_type?: string; direction?: string; label?: string; ends?: string[]; subnets?: string[]; evidence?: string[]; state?: string; available?: boolean | null }
interface Graph { nodes: TopologyNode[]; links: Link[]; model_profile: string }
interface Target { id: string; address: string; depth: number; status: string; reason?: string; version: number; evidence_id?: string }
// The discovery form's choice for a new network; never sent to the server.
const NEW_NETWORK = '__new__'
interface Run { id: string; network_id: string; status: string; port?: number; snapshot_id?: string; graph: Graph; targets: Target[]; created_at: string }
interface History { id: string; status: string; created_at: string; snapshot_id?: string }
const labels: Record<string, string> = { queued: '排队中', running: '发现中', waiting_for_input: '等待人工处理', completed: '已完成', partial: '部分完成', cancelled: '已取消', succeeded: '采集成功', skipped: '人工跳过', observed: '本次观测', advertised: '邻居通告', not_observed: '本次未观测', absent_in_observation: '本次邻接表中未出现', candidate: '候选身份', address_scoped: '地址范围内身份', collected: '已采集', host: '未识别端点', endpoint: '未识别端点', segment: '共享网段', p2p: '点到点', mp2mp: '多点共享', bi: '双向', uni: '单向' }
const failures: Record<string, string> = { platform_unrecognized: '平台未识别，请确认此节点平台或补充适配', platform_ambiguous: '平台识别证据冲突，请人工确认', platform_unsupported: '此平台尚无已验证适配', authentication_failed: '用户名或密码不正确，请输入新的用户名密码', connection_failed: '无法连接设备，可重试或跳过', timeout: '连接或命令超时，可重试或跳过', credential_expired: '本次用户名密码已失效，请重新输入', credentials_required: '请输入用户名和密码', host_key_changed: '设备主机密钥已变化，请核对新指纹', host_key_rejected: '主机密钥未通过校验', parse_failed: '无法识别设备输出，请检查适配与证据', output_incomplete: '设备输出未完整返回', worker_interrupted: '执行中断，请人工确认后重试' }
const loginReasons = ['authentication_failed', 'credential_expired', 'credentials_required']
const profiles = [{ value: 'auto', label: '自动识别（无需预先知道型号）' }, { value: 'comware-lldp-v1', label: 'Comware · LLDP v1' }, { value: 'nxos-lldp-v1', label: 'NX-OS · LLDP v1' }]
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试'
Object.assign(labels, { known: '已知设备', historical: '历史观测', up: '运行中', down: '已断开', unavailable: '不可用', unknown: '状态未知', complete: '采集完成', failed: '采集失败' })
const detailLabels: Record<string, string> = { state: '运行状态', available: '是否可用', collection_status: '采集状态', historical: '历史关系', current_lldp_proven: '本次邻接已确认', state_sources: '端口状态依据', prior_snapshot_id: '原观测快照', prior_evidence: '原观测依据', last_seen: '最后确认时间', last_seen_source: '时间来源', network_id: '网络编号', scope: '关系范围' }
function detailValue(key: string, value: unknown) {
  if (key === 'available') return value === true ? '可用' : value === false ? '不可用' : '待确认'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (key === 'state_sources' && Array.isArray(value)) return value.map(s => `${s.address || s.node_id} ${s.interface_name || '端口未知'}：${labels[s.oper_status] || s.oper_status}（${s.observed_at || '无采样时间'}，证据 ${s.evidence_id || '缺失'}）`).join('；')
  return labels[String(value)] || (typeof value === 'object' ? JSON.stringify(value) : String(value))
}

const POSITIONS: Record<Side, Position> = { top: Position.Top, bottom: Position.Bottom, left: Position.Left, right: Position.Right }

function TopologyNodeView({ id, data }: { id: string; data: { label: React.ReactNode; ports: Port[] } }) {
  const updateNodeInternals = useUpdateNodeInternals()
  const signature = data.ports.map(port => `${port.id}:${port.side}:${port.offset}`).join('|')
  useEffect(() => { updateNodeInternals(id) }, [id, signature, updateNodeInternals])
  return <>{data.ports.map(port => <Handle key={port.id} type={port.type} id={port.id} position={POSITIONS[port.side]} isConnectable={false}
    style={port.side === 'left' || port.side === 'right' ? { top: port.offset } : { left: port.offset }} />)}{data.label}</>
}
const nodeTypes = { topology: TopologyNodeView }

function TopologyEdgeView({ id, data, style, markerStart, markerEnd }: EdgeProps) {
  const { route, title, active } = data as { route: Route; title: string; active: boolean }
  return <g data-route-id={id}>
    <title>{title}</title>
    <path d={route.path} fill="none" stroke="#f7fafc" strokeWidth={7} style={{ opacity: style?.opacity }} pointerEvents="none" />
    <BaseEdge id={id} path={route.path} style={style} markerStart={markerStart} markerEnd={markerEnd} interactionWidth={18}
      label={active ? title : undefined} labelX={route.label.x} labelY={route.label.y}
      labelStyle={{ fontSize: 12, fill: '#174765' }} labelBgStyle={{ fill: '#fff', fillOpacity: 0.96 }} labelBgPadding={[7, 4]} />
  </g>
}
const edgeTypes = { routed: TopologyEdgeView }

function AdjacentLinks({ node, graph, onLink }: { node: TopologyNode; graph: Graph; onLink: (link: Link) => void }) {
  return <><h3>相邻链路</h3><ul className="tp-adjacencies">{graph.links.filter(link => link.source === node.id || link.target === node.id).map(link => {
    const source = link.source === node.id
    const localPort = node.tps.find(tp => tp.id === (source ? link.source_tp : link.target_tp))?.name || '端口未知'
    const peer = graph.nodes.find(item => item.id === (source ? link.target : link.source))
    const remotePort = peer?.tps.find(tp => tp.id === (source ? link.target_tp : link.source_tp))?.name || '端口未知'
    return <li key={link.id}><button type="button" onClick={() => onLink(link)} aria-label={`${localPort} → ${peer?.name || '未知节点'} ${remotePort}`}><strong>{localPort} → {peer?.name || '未知节点'}</strong><span>{remotePort}{link.state ? ` · ${labels[link.state] || link.state}` : ''}</span></button></li>
  })}</ul></>
}

export function Canvas({ graph, highlight, selectedId, onNode, onLink, onClear }: { graph: Graph; highlight: Set<string>; selectedId?: string; onNode: (node: TopologyNode) => void; onLink: (link: Link) => void; onClear?: () => void }) {
  const { fitView } = useReactFlow()
  const plan = useMemo(() => drawing(graph.nodes, graph.links), [graph])
  const geometryKey = JSON.stringify([
    graph.nodes.map(node => [node.id, nodeWidth(node)]).sort(),
    graph.links.map(link => [link.id, link.source, link.target]).sort(),
  ])
  const [hover, setHover] = useState<{ kind: 'node' | 'edge'; id: string }>()
  const focus = hover ?? (selectedId ? { kind: graph.nodes.some(node => node.id === selectedId) ? 'node' : 'edge', id: selectedId } : undefined)
  const focusedEdges = new Set(focus ? graph.links.filter(link => focus.kind === 'edge' ? link.id === focus.id : link.source === focus.id || link.target === focus.id).map(link => link.id) : [])
  const focusedNodes = new Set(graph.links.filter(link => focusedEdges.has(link.id)).flatMap(link => [link.source, link.target]))
  const missingRoutes = graph.links.filter(link => !plan.routes.has(link.id))
  if (focus?.kind === 'node') focusedNodes.add(focus.id)
  // Hover rebuilds these objects. Without static size and handles React Flow drops the
  // measured handle bounds of every new node object and hides all edges until it
  // re-measures, which under the pointer re-triggers hover: the edges flicker.
  const nodes = graph.nodes.map(node => ({ id: node.id, type: 'topology',
    position: plan.positions.get(node.id) ?? { x: 0, y: 0 },
    width: nodeWidth(node), height: NODE_HEIGHT, measured: { width: nodeWidth(node), height: NODE_HEIGHT },
    handles: (plan.ports.get(node.id) ?? []).map(port => ({ id: port.id, type: port.type, position: POSITIONS[port.side], width: 1, height: 1,
      x: port.side === 'left' ? 0 : port.side === 'right' ? nodeWidth(node) : port.offset,
      y: port.side === 'top' ? 0 : port.side === 'bottom' ? NODE_HEIGHT : port.offset })),
    style: { width: nodeWidth(node), height: NODE_HEIGHT, opacity: focus && !focusedNodes.has(node.id) ? 0.32 : 1 },
    ariaLabel: `${node.name} · ${labels[node.identity_status]}`,
    data: { ports: plan.ports.get(node.id) ?? [], label: <><strong title={node.name}>{node.name}</strong><span title={node.address || node.addresses?.join(', ')}>{node.address || node.addresses?.join(', ') || '管理地址待确认'}</span><small>{labels[node.identity_status]} · {labels[node.freshness]}</small></> },
    className: (node.identity_status === 'segment' ? 'topology-node segment' : ['candidate', 'advertised'].includes(node.identity_status) ? 'topology-node candidate' : 'topology-node') + (highlight.has(node.id) || focusedNodes.has(node.id) ? ' located' : ''),
  }))
  const edges = graph.links.flatMap(link => {
    const route = plan.routes.get(link.id)
    if (!route) return []
    const find = (id: string) => graph.nodes.find(n => n.id === id)
    const activeEdge = focusedEdges.has(link.id) || highlight.has(link.source) && highlight.has(link.target)
    const title = [link.label ?? find(link.source)?.tps.find(tp => tp.id === link.source_tp)?.name, link.state && labels[link.state]].filter(Boolean).join(' · ')
    return [{ id: link.id, type: 'routed', source: link.source, target: link.target, sourceHandle: route.source.id, targetHandle: route.target.id,
      ariaLabel: `${find(link.source)?.name} — ${find(link.target)?.name} · ${title}`,
      data: { route, title, active: focus?.kind === 'edge' && focus.id === link.id }, zIndex: activeEdge ? 10 : 0,
      ...(link.direction === 'bi' && link.link_type === 'p2p' ? { markerStart: { type: MarkerType.ArrowClosed }, markerEnd: { type: MarkerType.ArrowClosed } } : {}),
      style: { stroke: link.available === false ? '#bc3b42' : activeEdge ? '#1671e8' : link.state === 'unknown' || link.freshness !== 'observed' ? '#8997a7' : '#477b8a', strokeWidth: activeEdge ? 3 : 1.6, opacity: focus && !focusedEdges.has(link.id) ? 0.12 : 1, strokeDasharray: link.freshness === 'observed' ? undefined : '5 5' },
    }]
  })
  useEffect(() => { const timer = setTimeout(() => void fitView({ padding: 0.12, maxZoom: 1.1 }), 80); return () => clearTimeout(timer) }, [fitView, geometryKey])
  return <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} nodesDraggable={false} nodesConnectable={false} minZoom={0.15} maxZoom={2}
    onNodeMouseEnter={(_, node) => setHover({ kind: 'node', id: node.id })} onNodeMouseLeave={() => setHover(undefined)}
    onEdgeMouseEnter={(_, edge) => setHover({ kind: 'edge', id: edge.id })} onEdgeMouseLeave={() => setHover(undefined)}
    onPaneClick={() => { setHover(undefined); onClear?.() }}
    onNodeClick={(_, node) => onNode(graph.nodes.find(n => n.id === node.id)!)}
    onEdgeClick={(_, edge) => onLink(graph.links.find(link => link.id === edge.id)!)} fitView fitViewOptions={{ padding: 0.12, maxZoom: 1.1 }}>
    <Background gap={24} color="#dce3e9" /><Controls showInteractive={false} />
    {missingRoutes.length > 0 && <div className="tp-route-warning" role="status">有 {missingRoutes.length} 条关系暂未绘入画布：{missingRoutes.map(link => <button key={link.id} type="button" onClick={() => onLink(link)}>{link.label || link.id}</button>)}。可点击逐条查看观测。</div>}
  </ReactFlow>
}

export default function Page() {
  const { user } = useAuth()
  const [networks, setNetworks] = useState<Network[]>([])
  const [networkId, setNetworkId] = useState<string>()
  const [history, setHistory] = useState<History[]>([])
  const [runId, setRunId] = useState<string>()
  const [run, setRun] = useState<Run>()
  const [create, setCreate] = useState(false)
  const [offline, setOffline] = useState(false)
  const [defaultLogin, setDefaultLogin] = useState<{ username: string | null; configured: boolean }>()
  const [editDefault, setEditDefault] = useState(false)
  const [hostKey, setHostKey] = useState<HostKey>()
  const [importText, setImportText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<TopologyNode | Link>()
  const [evidence, setEvidence] = useState<unknown>()
  const [target, setTarget] = useState<Target>()
  const [replaceCredentials, setReplaceCredentials] = useState(true)
  const [retryProfile, setRetryProfile] = useState('auto')
  const [terminal, setTerminal] = useState<string>()
  const [changes, setChanges] = useState<unknown>()
  const [located, setLocated] = useState<LocateResult>()
  const [locating, setLocating] = useState(false)
  const highlight = useMemo(() => highlightIds(located), [located])
  const locate = async (query: string) => {
    if (!run?.snapshot_id || !query.trim()) return
    setLocating(true)
    try { setLocated((await api.get<LocateResult>(`/apps/topology/snapshots/${run.snapshot_id}/locate`, { params: { q: query.trim() } })).data); setSelected(undefined) }
    catch (error) { message.error(errorMessage(error)) }
    finally { setLocating(false) }
  }
  const [form] = Form.useForm()
  const chosenNetwork = Form.useWatch('network_id', form)
  // An existing network keeps its name; "create a new network" is an explicit choice, not a
  // hidden clear icon, and starts from the default name rather than the last network's.
  const applyNetwork = (id?: string) => {
    const chosen = networks.find(n => n.id === id)
    form.setFieldsValue(chosen ? { network_id: chosen.id, name: chosen.name } : { network_id: NEW_NETWORK, name: '网络拓扑' })
  }
  const [retryForm] = Form.useForm()
  const [defaultForm] = Form.useForm()
  const refreshDefault = () => api.get<{ username: string | null; configured: boolean }>('/apps/topology/default-login').then(({ data }) => setDefaultLogin(data))
  const viewed = networks.find(n => n.id === networkId)
  const activate = async (network: Network) => {
    try {
      const { data } = await api.post<Network[]>(`/apps/topology/networks/${network.id}/activate`)
      setNetworks(data); message.success(`已将“${network.name}”设为活跃拓扑，AI 诊断将查询此网络`)
    } catch (error) { message.error(errorMessage(error)) }
  }
  const refreshNetworks = () => api.get<Network[]>('/apps/topology/networks').then(({ data }) => { setNetworks(data); setNetworkId(current => current || data.find(n => n.active)?.id || data[0]?.id) })
  useEffect(() => { void refreshNetworks().catch(error => setError(errorMessage(error))); void refreshDefault().catch(() => undefined) }, [])
  useEffect(() => {
    setRun(undefined); setSelected(undefined); setRunId(undefined); setLocated(undefined)
    if (!networkId) return
    let alive = true
    void api.get<History[]>(`/apps/topology/networks/${networkId}/runs`).then(({ data }) => { if (alive) { setHistory(data); setRunId(data[0]?.id) } }).catch(error => setError(errorMessage(error)))
    return () => { alive = false }
  }, [networkId])
  useEffect(() => {
    if (!runId) return
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const { data } = await api.get<Run>(`/apps/topology/runs/${runId}`)
        if (alive) {
          setRun(data); setError('')
          setHistory(items => items.map(item => item.id === data.id ? { ...item, status: data.status } : item))
          if (['queued', 'running', 'waiting_for_input'].includes(data.status)) timer = setTimeout(refresh, 3000)
          else void api.get<History[]>(`/apps/topology/networks/${data.network_id}/runs`).then(({ data }) => { if (alive) setHistory(data) })
        }
      } catch (error) { if (alive) setError(errorMessage(error)) }
    }
    void refresh()
    return () => { alive = false; clearTimeout(timer) }
  }, [runId])
  const active = run && ['queued', 'running', 'waiting_for_input'].includes(run.status)
  const waiting = useMemo(() => run?.targets.filter(t => t.status === 'waiting_for_input') || [], [run])
  const accept = async (value: Run) => {
    await refreshNetworks(); setNetworkId(value.network_id)
    const { data } = await api.get<History[]>(`/apps/topology/networks/${value.network_id}/runs`)
    setHistory(data); setRunId(value.id); setRun(value)
  }
  const readEvidence = async (id?: string) => { if (!id) return; try { setEvidence((await api.get(`/apps/topology/evidence/${id}`)).data) } catch (error) { message.error(errorMessage(error)) } }
  const openTarget = useCallback((row: Target) => {
    setTarget(row); setRetryProfile(row.platform?.source === 'manual' ? row.platform.profile_id || 'auto' : 'auto')
    setReplaceCredentials(loginReasons.includes(row.reason || '')); retryForm.resetFields(); setHostKey(undefined)
    if (row.reason === 'host_key_changed') void readHostKey(row.address, run?.port || 22).then(setHostKey).catch(error => message.error(errorMessage(error)))
  }, [retryForm, run?.port])
  // A node that needs a person asks right away, once per failed attempt; the table button reopens it.
  const prompted = useRef(new Set<string>())
  useEffect(() => {
    if (target) return
    const next = waiting.find(row => !prompted.current.has(`${row.id}:${row.version}`))
    if (next) { prompted.current.add(`${next.id}:${next.version}`); openTarget(next) }
  }, [waiting, target, openTarget])
  const resolve = async (action: 'retry' | 'skip') => {
    if (!target || !run) return
    setBusy(true)
    try {
      const credentials = action === 'retry' && replaceCredentials ? await retryForm.validateFields() : undefined
      if (action === 'retry' && hostKey) await acceptHostKey(hostKey)
      const { data } = await api.post<Run>(`/apps/topology/runs/${run.id}/targets/${target.id}/resolve`, { request_id: crypto.randomUUID(), version: target.version, action, credentials, profile_id: action === 'retry' ? retryProfile : undefined })
      setRun(data); setTarget(undefined); setHostKey(undefined); retryForm.resetFields()
    } catch (error) { message.error(errorMessage(error)) }
    finally { retryForm.setFieldValue('password', ''); setBusy(false) }
  }
  const pickRun = (id: string) => { setSelected(undefined); setLocated(undefined); setRunId(id) }
  const exportSnapshot = (id: string, devices = false) => void api.get(`/apps/topology/snapshots/${id}/${devices ? 'state' : 'rfc8345'}`).then(({ data }) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `${devices ? 'device-models' : 'topology'}-${id}.json`; a.click(); URL.revokeObjectURL(url)
  }).catch(error => message.error(errorMessage(error)))
  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 9, colorText: '#242b38', colorBorder: '#dce1e9', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 36 } }}>
  <div className="tp-app">
    <header className="tp-toolbar">
      <div className="tp-breadcrumb"><RouterLink className="tp-back" to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</RouterLink><span className="tp-breadcrumb-divider">/</span><span className="tp-toolbar-title">网络拓扑</span></div>
      <span className="tp-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span>
    </header>
    <main className="tp-window">
      <div className="tp-heading">
        <div><h1>网络拓扑</h1><p>发现网络，保留观测，逐步构建数字孪生。</p></div>
        <div className="tp-heading-actions">
          <Button onClick={() => { defaultForm.resetFields(); setEditDefault(true) }}>默认登录{defaultLogin?.configured ? ` · ${defaultLogin.username}` : ''}</Button>
          <Button onClick={() => setOffline(true)}>导入离线观测</Button>
          <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => { form.resetFields(); applyNetwork(networkId); setCreate(true) }}>新建发现任务</Button>
        </div>
      </div>
      {error && <Alert className="tp-alert" type="error" message={error} action={<Button size="small" onClick={() => { setError(''); void refreshNetworks().catch(error => setError(errorMessage(error))) }}>刷新</Button>} />}
      <div className="tp-tools">
        <Select aria-label="网络" placeholder="选择网络" value={networkId} onChange={setNetworkId} options={networks.map(n => ({ value: n.id, label: n.active ? `${n.name}（活跃）` : n.name }))} style={{ minWidth: 200 }} />
        {viewed && (viewed.active
          ? <Tag color="blue" title="AI 诊断只查询活跃拓扑的当前快照">活跃拓扑 · AI 查询此网络</Tag>
          : <Button title="AI 诊断只查询活跃拓扑；同一时间只有一个" onClick={() => void activate(viewed)}>设为活跃拓扑</Button>)}
        <Select aria-label="观测任务" placeholder="观测任务" value={runId} onChange={pickRun} options={history.map(h => ({ value: h.id, label: `${new Date(h.created_at).toLocaleString()} · ${labels[h.status] || h.status}` }))} style={{ minWidth: 270 }} />
        {run && <Tag color={active ? 'processing' : run.status === 'completed' ? 'success' : 'default'}>{labels[run.status]}</Tag>}
        <Input.Search className="tp-locate" aria-label="定位 IP 或 MAC" placeholder={run?.snapshot_id ? '定位 IP 或 MAC' : '任务完成后可定位'} enterButton="定位" loading={locating} disabled={!run?.snapshot_id} onSearch={value => void locate(value)} allowClear />
        {run?.snapshot_id && <Button icon={<DownloadOutlined aria-hidden="true" />} onClick={() => exportSnapshot(run.snapshot_id!)}>导出 RFC 8345 + SIMAP</Button>}
        {run?.snapshot_id && <Button icon={<DownloadOutlined aria-hidden="true" />} title="包含采样标准实例、证据和完整性差距" onClick={() => exportSnapshot(run.snapshot_id!, true)}>导出设备模型</Button>}
        {active && <Button danger onClick={() => void api.post(`/apps/topology/runs/${run.id}/cancel`).then(({ data }) => setRun(data)).catch(error => message.error(errorMessage(error)))}>停止任务</Button>}
      </div>
      {waiting.length > 0 && <Alert className="tp-alert" type="warning" showIcon message={`${waiting.length} 个节点等待人工处理；成功节点的观测已保留`} />}
      <div className="tp-stage"><section className="tp-canvas" aria-label="网络拓扑画布">
        {run?.graph.nodes.length ? <ReactFlowProvider><Canvas graph={run.graph} highlight={highlight} selectedId={selected?.id} onClear={() => setSelected(undefined)} onNode={node => { setLocated(undefined); setSelected(node) }} onLink={link => { setLocated(undefined); setSelected(link) }} /></ReactFlowProvider> : <div className="tp-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={run ? '尚无成功的拓扑观测，请查看下方节点状态' : '从一个管理地址开始发现，或导入已有 LLDP 输出'} /><p>每条链路都保留来源与观测时间。未确认的邻居以候选身份展示。</p></div>}
        {run && <div className="tp-map-caption">{run.graph.nodes.length} 个节点 · {run.graph.links.length} 条邻接记录 · L2/L3 拓扑（RFC 8345 + SIMAP）</div>}
      </section><aside className="tp-inspector"><h2>{located ? '定位结果' : selected ? '观测详情' : '阅读这张拓扑'}</h2>
        {located ? <Located result={located} onEvidence={id => void readEvidence(id)} onClear={() => setLocated(undefined)} /> : selected ? <>{'tps' in selected && run && <AdjacentLinks node={selected} graph={run.graph} onLink={setSelected} />}<dl>{Object.entries(selected).filter(([key]) => !['tps', 'id', 'source_tp', 'target_tp', 'source', 'target'].includes(key)).map(([key, value]) => <React.Fragment key={key}><dt>{({ name: '名称', address: '管理地址', addresses: '通告地址', identity_status: '身份状态', freshness: '新鲜度', observed_at: '观测时间', evidence_id: '证据编号', members: '端口数', subnets: '三层网段', link_type: '链路类型', evidence: '依据', direction: '方向', ends: '两端端口', label: '端口', ...detailLabels } as Record<string, string>)[key] || key}</dt><dd>{detailValue(key, value)}</dd></React.Fragment>)}</dl>
          {'tps' in selected && <><h3>终结点 / 端口</h3>{selected.tps.map(tp => <div key={tp.id}>{tp.name}</div>)}</>}
          <Space direction="vertical" style={{ marginTop: 16 }}><Button disabled={!selected.evidence_id} onClick={() => void readEvidence(selected.evidence_id)}>查看原始证据</Button>{'address' in selected && selected.address && <Button onClick={() => setTerminal(selected.address)}>打开 SSH 终端</Button>}</Space>
        </> : <><p>链路表示二层邻接，不代表一根实际线缆；共享网段表示同一介质上的多个端口，例如经过 Hub。已知设备失联时保留历史关系，以虚线显示；红色链路表示采样端口不可用，灰色表示状态待确认。</p><p>只被邻居通告、尚未采集的设备用虚线框显示。设备按自身报告的机箱 ID 识别，采集后与通告合并为同一节点。</p><p>点击节点或链路可查看观测时间与命令证据。</p></>}
        {run?.snapshot_id && <><h3>快照比较</h3><Select aria-label="比较基线" placeholder="选择较早的快照" style={{ width: '100%' }} options={history.filter(h => h.snapshot_id && h.id !== run.id).map(h => ({ value: h.snapshot_id!, label: new Date(h.created_at).toLocaleString() }))} onChange={before => void api.get(`/apps/topology/snapshots/${run.snapshot_id}/diff/${before}`).then(({ data }) => setChanges(data)).catch(error => message.error(errorMessage(error)))} /></>}
      </aside></div>
      <section className="tp-targets"><div className="tp-section-title"><h2>发现节点</h2><span>{run?.targets.length || 0} 个目标 · 重试由人工确认</span></div>
        <Table<Target> size="middle" rowKey="id" dataSource={run?.targets || []} pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 650 }} locale={{ emptyText: '还没有发现节点' }} columns={[
          { title: '管理地址', dataIndex: 'address' }, { title: '发现深度', dataIndex: 'depth', width: 100 },
          { title: '设备平台', render: (_, row) => row.platform?.profile_id ? `${profiles.find(p => p.value === row.platform?.profile_id)?.label || row.platform.profile_id} · ${row.platform.source === 'manual' ? '人工指定' : '自动识别'}` : '待识别' },
          { title: '状态', render: (_, row) => <><Tag color={row.status === 'waiting_for_input' ? 'warning' : row.status === 'succeeded' ? 'success' : 'default'}>{labels[row.status] || row.status}</Tag><span className="tp-reason">{failures[row.reason || ''] || row.reason}</span></> },
          { title: '操作', render: (_, row) => <Space>{row.status === 'waiting_for_input' && <Button size="small" onClick={() => openTarget(row)}>人工处理</Button>}<Button size="small" disabled={!row.evidence_id} onClick={() => void readEvidence(row.evidence_id)}>证据</Button><Button size="small" onClick={() => setTerminal(row.address)}>SSH</Button></Space> },
        ]} />
      </section>
    </main>
    <Drawer title="新建拓扑发现" open={create} onClose={() => { form.resetFields(); setCreate(false) }} destroyOnClose width={520}>
      <Form form={form} layout="vertical" initialValues={{ name: '网络拓扑', port: 22, profile_id: 'auto', max_depth: 3, max_targets: 30 }} onFinish={async values => {
        const { username, password, network_id, ...rest } = values
        if (!!username !== !!password) { message.error('本次用户名和密码需要同时填写，或都留空使用默认'); return }
        if (!username && !defaultLogin?.configured) { message.error('请填写本次用户名和密码，或先设置默认登录'); return }
        setBusy(true)
        try { const { data } = await api.post<Run>('/apps/topology/runs', { ...rest, ...(network_id && network_id !== NEW_NETWORK ? { network_id } : {}), ...(username ? { credentials: { username, password } } : {}), request_id: crypto.randomUUID(), seeds: values.seeds.split(/[\s,]+/).filter(Boolean), allowed_cidrs: values.allowed_cidrs.split(/[\s,]+/).filter(Boolean) }); await accept(data); setCreate(false); form.resetFields() }
        catch (error) { message.error(errorMessage(error)) }
        finally { form.setFieldValue('password', ''); setBusy(false) }
      }}>
        <Form.Item name="network_id" label="网络范围" extra="选择已有网络会在该网络下重新发现；选择“创建新网络”会新建一个网络"><Select onChange={value => applyNetwork(value)} options={[{ value: NEW_NETWORK, label: '＋ 创建新网络' }, ...networks.map(n => ({ value: n.id, label: n.name }))]} /></Form.Item>
        <Form.Item name="name" label="网络名称" rules={[{ required: true }]}><Input disabled={!!chosenNetwork && chosenNetwork !== NEW_NETWORK} /></Form.Item>
        <Form.Item name="seeds" label="种子设备地址" rules={[{ required: true }]} extra="多个 IP 以空格或换行分隔"><Input.TextArea rows={2} placeholder="10.10.1.1" /></Form.Item>
        <Form.Item name="allowed_cidrs" label="本任务允许的管理网段" rules={[{ required: true }]} extra="只发现此网段内的邻居"><Input placeholder="10.10.1.0/24" /></Form.Item>
        <Form.Item name="profile_id" label="种子设备平台（可选）" extra="默认自动识别。手工选择只适用于种子；每个新邻居都独立识别，不继承种子平台。"><Select options={profiles} /></Form.Item>
        <Space><Form.Item name="port" label="SSH 端口"><InputNumber min={1} max={65535} /></Form.Item><Form.Item name="max_depth" label="最大深度"><InputNumber min={0} max={16} /></Form.Item><Form.Item name="max_targets" label="目标上限"><InputNumber min={1} max={200} /></Form.Item></Space>
        <Form.Item name="username" label="本次用户名（可选）"><Input autoComplete="off" placeholder={defaultLogin?.configured ? `留空使用默认 ${defaultLogin.username}` : '尚未设置默认登录'} /></Form.Item><Form.Item name="password" label="本次密码（可选）"><Input.Password autoComplete="new-password" /></Form.Item>
        <p>本次用户名密码只用于这次任务、不保存；本次所有节点（包括新发现的邻居）都使用同一组。某个节点登录失败时，会提示为该节点输入新的用户名密码。</p><Button type="primary" htmlType="submit" loading={busy}>开始发现</Button>
      </Form>
    </Drawer>
    <Modal open={!!target} title={`处理节点 · ${target?.address || ''}`} onCancel={() => { retryForm.resetFields(); setTarget(undefined) }} footer={<Space><Button onClick={() => void resolve('skip')} disabled={busy}>跳过此节点</Button><Button type="primary" loading={busy} onClick={() => void resolve('retry')}>确认并重试一次</Button></Space>} destroyOnClose>
      <Alert type="warning" message={failures[target?.reason || ''] || target?.reason} style={{ marginBottom: 16 }} /><div style={{ marginBottom: 16 }}><Typography.Text>仅此节点的平台</Typography.Text><Select aria-label="仅此节点的平台" value={retryProfile} onChange={setRetryProfile} options={profiles} style={{ width: '100%', marginTop: 8 }} /><p>无法识别或尚未支持的设备可保留候选节点并跳过；此选择不会传给邻居。</p></div>{hostKey && <Alert type="error" showIcon message="主机密钥已变化" description={<>已保存：<code>{hostKey.fingerprint}</code><br />设备当前：<code>{hostKey.observed_fingerprint}</code><br />核对无误后点“确认并重试一次”，将替换为新密钥。</>} style={{ marginBottom: 16 }} />}<Checkbox checked={replaceCredentials} onChange={event => setReplaceCredentials(event.target.checked)}>为此节点输入新的用户名和密码</Checkbox>
      {replaceCredentials && <Form form={retryForm} layout="vertical" style={{ marginTop: 16 }}><Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item><Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item></Form>}
    </Modal>
    <Modal open={editDefault} title="默认登录" okText="保存" cancelText="取消" confirmLoading={busy} onCancel={() => { defaultForm.resetFields(); setEditDefault(false) }} destroyOnClose
      onOk={() => void defaultForm.validateFields().then(async values => {
        setBusy(true)
        try { await api.put('/apps/topology/default-login', values); await refreshDefault(); setEditDefault(false) }
        catch (error) { message.error(errorMessage(error)) }
        finally { defaultForm.resetFields(); setBusy(false) }
      }).catch(() => undefined)}>
      <p>新建发现任务时不填写本次用户名密码，就使用这一组。密码加密保存，不会显示。{defaultLogin?.configured && `当前：${defaultLogin.username}`}</p>
      <Form form={defaultForm} layout="vertical" preserve={false}><Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item><Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item></Form>
    </Modal>
    <Drawer title="导入离线 LLDP 观测" open={offline} onClose={() => setOffline(false)} width={660}><p>JSON 包含 profile_id 和 captures。每项需提供 address、含时区的 observed_at、outputs.neighbors 与 complete。离线数据不会触发设备登录。</p><Input.TextArea aria-label="离线观测 JSON" rows={18} value={importText} onChange={event => setImportText(event.target.value)} placeholder={'{"profile_id":"comware-lldp-v1","captures":[{"address":"10.10.1.1","observed_at":"2026-09-28T08:00:00+08:00","outputs":{"neighbors":"完整 LLDP 命令输出"},"complete":true}]}'} /><Button type="primary" style={{ marginTop: 16 }} loading={busy} onClick={async () => { setBusy(true); try { const { data } = await api.post('/apps/topology/imports', { ...JSON.parse(importText), request_id: crypto.randomUUID() }); await accept(data); setOffline(false); setImportText('') } catch (error) { message.error(errorMessage(error)) } finally { setBusy(false) } }}>校验并导入</Button></Drawer>
    <Drawer title="原始观测证据" open={evidence !== undefined} onClose={() => setEvidence(undefined)} width={760}><pre className="topology-raw">{JSON.stringify(evidence, null, 2)}</pre></Drawer>
    <Drawer title="快照差异" open={changes !== undefined} onClose={() => setChanges(undefined)} width={600}><p>差异包含对象、新鲜度和观测时间变化；未观测不等于已经删除。</p><pre className="topology-raw">{JSON.stringify(changes, null, 2)}</pre></Drawer>
    <DeviceTerminal open={!!terminal} onClose={() => setTerminal(undefined)} address={terminal} />
  </div>
  </ConfigProvider>
}
