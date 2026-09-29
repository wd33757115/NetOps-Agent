import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import { layout, sides, type Side } from './layout'
import '@xyflow/react/dist/style.css'
import { api } from '@netops/config/api'
import DeviceTerminal from '@netops/platform/terminal/DeviceTerminal'
import { useAuth } from '@netops/context/AuthContext'
import AccessScopes from './AccessScopes'
import './topology.css'

interface Network { id: string; name: string; domain: string; active_run?: string; current_snapshot?: string }
interface TopologyNode { id: string; name: string; address?: string; addresses?: string[]; identity_status: string; freshness: string; observed_at?: string; evidence_id?: string; members?: number; subnets?: string[]; tps: { id: string; name: string }[] }
interface Link { id: string; source: string; target: string; source_tp: string; target_tp: string; freshness: string; observed_at?: string; evidence_id?: string; link_type?: string; direction?: string; label?: string; ends?: string[]; subnets?: string[]; evidence?: string[] }
interface Graph { nodes: TopologyNode[]; links: Link[]; domain: string; model_profile: string }
interface Target { id: string; address: string; depth: number; status: string; reason?: string; version: number; evidence_id?: string }
interface Run { id: string; network_id: string; status: string; snapshot_id?: string; graph: Graph; targets: Target[]; created_at: string }
interface History { id: string; status: string; created_at: string; snapshot_id?: string }
const labels: Record<string, string> = { queued: '排队中', running: '发现中', waiting_for_input: '等待人工处理', completed: '已完成', partial: '部分完成', cancelled: '已取消', succeeded: '采集成功', skipped: '人工跳过', observed: '本次观测', advertised: '邻居通告', not_observed: '本次未观测', absent_in_observation: '本次邻接表中未出现', candidate: '候选身份', address_scoped: '地址范围内身份', collected: '已采集', host: '终端主机', segment: '共享网段', p2p: '点到点', mp2mp: '多点共享', bi: '双向', uni: '单向' }
const failures: Record<string, string> = { platform_unrecognized: '平台未识别，请确认此节点平台或补充适配', platform_ambiguous: '平台识别证据冲突，请人工确认', platform_unsupported: '此平台尚无已验证适配', authentication_failed: '用户名或密码不正确', connection_failed: '无法连接设备', timeout: '连接或命令超时', credential_expired: '凭据已过期', credentials_required: '请为新节点提供凭据', host_key_rejected: '请管理员核验主机密钥', parse_failed: '无法识别设备输出，请检查适配与证据', output_incomplete: '设备输出未完整返回', not_authorized: '超出当前账号设备授权范围', worker_interrupted: '执行中断，请人工确认后重试' }
const profiles = [{ value: 'auto', label: '自动识别（无需预先知道型号）' }, { value: 'comware-lldp-v1', label: 'Comware · LLDP v1' }, { value: 'nxos-lldp-v1', label: 'NX-OS · LLDP v1' }]
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试'

const SIDES: [Side, Position][] = [['top', Position.Top], ['bottom', Position.Bottom], ['left', Position.Left], ['right', Position.Right]]

// Handles on every side so each edge can use the sides that face each other.
function TopologyNodeView({ data }: { data: { label: React.ReactNode } }) {
  return <>{SIDES.map(([side, position]) => <React.Fragment key={side}>
    <Handle type="source" id={`${side}-s`} position={position} isConnectable={false} />
    <Handle type="target" id={`${side}-t`} position={position} isConnectable={false} />
  </React.Fragment>)}{data.label}</>
}
const nodeTypes = { topology: TopologyNodeView }

function Canvas({ graph, onNode, onLink }: { graph: Graph; onNode: (node: TopologyNode) => void; onLink: (link: Link) => void }) {
  const { fitView } = useReactFlow()
  const positions = useMemo(() => layout(graph.nodes, graph.links), [graph])
  const nodes = useMemo(() => graph.nodes.map(node => ({ id: node.id, type: 'topology',
    position: positions.get(node.id) ?? { x: 0, y: 0 },
    ariaLabel: `${node.name} · ${labels[node.identity_status]}`,
    data: { label: <><strong>{node.name}</strong><span>{node.address || node.addresses?.join(', ') || '管理地址待确认'}</span><small>{labels[node.identity_status]} · {labels[node.freshness]}</small></> },
    className: node.identity_status === 'segment' ? 'topology-node segment' : ['candidate', 'advertised'].includes(node.identity_status) ? 'topology-node candidate' : 'topology-node',
  })), [graph.nodes, positions])
  const edges = useMemo(() => graph.links.map(link => {
    const find = (id: string) => graph.nodes.find(n => n.id === id)
    const [from, to] = sides(positions.get(link.source) ?? { x: 0, y: 0 }, positions.get(link.target) ?? { x: 0, y: 0 }, find(link.source), find(link.target))
    return { id: link.id, source: link.source, target: link.target, sourceHandle: `${from}-s`, targetHandle: `${to}-t`,
      // SIMAP links carry both endpoints; older snapshots only name the source port.
      label: link.label ?? find(link.source)?.tps.find(tp => tp.id === link.source_tp)?.name,
      labelBgPadding: [4, 2] as [number, number], labelStyle: { fontSize: 11 },
      ...(link.direction === 'bi' && link.link_type === 'p2p' ? { markerStart: { type: MarkerType.ArrowClosed }, markerEnd: { type: MarkerType.ArrowClosed } } : {}),
      style: { stroke: link.freshness === 'observed' ? '#477b8a' : '#aeb8c3', strokeDasharray: link.freshness === 'observed' ? undefined : '5 5' },
    }
  }), [graph, positions])
  useEffect(() => { const timer = setTimeout(() => void fitView({ padding: 0.3, maxZoom: 1.1 }), 80); return () => clearTimeout(timer) }, [fitView, nodes.length])
  return <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false} minZoom={0.15} maxZoom={2}
    onNodeClick={(_, node) => onNode(graph.nodes.find(n => n.id === node.id)!)}
    onEdgeClick={(_, edge) => onLink(graph.links.find(link => link.id === edge.id)!)} fitView fitViewOptions={{ padding: 0.3, maxZoom: 1.1 }}>
    <Background gap={24} color="#dce3e9" /><Controls showInteractive={false} />
  </ReactFlow>
}

export default function Page() {
  const [networks, setNetworks] = useState<Network[]>([])
  const [networkId, setNetworkId] = useState<string>()
  const [history, setHistory] = useState<History[]>([])
  const [runId, setRunId] = useState<string>()
  const [run, setRun] = useState<Run>()
  const [create, setCreate] = useState(false)
  const [offline, setOffline] = useState(false)
  const [scopes, setScopes] = useState(false)
  const { user } = useAuth()
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
  const [form] = Form.useForm()
  const chosenNetwork = Form.useWatch('network_id', form)
  // An existing network fixes the name and management domain; authorization is checked
  // against that domain, so the form must never submit a different one.
  const applyNetwork = (id?: string) => {
    const chosen = networks.find(n => n.id === id)
    if (chosen) form.setFieldsValue({ network_id: chosen.id, name: chosen.name, domain: chosen.domain })
  }
  const [retryForm] = Form.useForm()
  const refreshNetworks = () => api.get<Network[]>('/apps/topology/networks').then(({ data }) => { setNetworks(data); setNetworkId(current => current || data[0]?.id) })
  useEffect(() => { void refreshNetworks().catch(error => setError(errorMessage(error))) }, [])
  useEffect(() => {
    setRun(undefined); setSelected(undefined); setRunId(undefined)
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
          if (['queued', 'running', 'waiting_for_input'].includes(data.status)) timer = setTimeout(refresh, 3000)
          else void api.get<History[]>(`/apps/topology/networks/${data.network_id}/runs`).then(({ data }) => { if (alive) setHistory(data) })
        }
      } catch (error) { if (alive) setError(errorMessage(error)) }
    }
    void refresh()
    return () => { alive = false; clearTimeout(timer) }
  }, [runId])
  const active = run && ['queued', 'running', 'waiting_for_input'].includes(run.status)
  const waiting = run?.targets.filter(t => t.status === 'waiting_for_input') || []
  const accept = async (value: Run) => {
    await refreshNetworks(); setNetworkId(value.network_id)
    const { data } = await api.get<History[]>(`/apps/topology/networks/${value.network_id}/runs`)
    setHistory(data); setRunId(value.id); setRun(value)
  }
  const readEvidence = async (id?: string) => { if (!id) return; try { setEvidence((await api.get(`/apps/topology/evidence/${id}`)).data) } catch (error) { message.error(errorMessage(error)) } }
  const resolve = async (action: 'retry' | 'skip') => {
    if (!target || !run) return
    setBusy(true)
    try {
      const credentials = action === 'retry' && replaceCredentials ? await retryForm.validateFields() : undefined
      const { data } = await api.post<Run>(`/apps/topology/runs/${run.id}/targets/${target.id}/resolve`, { request_id: crypto.randomUUID(), version: target.version, action, credentials, profile_id: action === 'retry' ? retryProfile : undefined })
      setRun(data); setTarget(undefined); retryForm.resetFields()
    } catch (error) { message.error(errorMessage(error)) }
    finally { retryForm.setFieldValue('password', ''); setBusy(false) }
  }
  return <main className="topology-workspace">
    <header className="topology-heading"><div><Typography.Title level={2}>网络拓扑</Typography.Title><p>发现网络，保留观测，逐步构建数字孪生。</p></div><Space>{user?.role === 'admin' && <Button onClick={() => setScopes(true)}>访问范围</Button>}<Button onClick={() => setOffline(true)}>导入离线观测</Button><Button type="primary" onClick={() => { form.resetFields(); applyNetwork(networkId); setCreate(true) }}>新建发现任务</Button></Space></header>
    {error && <Alert type="error" message={error} action={<Button onClick={() => { setError(''); void refreshNetworks().catch(error => setError(errorMessage(error))) }}>刷新</Button>} />}
    <div className="topology-toolbar"><Select aria-label="网络" placeholder="选择网络" value={networkId} onChange={setNetworkId} options={networks.map(n => ({ value: n.id, label: `${n.name} · ${n.domain}` }))} style={{ minWidth: 220 }} />
      <Select aria-label="观测任务" placeholder="观测任务" value={runId} onChange={value => { setSelected(undefined); setRunId(value) }} options={history.map(h => ({ value: h.id, label: `${new Date(h.created_at).toLocaleString()} · ${labels[h.status] || h.status}` }))} style={{ minWidth: 290 }} />
      {run && <Tag color={active ? 'processing' : 'default'}>{labels[run.status]}</Tag>}
      {run?.snapshot_id && <Button onClick={() => void api.get(`/apps/topology/snapshots/${run.snapshot_id}/rfc8345`).then(({ data }) => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `topology-${run.snapshot_id}.json`; a.click(); URL.revokeObjectURL(url)
      }).catch(error => message.error(errorMessage(error)))}>导出 RFC 8345 + SIMAP</Button>}
      {active && <Button danger onClick={() => void api.post(`/apps/topology/runs/${run.id}/cancel`).then(({ data }) => setRun(data)).catch(error => message.error(errorMessage(error)))}>停止任务</Button>}
    </div>
    {waiting.length > 0 && <Alert type="warning" showIcon message={`${waiting.length} 个节点等待人工处理；成功节点的观测已保留`} />}
    <div className="topology-main"><section className="topology-canvas" aria-label="网络拓扑画布">
      {run?.graph.nodes.length ? <ReactFlowProvider><Canvas graph={run.graph} onNode={setSelected} onLink={setSelected} /></ReactFlowProvider> : <div className="topology-empty"><Empty description={run ? '尚无成功的拓扑观测，请查看节点状态' : '从一个管理地址开始发现，或导入已有 LLDP 输出'} /><p>每条链路都保留来源与观测时间。未确认的邻居以候选身份展示。</p></div>}
      {run && <div className="topology-map-caption">{run.graph.nodes.length} 个节点 · {run.graph.links.length} 条邻接记录 · L2/L3 拓扑（RFC 8345 + SIMAP）</div>}
    </section><aside className="topology-inspector"><h3>{selected ? '观测详情' : '阅读这张拓扑'}</h3>
      {selected ? <><dl>{Object.entries(selected).filter(([key]) => !['tps', 'id', 'source_tp', 'target_tp', 'source', 'target'].includes(key)).map(([key, value]) => <React.Fragment key={key}><dt>{({ name: '名称', address: '管理地址', addresses: '通告地址', identity_status: '身份状态', freshness: '新鲜度', observed_at: '观测时间', evidence_id: '证据编号', members: '端口数', subnets: '三层网段', link_type: '链路类型', evidence: '依据', direction: '方向', ends: '两端端口', label: '端口' } as Record<string, string>)[key] || key}</dt><dd>{labels[String(value)] || (typeof value === 'object' ? JSON.stringify(value) : String(value))}</dd></React.Fragment>)}</dl>
        {'tps' in selected && <><h4>终结点 / 端口</h4>{selected.tps.map(tp => <div key={tp.id}>{tp.name}</div>)}</>}
        <Space direction="vertical" style={{ marginTop: 16 }}><Button disabled={!selected.evidence_id} onClick={() => void readEvidence(selected.evidence_id)}>查看原始证据</Button>{'address' in selected && selected.address && <Button onClick={() => setTerminal(selected.address)}>打开 SSH 终端</Button>}</Space>
      </> : <><p>拓扑只展示本次任务的观测结果。链路表示二层邻接，不代表一根实际线缆；共享网段表示同一介质上的多个端口，例如经过 Hub。</p><p>只被邻居通告、尚未采集的设备用虚线框显示。设备按自身报告的机箱 ID 识别，采集后与通告合并为同一节点。</p><p>点击节点或链路可查看观测时间与命令证据。</p></>}
      {run?.snapshot_id && <><h4>快照比较</h4><Select aria-label="比较基线" placeholder="选择较早的快照" style={{ width: '100%' }} options={history.filter(h => h.snapshot_id && h.id !== run.id).map(h => ({ value: h.snapshot_id!, label: new Date(h.created_at).toLocaleString() }))} onChange={before => void api.get(`/apps/topology/snapshots/${run.snapshot_id}/diff/${before}`).then(({ data }) => setChanges(data)).catch(error => message.error(errorMessage(error)))} /></>}
    </aside></div>
    <section className="topology-targets"><div className="topology-section-title"><h3>发现节点</h3><span>{run?.targets.length || 0} 个目标 · 重试由人工确认</span></div>
      <Table<Target> size="small" rowKey="id" dataSource={run?.targets || []} pagination={{ pageSize: 8 }} scroll={{ x: 650 }} columns={[
        { title: '管理地址', dataIndex: 'address' }, { title: '发现深度', dataIndex: 'depth', width: 100 },
        { title: '设备平台', render: (_, row) => row.platform?.profile_id ? `${profiles.find(p => p.value === row.platform?.profile_id)?.label || row.platform.profile_id} · ${row.platform.source === 'manual' ? '人工指定' : '自动识别'}` : '待识别' },
        { title: '状态', render: (_, row) => <><Tag color={row.status === 'waiting_for_input' ? 'warning' : 'default'}>{labels[row.status] || row.status}</Tag><span>{failures[row.reason || ''] || row.reason}</span></> },
        { title: '操作', render: (_, row) => <Space>{row.status === 'waiting_for_input' && <Button size="small" onClick={() => { setTarget(row); setRetryProfile(row.platform?.source === 'manual' ? row.platform.profile_id || 'auto' : 'auto'); setReplaceCredentials(!row.reason?.startsWith('platform_')); retryForm.resetFields() }}>人工处理</Button>}<Button size="small" disabled={!row.evidence_id} onClick={() => void readEvidence(row.evidence_id)}>证据</Button><Button size="small" onClick={() => setTerminal(row.address)}>SSH</Button></Space> },
      ]} />
    </section>
    <Drawer title="新建拓扑发现" open={create} onClose={() => { form.resetFields(); setCreate(false) }} destroyOnClose width={520}>
      <Form form={form} layout="vertical" initialValues={{ name: '网络拓扑', domain: 'default', port: 22, profile_id: 'auto', max_depth: 3, max_targets: 30, credentials_for_neighbors: false }} onFinish={async values => {
        setBusy(true)
        try { const { data } = await api.post<Run>('/apps/topology/runs', { ...values, request_id: crypto.randomUUID(), seeds: values.seeds.split(/[\s,]+/).filter(Boolean), allowed_cidrs: values.allowed_cidrs.split(/[\s,]+/).filter(Boolean) }); await accept(data); setCreate(false); form.resetFields() }
        catch (error) { message.error(errorMessage(error)) }
        finally { form.setFieldValue(['credentials', 'password'], ''); setBusy(false) }
      }}>
        <Form.Item name="network_id" label="网络范围"><Select allowClear placeholder="创建新网络" onChange={value => applyNetwork(value)} options={networks.map(n => ({ value: n.id, label: `${n.name} · ${n.domain}` }))} /></Form.Item>
        <Form.Item name="name" label="网络名称" rules={[{ required: true }]}><Input disabled={!!chosenNetwork} /></Form.Item><Form.Item name="domain" label="管理域" rules={[{ required: true }]} extra={chosenNetwork ? '沿用所选网络的管理域；设备授权按此管理域校验' : '设备授权按此管理域校验，须与本应用“访问范围”中的管理域一致'}><Input disabled={!!chosenNetwork} /></Form.Item>
        <Form.Item name="seeds" label="种子设备地址" rules={[{ required: true }]} extra="多个 IP 以空格或换行分隔"><Input.TextArea rows={2} placeholder="10.10.1.1" /></Form.Item>
        <Form.Item name="allowed_cidrs" label="本任务允许的管理网段" rules={[{ required: true }]} extra="发现范围同时受管理员授予的设备权限约束"><Input placeholder="10.10.1.0/24" /></Form.Item>
        <Form.Item name="profile_id" label="种子设备平台（可选）" extra="默认自动识别。手工选择只适用于种子；每个新邻居都独立识别，不继承种子平台。"><Select options={profiles} /></Form.Item>
        <Space><Form.Item name="port" label="SSH 端口"><InputNumber min={1} max={65535} /></Form.Item><Form.Item name="max_depth" label="最大深度"><InputNumber min={0} max={16} /></Form.Item><Form.Item name="max_targets" label="目标上限"><InputNumber min={1} max={200} /></Form.Item></Space>
        <Form.Item name={['credentials', 'username']} label="用户名" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item><Form.Item name={['credentials', 'password']} label="密码" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
        <Form.Item name="credentials_for_neighbors" valuePropName="checked"><Checkbox>允许本次凭据用于上述网段内发现的新邻居</Checkbox></Form.Item>
        <p>认证或连接失败后等待人工处理。凭据有效期 1 小时；不会从资产、巡检或历史密码中自动取用。</p><Button type="primary" htmlType="submit" loading={busy}>开始发现</Button>
      </Form>
    </Drawer>
    <Modal open={!!target} title={`处理节点 · ${target?.address || ''}`} onCancel={() => { retryForm.resetFields(); setTarget(undefined) }} footer={<Space><Button onClick={() => void resolve('skip')} disabled={busy}>跳过此节点</Button><Button type="primary" loading={busy} onClick={() => void resolve('retry')}>确认并重试一次</Button></Space>} destroyOnClose>
      <Alert type="warning" message={failures[target?.reason || ''] || target?.reason} style={{ marginBottom: 16 }} /><div style={{ marginBottom: 16 }}><Typography.Text>仅此节点的平台</Typography.Text><Select aria-label="仅此节点的平台" value={retryProfile} onChange={setRetryProfile} options={profiles} style={{ width: '100%', marginTop: 8 }} /><p>无法识别或尚未支持的设备可保留候选节点并跳过；此选择不会传给邻居。</p></div><Checkbox checked={replaceCredentials} onChange={event => setReplaceCredentials(event.target.checked)}>提供新的用户名和密码</Checkbox>
      {replaceCredentials && <Form form={retryForm} layout="vertical" style={{ marginTop: 16 }}><Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item><Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item></Form>}
    </Modal>
    <Drawer title="访问范围" open={scopes} onClose={() => setScopes(false)} width={900} destroyOnClose><AccessScopes /></Drawer>
    <Drawer title="导入离线 LLDP 观测" open={offline} onClose={() => setOffline(false)} width={660}><p>JSON 包含 profile_id、domain 和 captures。每项需提供 address、含时区的 observed_at、outputs.neighbors 与 complete。离线数据不会触发设备登录。</p><Input.TextArea aria-label="离线观测 JSON" rows={18} value={importText} onChange={event => setImportText(event.target.value)} placeholder={'{"profile_id":"comware-lldp-v1","domain":"default","captures":[{"address":"10.10.1.1","observed_at":"2026-09-28T08:00:00+08:00","outputs":{"neighbors":"完整 LLDP 命令输出"},"complete":true}]}'} /><Button type="primary" style={{ marginTop: 16 }} loading={busy} onClick={async () => { setBusy(true); try { const { data } = await api.post('/apps/topology/imports', { ...JSON.parse(importText), request_id: crypto.randomUUID() }); await accept(data); setOffline(false); setImportText('') } catch (error) { message.error(errorMessage(error)) } finally { setBusy(false) } }}>校验并导入</Button></Drawer>
    <Drawer title="原始观测证据" open={evidence !== undefined} onClose={() => setEvidence(undefined)} width={760}><pre className="topology-raw">{JSON.stringify(evidence, null, 2)}</pre></Drawer>
    <Drawer title="快照差异" open={changes !== undefined} onClose={() => setChanges(undefined)} width={600}><p>差异包含对象、新鲜度和观测时间变化；未观测不等于已经删除。</p><pre className="topology-raw">{JSON.stringify(changes, null, 2)}</pre></Drawer>
    <DeviceTerminal open={!!terminal} onClose={() => setTerminal(undefined)} address={terminal} domain={run?.graph.domain} />
  </main>
}
