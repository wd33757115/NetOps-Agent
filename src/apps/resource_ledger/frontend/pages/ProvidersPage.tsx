import React, { useMemo, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space as Gap, Table, Tabs, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useSearchParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { NetworkModal, PopModal, ProviderModal, RiskModal, TypeModal } from '../components/CircuitForms'
import { pageLocally } from '../components/Paging'
import { errorText, ledger, type ProviderRow, type RiskRow } from '../api'

type Network = ProviderRow['networks'][number] & { provider: string }
type Pop = ProviderRow['pops'][number] & { provider_id: string; provider: string }
type CircuitType = { id: string; name: string; note: string | null; used: number }

const muted = (text = '—') => <span className="rl-muted">{text}</span>

export default function ProvidersPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'providers'
  const providerFilter = params.get('provider') || undefined
  const refresh = useRefresh()
  const providers = useQuery('rl-providers', ledger.providers)
  const types = useQuery('rl-circuit-types', ledger.circuitTypes)
  const risks = useQuery('rl-risks', ledger.risks)
  const canEdit = Boolean(providers.data?.can_edit)
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState<{ open: boolean; row?: ProviderRow | null }>({ open: false })
  const [network, setNetwork] = useState<{ open: boolean; providerId?: string; row?: Network | null }>({ open: false })
  const [pop, setPop] = useState<{ open: boolean; providerId?: string; row?: Pop | null }>({ open: false })
  const [kind, setKind] = useState<{ open: boolean; row?: CircuitType | null }>({ open: false })
  const [risk, setRisk] = useState<{ open: boolean; row?: RiskRow | null }>({ open: false })
  const done = (close: () => void) => () => { close(); refresh() }
  const go = (key: string, provider?: string) => { setQuery(''); setParams(provider ? { tab: key, provider } : { tab: key }) }

  const list = providers.data?.providers || []
  const choices = list.map(p => ({ value: p.id, label: p.name }))
  const text = query.trim().toLowerCase()
  const has = (...values: (string | null | undefined)[]) => !text || values.some(v => v?.toLowerCase().includes(text))
  // Networks and points of presence belong to a provider; their tabs list them flat, with the provider as a column.
  const networks = useMemo<Network[]>(() => list.flatMap(p => p.networks.map(n => ({ ...n, provider: p.name }))), [list])
  const pops = useMemo<Pop[]>(() => list.flatMap(p => p.pops.map(o => ({ ...o, provider_id: p.id, provider: p.name }))), [list])

  const remove = (kindName: string, path: Parameters<typeof ledger.remove>[0], id: string, name: string, content?: string) => Modal.confirm({
    title: `删除${kindName} ${name}？`, content, okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => {
      try {
        const result = await ledger.remove(path, id)
        message.success(result.unlinked ? `${kindName}已删除，已从 ${result.unlinked} 条专线上去掉` : `${kindName}已删除`)
        refresh()
      } catch (error) { message.error(errorText(error)); throw error }
    },
  })
  const ops = (onEdit: () => void, onDelete: () => void, label: string) => canEdit ? <Gap size={0}>
    <Button type="text" size="small" aria-label={`编辑 ${label}`} icon={<EditOutlined aria-hidden="true" />} onClick={onEdit} />
    <Button type="text" size="small" danger aria-label={`删除 ${label}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={onDelete} />
  </Gap> : null
  const opsColumn = <T,>(render: (row: T) => React.ReactNode) => canEdit ? [{ title: '', key: 'ops', width: 76, render: (_: unknown, row: T) => render(row) }] : []
  const tools = (placeholder: string, add?: React.ReactNode, byProvider = false) => <div className="rl-tools">
    {canEdit && add}
    <Input.Search allowClear placeholder={placeholder} style={{ maxWidth: 300 }} value={query} onChange={event => setQuery(event.target.value)} />
    {byProvider && <Select allowClear showSearch optionFilterProp="label" placeholder="运营商" style={{ width: 160 }} value={providerFilter}
      onChange={value => setParams(value ? { tab, provider: value } : { tab })} options={choices} />}
  </div>
  const used = (count: number, to: string) => count ? <Link to={to}>{count} 条</Link> : muted('0 条')

  const providerTab = <>
    {tools('搜索运营商、报障电话或客户编号', <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setProvider({ open: true })}>新增运营商</Button>)}
    <Table<ProviderRow> size="middle" rowKey="id" loading={providers.isLoading} scroll={{ x: 960 }} pagination={pageLocally()}
      dataSource={list.filter(p => has(p.name, p.support_phone, p.account, p.contact, p.note))}
      locale={{ emptyText: <div className="rl-empty">{text ? '没有符合条件的运营商' : '还没有运营商。'}</div> }}
      columns={[
        { title: '运营商', key: 'name', width: 180, render: (_, row) => <div className="rl-owner"><strong>{row.name}</strong>{row.note && <small>{row.note}</small>}</div> },
        { title: '报障电话', dataIndex: 'support_phone', width: 150, render: value => value || muted('未登记') },
        { title: '客户编号', dataIndex: 'account', width: 160, render: value => value || muted() },
        { title: '客户经理', dataIndex: 'contact', render: value => value || muted() },
        { title: '运营商网络', key: 'networks', width: 110, render: (_, row) => row.networks.length
          ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => go('networks', row.id)}>{row.networks.length} 个</Button> : muted('0 个') },
        { title: '局端', key: 'pops', width: 90, render: (_, row) => row.pops.length
          ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => go('pops', row.id)}>{row.pops.length} 个</Button> : muted('0 个') },
        { title: '专线', key: 'circuits', width: 80, render: (_, row) => used(row.circuits, `${ROOT}/circuits?provider=${row.id}`) },
        ...opsColumn<ProviderRow>(row => ops(() => setProvider({ open: true, row }),
          () => remove('运营商', 'providers', row.id, row.name, '它的运营商网络和局端会一起删除；还有专线时不能删除。'), row.name)),
      ]} />
  </>

  const networkTab = <>
    {tools('搜索名称或服务标识', <Button type="primary" icon={<PlusOutlined aria-hidden="true" />}
      onClick={() => setNetwork({ open: true, providerId: providerFilter })}>新增运营商网络</Button>, true)}
    <Table<Network> size="middle" rowKey="id" loading={providers.isLoading} scroll={{ x: 900 }} pagination={pageLocally()}
      dataSource={networks.filter(n => (!providerFilter || n.provider_id === providerFilter) && has(n.name, n.service_id, n.note, n.provider))}
      locale={{ emptyText: <div className="rl-empty">{text || providerFilter ? '没有符合条件的运营商网络'
        : '还没有运营商网络。MPLS VPN、互联网等专线的另一端接在运营商网络上；也可以在新增专线时直接输入名称新建。'}</div> }}
      columns={[
        { title: '名称', key: 'name', width: 200, render: (_, row) => <div className="rl-owner"><strong>{row.name}</strong>{row.note && <small>{row.note}</small>}</div> },
        { title: '运营商', dataIndex: 'provider', width: 130 },
        { title: '类型', dataIndex: 'kind', width: 100 },
        { title: '服务标识', dataIndex: 'service_id', render: value => value || muted() },
        { title: '站点间互通', dataIndex: 'interconnects', width: 100, render: value => value ? '是' : muted('否') },
        { title: '专线', dataIndex: 'used', width: 80, render: (value, row) => used(value, `${ROOT}/circuits?q=${encodeURIComponent(row.name)}`) },
        ...opsColumn<Network>(row => ops(() => setNetwork({ open: true, row }), () => remove('运营商网络', 'provider-networks', row.id, row.name,
          '还有专线接在这个网络上时不能删除。'), row.name)),
      ]} />
    <p className="rl-note">运营商网络是运营商的 MPLS VPN、互联网等网络：专线的另一端不是我方站点时就接在这里。
      “站点间互通”表示接在同一网络上的站点能经它互访（MPLS VPN 一般是；互联网要部署了 SD-WAN 或 IPsec 才是），站点韧性据此连出路径。</p>
  </>

  const popTab = <>
    {tools('搜索局端名称', <Button type="primary" icon={<PlusOutlined aria-hidden="true" />}
      onClick={() => setPop({ open: true, providerId: providerFilter })}>新增局端</Button>, true)}
    <Table<Pop> size="middle" rowKey="id" loading={providers.isLoading} pagination={pageLocally()}
      dataSource={pops.filter(o => (!providerFilter || o.provider_id === providerFilter) && has(o.name, o.note, o.provider))}
      locale={{ emptyText: <div className="rl-empty">{text || providerFilter ? '没有符合条件的局端' : '还没有局端。编辑专线时直接输入局端名称就会新建。'}</div> }}
      columns={[
        { title: '名称', key: 'name', render: (_, row) => <div className="rl-owner"><strong>{row.name}</strong>{row.note && <small>{row.note}</small>}</div> },
        { title: '运营商', dataIndex: 'provider', width: 160 },
        { title: '专线', dataIndex: 'used', width: 90, render: (value, row) => used(value, `${ROOT}/circuits?q=${encodeURIComponent(row.name)}`) },
        ...opsColumn<Pop>(row => ops(() => setPop({ open: true, providerId: row.provider_id, row }),
          () => remove('局端', 'pops', row.id, row.name, '还有专线接入这个局端时不能删除。'), row.name)),
      ]} />
    <p className="rl-note">局端是运营商的接入局点或 PE 设备；同一局端的专线会一起受局端故障影响。</p>
  </>

  const typeTab = <>
    {tools('搜索专线类型', <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setKind({ open: true })}>新增专线类型</Button>)}
    <Table<CircuitType> size="middle" rowKey="id" loading={types.isLoading} pagination={pageLocally()}
      dataSource={(types.data?.types || []).filter(t => has(t.name, t.note))}
      columns={[
        { title: '名称', dataIndex: 'name', width: 200, render: value => <strong style={{ fontWeight: 550 }}>{value}</strong> },
        { title: '说明', dataIndex: 'note', render: value => value || muted() },
        { title: '专线', dataIndex: 'used', width: 100, render: (value, row) => used(value, `${ROOT}/circuits?type=${row.id}`) },
        ...opsColumn<CircuitType>(row => ops(() => setKind({ open: true, row }), () => remove('专线类型', 'circuit-types', row.id, row.name), row.name)),
      ]} />
  </>

  const riskTab = <>
    {tools('搜索共享风险', <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setRisk({ open: true })}>新增共享风险</Button>)}
    <Table<RiskRow> size="middle" rowKey="id" loading={risks.isLoading} pagination={pageLocally()}
      dataSource={(risks.data?.risks || []).filter(r => has(r.name, r.note, ...r.circuits))}
      locale={{ emptyText: <div className="rl-empty">{text ? '没有符合条件的共享风险' : '还没有共享风险。在专线上填写经过的光缆、管道，系统据此找出主备线共用的物理资源。'}</div> }}
      columns={[
        { title: '名称', dataIndex: 'name', width: 220, render: value => <strong style={{ fontWeight: 550 }}>{value}</strong> },
        { title: '类型', dataIndex: 'kind', width: 90 },
        { title: '经过的专线', key: 'circuits', render: (_, row) => row.circuits.length ? row.circuits.join('、') : muted('没有专线') },
        { title: '说明', dataIndex: 'note', width: 200, render: value => value || muted() },
        ...opsColumn<RiskRow>(row => ops(() => setRisk({ open: true, row }), () => remove('共享风险', 'risks', row.id, row.name,
          row.circuits.length ? `会从 ${row.circuits.length} 条专线上去掉这个标注。` : undefined), row.name)),
      ]} />
  </>

  const error = providers.error || types.error || risks.error
  return <Layout active="providers" crumbs={['资源台账', '运营商与分类']} title="运营商与分类"
    subtitle="运营商和它的运营商网络、接入局端，以及专线类型、共享风险。新增专线时没有的运营商、运营商网络、局端和共享风险，也可以直接输入名称新建。">
    {error ? <Alert type="error" showIcon message={errorText(error)} /> : null}
    <Tabs activeKey={tab} onChange={key => go(key)} items={[
      { key: 'providers', label: `运营商（${list.length}）`, children: providerTab },
      { key: 'networks', label: `运营商网络（${networks.length}）`, children: networkTab },
      { key: 'pops', label: `局端（${pops.length}）`, children: popTab },
      { key: 'types', label: `专线类型（${types.data?.types.length ?? 0}）`, children: typeTab },
      { key: 'risks', label: `共享风险（${risks.data?.risks.length ?? 0}）`, children: riskTab },
    ]} />
    <ProviderModal open={provider.open} provider={provider.row} onClose={() => setProvider({ open: false })} onSaved={done(() => setProvider({ open: false }))} />
    <NetworkModal open={network.open} providers={list} providerId={network.providerId} network={network.row}
      onClose={() => setNetwork({ open: false })} onSaved={done(() => setNetwork({ open: false }))} />
    <PopModal open={pop.open} providers={list} providerId={pop.providerId} pop={pop.row} onClose={() => setPop({ open: false })} onSaved={done(() => setPop({ open: false }))} />
    <TypeModal open={kind.open} item={kind.row} onClose={() => setKind({ open: false })} onSaved={done(() => setKind({ open: false }))} />
    <RiskModal open={risk.open} risk={risk.row} onClose={() => setRisk({ open: false })} onSaved={done(() => setRisk({ open: false }))} />
  </Layout>
}
