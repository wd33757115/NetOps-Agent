import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Checkbox, Divider, Drawer, Form, Input, InputNumber, Radio, Select, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import {
  CIRCUIT_STATES, NETWORK_KINDS, RISK_KINDS, ROUTING, SITE_KINDS, errorText, ledger,
  type CircuitDetail, type Overview, type ProviderRow, type RiskRow, type Site,
} from '../api'
import { FormModal, useRefresh, useSubmit } from './Forms'

type Done = () => void
type Saved = { id: string; name?: string }
/** What a create-in-place hands back: the new record, or nothing when the person cancelled. */
type Created = { id: string; label?: string } | null
const Row = ({ columns = '1fr 1fr', children }: { columns?: string; children: React.ReactNode }) =>
  <div className="rl-form-row" style={{ gridTemplateColumns: columns }}>{children}</div>

/** A select whose missing entries are created in place from what was typed (directly, or through a small form). */
export function CreatableSelect({ value, onChange, options, onCreate, placeholder, disabled }: {
  value?: string; onChange?: (value?: string) => void; options: { value: string; label: string }[]
  onCreate?: (name: string) => Promise<Created>; placeholder?: string; disabled?: boolean
}) {
  const [typed, setTyped] = useState('')
  const [made, setMade] = useState<{ value: string; label: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [opened, setOpened] = useState(false)
  const all = [...options, ...made.filter(m => !options.some(o => o.value === m.value))]
  const name = typed.trim()
  const creatable = Boolean(onCreate && name && !all.some(o => o.label === name))
  const create = async () => {
    // Close first: a form opened for the new entry must not sit under this dropdown.
    setOpened(false)
    setBusy(true)
    try {
      const created = await onCreate!(name)
      if (created) {
        setMade(previous => [...previous, { value: created.id, label: created.label || name }])
        onChange?.(created.id)
        setTyped('')
      }
    } catch (error) { message.error(errorText(error)) } finally { setBusy(false) }
  }
  return <Select showSearch allowClear optionFilterProp="label" value={value} onChange={onChange} options={all} disabled={disabled}
    placeholder={placeholder} searchValue={typed} onSearch={setTyped} open={opened} onDropdownVisibleChange={setOpened}
    // Never empty: antd closes a dropdown with nothing to show, which would hide the create button.
    notFoundContent={<span className="rl-muted">{creatable ? `还没有“${name}”` : '没有匹配项'}</span>}
    dropdownRender={menu => <>{menu}{creatable && <><Divider style={{ margin: '4px 0' }} />
      <Button type="text" block icon={<PlusOutlined aria-hidden="true" />} loading={busy} style={{ textAlign: 'left' }}
        onMouseDown={event => event.preventDefault()} onClick={create}>新建“{name}”</Button></>}</>} />
}

// --------------------------------------------------------------------------- simple records

export function ProviderModal({ open, provider, onClose, onSaved }: { open: boolean; provider?: ProviderRow | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<Partial<ProviderRow>>(values => ledger.save('providers', values, provider?.id), provider ? '运营商已保存' : '运营商已新增', onSaved)
  return <FormModal<Partial<ProviderRow>> open={open} title={provider ? `编辑运营商 ${provider.name}` : '新增运营商'} okText="保存" busy={busy}
    onClose={onClose} onSubmit={submit} initial={provider || {}}>
    <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="中国电信" /></Form.Item>
    <Row>
      <Form.Item name="support_phone" label="报障电话"><Input maxLength={80} placeholder="10000 转 5" /></Form.Item>
      <Form.Item name="account" label="客户编号"><Input maxLength={80} /></Form.Item>
    </Row>
    <Form.Item name="contact" label="客户经理 / 联系人"><Input maxLength={120} /></Form.Item>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

type NetworkValues = { provider_id: string; name: string; kind: string; service_id?: string; interconnects: boolean; note?: string }

/** A provider network: the carrier's MPLS VPN, internet or similar that circuits end on, with no site of ours at the far end. */
export function NetworkModal({ open, providers, providerId, network, initialName, initialKind, onClose, onSaved }: {
  open: boolean; providers: { id: string; name: string }[]; providerId?: string; network?: ProviderRow['networks'][number] | null
  initialName?: string; initialKind?: string; onClose: Done; onSaved: (saved: Saved) => void
}) {
  const { busy, submit } = useSubmit<NetworkValues, Saved>(values => ledger.save('provider-networks', values, network?.id),
    network ? '运营商网络已保存' : '运营商网络已新增', onSaved)
  const initial = useMemo(() => {
    if (network) return { ...network, service_id: network.service_id || undefined, note: network.note || undefined }
    const kind = initialKind || 'MPLS VPN'
    return { provider_id: providerId, name: initialName, kind, interconnects: kind === 'MPLS VPN' }
  }, [network, providerId, initialName, initialKind])
  return <FormModal<NetworkValues> open={open} title={network ? `编辑运营商网络 ${network.name}` : '新增运营商网络'} okText="保存" busy={busy}
    onClose={onClose} onSubmit={submit} initial={initial as Partial<NetworkValues>}
    subtitle="运营商的 MPLS VPN、互联网等网络。专线的另一端不是我方站点时接在这里；接在同一个网络上的站点可以经它互通。">
    <Row columns="1.4fr 1fr">
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={80} placeholder="电信 CN2 华北" /></Form.Item>
      <Form.Item name="kind" label="类型"><Select options={NETWORK_KINDS.map(value => ({ value }))} /></Form.Item>
    </Row>
    <Form.Item name="provider_id" label="所属运营商" rules={[{ required: true, message: '请选择运营商' }]}>
      <Select showSearch optionFilterProp="label" options={providers.map(p => ({ value: p.id, label: p.name }))} />
    </Form.Item>
    <Form.Item name="service_id" label="服务标识" extra="运营商给的 VPN 名称或实例号，报障时会用到。"><Input maxLength={80} /></Form.Item>
    <Form.Item name="interconnects" valuePropName="checked" extra="MPLS VPN 一般勾选；互联网只有部署了 SD-WAN 或 IPsec 才勾选。">
      <Checkbox>站点之间能经这个网络互访</Checkbox>
    </Form.Item>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

type NamedValues = { name: string; note?: string; kind?: string }

type PopValues = NamedValues & { provider_id?: string }

export function PopModal({ open, providers, providerId, pop, onClose, onSaved }: {
  open: boolean; providers: { id: string; name: string }[]; providerId?: string; pop?: { id: string; name: string; note: string | null } | null
  onClose: Done; onSaved: Done
}) {
  const { busy, submit } = useSubmit<PopValues>(values => ledger.save('pops', values, pop?.id), pop ? '局端已保存' : '局端已新增', onSaved)
  return <FormModal<PopValues> open={open} title={pop ? `编辑局端 ${pop.name}` : '新增局端'} okText="保存" busy={busy} onClose={onClose}
    onSubmit={submit} initial={pop ? { name: pop.name, note: pop.note || undefined, provider_id: providerId } : { provider_id: providerId }}
    subtitle="运营商的接入局点或 PE 设备；同一局端的专线会一起受局端故障影响。">
    <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={120} placeholder="西安高新局 PE-XA-03" /></Form.Item>
    <Form.Item name="provider_id" label="所属运营商" rules={[{ required: true, message: '请选择运营商' }]}>
      <Select showSearch optionFilterProp="label" options={providers.map(p => ({ value: p.id, label: p.name }))} />
    </Form.Item>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

export function TypeModal({ open, item, onClose, onSaved }: { open: boolean; item?: { id: string; name: string; note: string | null } | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<NamedValues>(values => ledger.save('circuit-types', values, item?.id), item ? '专线类型已保存' : '专线类型已新增', onSaved)
  return <FormModal<NamedValues> open={open} title={item ? `编辑专线类型 ${item.name}` : '新增专线类型'} okText="保存" busy={busy} onClose={onClose}
    onSubmit={submit} initial={item ? { name: item.name, note: item.note || undefined } : {}}>
    <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={40} placeholder="卫星链路" /></Form.Item>
    <Form.Item name="note" label="说明"><Input maxLength={200} /></Form.Item>
  </FormModal>
}

export function RiskModal({ open, risk, onClose, onSaved }: { open: boolean; risk?: RiskRow | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<NamedValues>(values => ledger.save('risks', values, risk?.id), risk ? '共享风险已保存' : '共享风险已新增', onSaved)
  return <FormModal<NamedValues> open={open} title={risk ? `编辑共享风险 ${risk.name}` : '新增共享风险'} okText="保存" busy={busy} onClose={onClose}
    onSubmit={submit} initial={risk ? { name: risk.name, kind: risk.kind, note: risk.note || undefined } : { kind: '光缆' }}
    subtitle="几条专线共用的物理资源，例如同一条入户光缆、同一段管道。主备线标了同一个风险，就会判为共路由。">
    <Row columns="1.6fr 1fr">
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={80} placeholder="西安机房入户光缆 A" /></Form.Item>
      <Form.Item name="kind" label="类型"><Select options={RISK_KINDS.map(value => ({ value }))} /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

/** A site: where circuits end. Core sites are what the other sites' paths are traced to. */
export function SiteModal({ open, site, overview, initialName, onClose, onSaved }: {
  open: boolean; site?: Site | null; overview?: Overview; initialName?: string; onClose: Done; onSaved: (saved: Saved) => void
}) {
  const refresh = useRefresh()
  const { busy, submit } = useSubmit<Site, Saved>(values => ledger.save('sites', values, site?.id), site ? '站点已保存' : '站点已新增', onSaved)
  const initial = useMemo(() => site ? { ...site, address: site.address || undefined, note: site.note || undefined }
    : { name: initialName, kind: '自建', core: false }, [site, initialName])
  const createRegion = async (name: string) => { const created = await ledger.save('regions', { name }); refresh(); return created }
  return <FormModal<Site> open={open} title={site ? `编辑站点 ${site.name}` : '新增站点'} okText="保存" busy={busy} onClose={onClose}
    onSubmit={submit} initial={initial as Partial<Site>} width={600}>
    <Row columns="1.4fr 1fr 1fr">
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="西安机房" /></Form.Item>
      <Form.Item name="region_id" label="地域" rules={[{ required: true, message: '请选择地域' }]}>
        <CreatableSelect options={(overview?.regions || []).map(r => ({ value: r.id, label: r.name }))} onCreate={createRegion} placeholder="选择或输入新建" />
      </Form.Item>
      <Form.Item name="kind" label="类型"><Select options={SITE_KINDS.map(value => ({ value }))} /></Form.Item>
    </Row>
    <Form.Item name="core" valuePropName="checked" extra="数据中心等汇聚站点。“站点韧性”列出其他站点到核心站点的路径和单点。">
      <Checkbox>这是核心站点</Checkbox>
    </Form.Item>
    <Form.Item name="address" label="地址"><Input maxLength={200} /></Form.Item>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

// --------------------------------------------------------------------------- circuits

type EndValues = { site_id?: string; network_id?: string; device?: string; interface?: string; port_speed?: number; xconnect?: string; pop_id?: string; ip?: string }
type CircuitValues = {
  provider_id: string; cid: string; type_id: string; status: string; bandwidth?: number; install_date?: string; description?: string
  a: EndValues; z: EndValues; z_kind: 'site' | 'network' | 'none'
  block: 'none' | 'existing' | 'carve'; interconnect?: string; carve_from?: string; carve_length: number
  vlan?: number; routing?: string; local_as?: string; remote_as?: string; risks: string[]; note?: string
}

const endInitial = (end: CircuitDetail['a']): EndValues => end ? {
  site_id: end.site_id || undefined, network_id: end.network_id || undefined, device: end.device || undefined, interface: end.interface || undefined,
  port_speed: end.port_speed || undefined, xconnect: end.xconnect || undefined, pop_id: end.pop_id || undefined, ip: end.ip || undefined,
} : {}

/** Create or edit a circuit: its two ends, the interconnect block and addresses, and its shared risks. */
export function CircuitDrawer({ open, circuit, onClose, onSaved }: { open: boolean; circuit?: CircuitDetail | null; onClose: Done; onSaved: (id: string) => void }) {
  const refresh = useRefresh()
  const options = useQuery('rl-circuit-options', ledger.circuitOptions, { enabled: open })
  const overview = useQuery('rl-overview', ledger.overview, { enabled: open })
  const [form] = Form.useForm<CircuitValues>()
  const [busy, setBusy] = useState(false)
  // A site or network typed in a select opens its form; the select gets the result when that form closes.
  const [making, setMaking] = useState<{ kind: 'site' | 'network'; name: string; resolve: (created: Created) => void } | null>(null)
  const make = (kind: 'site' | 'network') => (name: string) => new Promise<Created>(resolve => setMaking({ kind, name, resolve }))
  const finish = (saved: Saved | null) => {
    making?.resolve(saved ? { id: saved.id, label: saved.name } : null)
    setMaking(null)
    if (saved) refresh()
  }
  const initial = useMemo<Partial<CircuitValues>>(() => circuit ? {
    provider_id: circuit.provider?.id, cid: circuit.cid, type_id: circuit.type?.id, status: circuit.status, bandwidth: circuit.bandwidth || undefined,
    install_date: circuit.install_date || undefined, description: circuit.description || undefined,
    a: endInitial(circuit.a), z: endInitial(circuit.z),
    z_kind: circuit.z?.site_id ? 'site' : circuit.z?.network_id ? 'network' : 'none',
    block: circuit.interconnect ? 'existing' : 'none', interconnect: circuit.interconnect?.network, carve_length: 30,
    vlan: circuit.vlan || undefined, routing: circuit.routing || undefined, local_as: circuit.local_as || undefined, remote_as: circuit.remote_as || undefined,
    risks: circuit.risks.map(r => r.name), note: circuit.note || undefined,
  } : { status: '在用', z_kind: 'network', block: 'none', carve_length: 30, risks: [] }, [circuit])
  const latest = useRef(initial)
  latest.current = initial
  useEffect(() => { if (open) { form.resetFields(); form.setFieldsValue(latest.current as never) } }, [open, form])
  const data = options.data
  const provider = Form.useWatch('provider_id', form)
  const typeId = Form.useWatch('type_id', form)
  const zKind = Form.useWatch('z_kind', form)
  const block = Form.useWatch('block', form)
  const pops = (data?.pops || []).filter(p => p.provider_id === provider).map(p => ({ value: p.id, label: p.name }))
  const sites = (data?.sites || []).map(s => ({ value: s.id, label: `${s.name}${s.region ? `（${s.region}）` : ''}` }))
  const networks = (data?.networks || []).map(n => ({ value: n.id, label: `${n.name}（${data?.providers.find(p => p.id === n.provider_id)?.name || ''}）` }))
  // A new network's kind follows the circuit's type: an internet line ends on the internet, an MPLS line on an MPLS VPN.
  const typeName = data?.types.find(t => t.id === typeId)?.name || ''
  const networkKind = /互联网/.test(typeName) ? '互联网' : /MPLS/i.test(typeName) ? 'MPLS VPN' : typeName ? '其他' : 'MPLS VPN'
  const createPop = async (name: string) => {
    const created = await ledger.save('pops', { provider_id: provider, name })
    refresh()
    return created
  }
  const createProvider = async (name: string) => { const created = await ledger.save('providers', { name }); refresh(); return created }
  const save = async (values: CircuitValues) => {
    setBusy(true)
    try {
      const end = (v: EndValues) => ({ ...v, port_speed: v.port_speed || undefined })
      const body = {
        provider_id: values.provider_id, cid: values.cid, type_id: values.type_id, status: values.status, bandwidth: values.bandwidth,
        install_date: values.install_date, description: values.description, a: end(values.a || {}),
        z: values.z_kind === 'none' ? null : end({ ...values.z, site_id: values.z_kind === 'site' ? values.z?.site_id : undefined,
          network_id: values.z_kind === 'network' ? values.z?.network_id : undefined }),
        interconnect: values.block === 'existing' ? values.interconnect : undefined,
        carve_from: values.block === 'carve' ? values.carve_from : undefined, carve_length: values.carve_length || 30,
        vlan: values.vlan, routing: values.routing, local_as: values.local_as, remote_as: values.remote_as, risks: values.risks || [], note: values.note,
      }
      const saved = await ledger.saveCircuit(body, circuit?.id)
      message.success(circuit ? '专线已保存' : '专线已新增')
      onSaved(saved.id)
    } catch (error) { message.error(errorText(error)) } finally { setBusy(false) }
  }
  const endFields = (side: 'a' | 'z') => <>
    <Row columns="1fr 1fr 110px">
      <Form.Item name={[side, 'device']} label="设备"><Input maxLength={80} placeholder="XA-WAN-R1" /></Form.Item>
      <Form.Item name={[side, 'interface']} label="接口"><Input maxLength={80} placeholder="GE0/0/3" /></Form.Item>
      <Form.Item name={[side, 'port_speed']} label="端口速率"><InputNumber min={1} addonAfter="M" style={{ width: '100%' }} /></Form.Item>
    </Row>
    <Row columns="1fr 1fr 1fr">
      <Form.Item name={[side, 'xconnect']} label="配线 / 交接"><Input maxLength={120} placeholder="ODF-02 第 14 口" /></Form.Item>
      <Form.Item name={[side, 'pop_id']} label="接入局端">
        <CreatableSelect options={pops} onCreate={provider ? createPop : undefined} disabled={!provider}
          placeholder={provider ? '选择或输入新建' : '先选运营商'} />
      </Form.Item>
      <Form.Item name={[side, 'ip']} label="地址" extra={side === 'a' ? '在互联网段里分配' : undefined}><Input maxLength={15} placeholder="10.255.0.5" /></Form.Item>
    </Row>
  </>
  return <Drawer open={open} onClose={onClose} width={860} destroyOnClose title={circuit ? `编辑专线 ${circuit.cid}` : '新增专线'}
    extra={<Button type="primary" loading={busy} onClick={() => form.submit()}>保存</Button>}>
    <Form<CircuitValues> form={form} layout="vertical" onFinish={save}>
      <h3 className="rl-form-section">基本信息</h3>
      <Row columns="1.2fr 1.2fr 1fr">
        <Form.Item name="provider_id" label="运营商" rules={[{ required: true, message: '请选择运营商' }]}>
          <CreatableSelect options={(data?.providers || []).map(p => ({ value: p.id, label: p.name }))} onCreate={createProvider} placeholder="选择或输入新建" />
        </Form.Item>
        <Form.Item name="cid" label="电路编号" rules={[{ required: true, message: '请填写电路编号' }]}><Input maxLength={80} placeholder="CN2-0098812" /></Form.Item>
        <Form.Item name="type_id" label="类型" rules={[{ required: true, message: '请选择类型' }]}>
          <Select showSearch optionFilterProp="label" options={(data?.types || []).map(t => ({ value: t.id, label: t.name }))} />
        </Form.Item>
      </Row>
      <Row columns="1fr 1fr 1fr 1.4fr">
        <Form.Item name="status" label="状态"><Select options={CIRCUIT_STATES.map(value => ({ value }))} /></Form.Item>
        <Form.Item name="bandwidth" label="承诺带宽"><InputNumber min={0} addonAfter="Mbps" style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="install_date" label="开通日期"><Input type="date" /></Form.Item>
        <Form.Item name="description" label="说明"><Input maxLength={200} placeholder="例如 西安—北京数据中心互联" /></Form.Item>
      </Row>
      <h3 className="rl-form-section">A 端（本方站点）</h3>
      <Form.Item name={['a', 'site_id']} label="站点" rules={[{ required: true, message: '请选择站点' }]}>
        <CreatableSelect options={sites} onCreate={make('site')} placeholder="选择或输入名称新建" />
      </Form.Item>
      {endFields('a')}
      <h3 className="rl-form-section">Z 端</h3>
      <Form.Item name="z_kind"><Radio.Group optionType="button" options={[
        { value: 'site', label: '本方站点' }, { value: 'network', label: '运营商网络' }, { value: 'none', label: '只有一端' }]} /></Form.Item>
      {zKind === 'site' && <Form.Item name={['z', 'site_id']} label="站点" rules={[{ required: true, message: '请选择站点' }]}>
        <CreatableSelect options={sites} onCreate={make('site')} placeholder="选择或输入名称新建" /></Form.Item>}
      {zKind === 'network' && <Form.Item name={['z', 'network_id']} label="运营商网络" rules={[{ required: true, message: '请选择运营商网络' }]}
        extra="运营商的 MPLS VPN、互联网等网络，接在同一网络上的站点可以经它互通。没有的直接输入名称新建，也可以在“运营商与分类 → 运营商网络”里维护。">
        <CreatableSelect options={networks} onCreate={make('network')} placeholder="选择或输入名称新建" /></Form.Item>}
      {zKind !== 'none' && endFields('z')}
      <h3 className="rl-form-section">互联</h3>
      <Form.Item name="block" label="互联网段"><Radio.Group optionType="button" options={[
        { value: 'none', label: '不登记' }, { value: 'existing', label: '用已有网段' }, { value: 'carve', label: '从上级网段自动划出' }]} /></Form.Item>
      {block === 'existing' && <Form.Item name="interconnect" label="网段" rules={[{ required: true, message: '请填写网段' }]}
        extra="网段规划里已有的最末一级网段；两端地址在其中分配，专线拆除后转为待回收。"><Input maxLength={18} placeholder="10.255.0.4/30" /></Form.Item>}
      {block === 'carve' && <Row columns="1.4fr 1fr">
        <Form.Item name="carve_from" label="上级网段" rules={[{ required: true, message: '请填写上级网段' }]} extra="例如网段规划里的“互联地址”资源。两端地址可以留空，保存时 A 端、Z 端依次取前两个可用地址。">
          <Input maxLength={18} placeholder="10.255.0.0/16" /></Form.Item>
        <Form.Item name="carve_length" label="大小"><Select options={[{ value: 30, label: '/30（2 个可用地址）' }, { value: 31, label: '/31（点到点）' }]} /></Form.Item>
      </Row>}
      <Row columns="1fr 1fr 1fr 1fr">
        <Form.Item name="vlan" label="VLAN"><InputNumber min={1} max={4094} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="routing" label="路由方式"><Select allowClear options={ROUTING.map(value => ({ value }))} /></Form.Item>
        <Form.Item name="local_as" label="本端 AS"><Input maxLength={20} /></Form.Item>
        <Form.Item name="remote_as" label="对端 AS"><Input maxLength={20} /></Form.Item>
      </Row>
      <h3 className="rl-form-section">共享风险</h3>
      <Form.Item name="risks" extra="这条线经过的光缆、管道、入户等；没有的名称直接输入，会新建。运营商、局端和设备会自动比对，不用在这里填。">
        <Select mode="tags" options={(data?.risks || []).map(r => ({ value: r.name, label: `${r.name}（${r.kind}）` }))} placeholder="例如 西安机房入户光缆 A" />
      </Form.Item>
      <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
    </Form>
    <SiteModal open={making?.kind === 'site'} initialName={making?.name} overview={overview.data} onClose={() => finish(null)} onSaved={finish} />
    <NetworkModal open={making?.kind === 'network'} initialName={making?.name} initialKind={networkKind} providerId={provider}
      providers={data?.providers || []} onClose={() => finish(null)} onSaved={finish} />
  </Drawer>
}
