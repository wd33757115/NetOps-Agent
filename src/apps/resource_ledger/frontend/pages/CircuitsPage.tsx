import React, { useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space as Gap, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { CircuitDrawer } from '../components/CircuitForms'
import { useServerPage } from '../components/Paging'
import { CIRCUIT_STATE_CLASS } from '../components/Routes'
import { CIRCUIT_STATES, errorText, ledger, type CircuitDetail, type CircuitEnd, type CircuitRow } from '../api'

const mbps = (value: number | null) => value === null ? '—' : value >= 1000 && value % 1000 === 0 ? `${value / 1000} Gbps` : `${value} Mbps`

function EndCell({ end }: { end: CircuitEnd | null }) {
  if (!end?.place) return <span className="rl-muted">只有一端</span>
  return <span title={end.place.detail}>{end.place.name}{end.place.kind === 'network' && <span className="rl-muted">（运营商网络）</span>}</span>
}

export default function CircuitsPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const refresh = useRefresh()
  const paging = useServerPage()
  const [drawer, setDrawer] = useState<{ open: boolean; circuit?: CircuitDetail | null }>({ open: false })
  const filters = { q: params.get('q') || undefined, provider_id: params.get('provider') || undefined, type_id: params.get('type') || undefined,
    status: params.get('status') || undefined, site_id: params.get('site') || undefined }
  const risky = params.get('risky') === '1'
  const options = useQuery('rl-circuit-options', ledger.circuitOptions)
  const list = useQuery(['rl-circuits', filters, risky, paging.offset, paging.limit],
    () => ledger.circuits({ ...filters, single_point: risky || undefined, offset: paging.offset, limit: paging.limit }), { keepPreviousData: true })
  const canEdit = Boolean(list.data?.can_edit)
  const rows = list.data?.circuits || []
  const counts = list.data?.counts || {}
  const set = (key: string, value?: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    setParams(next); paging.reset()
  }
  const edit = async (row: CircuitRow) => {
    try { setDrawer({ open: true, circuit: await ledger.circuit(row.id) }) } catch (error) { message.error(errorText(error)) }
  }
  const remove = (row: CircuitRow) => Modal.confirm({
    title: `删除专线 ${row.cid}？`, content: '两端的互联地址会释放，互联网段转为待回收。只是线路停用时，建议把状态改为“已拆除”保留记录。',
    okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.remove('circuits', row.id); message.success('专线已删除'); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })

  return <Layout active="circuits" crumbs={['资源台账', '专线']} title="专线"
    subtitle="每条专线由“运营商 + 电路编号”确定，两端落在站点或运营商网络上；互联地址从网段规划里分配。"
    actions={canEdit && <>
      <Button onClick={() => navigate(`${ROOT}/import`)}>导入</Button>
      <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setDrawer({ open: true })}>新增专线</Button>
    </>}>
    {list.error ? <Alert type="error" showIcon message={errorText(list.error)} /> : null}
    <div className="rl-summary-strip">
      {['在用', '施工中', '规划', '离线'].map(state => <button type="button" key={state} className={filters.status === state ? 'on' : ''}
        onClick={() => set('status', filters.status === state ? undefined : state)}><strong>{counts[state] || 0}</strong><span>{state}</span></button>)}
      <button type="button" className={`warn ${risky ? 'on' : ''}`} onClick={() => set('risky', risky ? undefined : '1')}>
        <strong>{list.data?.single_points || 0}</strong><span>是单点的专线</span></button>
    </div>
    <div className="rl-tools">
      <Input.Search allowClear placeholder="搜索电路编号、站点、设备、接口、互联地址、局端" style={{ maxWidth: 380 }} defaultValue={filters.q} key={filters.q}
        onSearch={value => set('q', value.trim() || undefined)} />
      <Select allowClear showSearch optionFilterProp="label" placeholder="运营商" style={{ width: 150 }} value={filters.provider_id} onChange={value => set('provider', value)}
        options={(options.data?.providers || []).map(p => ({ value: p.id, label: p.name }))} />
      <Select allowClear showSearch optionFilterProp="label" placeholder="类型" style={{ width: 140 }} value={filters.type_id} onChange={value => set('type', value)}
        options={(options.data?.types || []).map(t => ({ value: t.id, label: t.name }))} />
      <Select allowClear showSearch optionFilterProp="label" placeholder="站点" style={{ width: 160 }} value={filters.site_id} onChange={value => set('site', value)}
        options={(options.data?.sites || []).map(s => ({ value: s.id, label: s.name }))} />
      <Select allowClear placeholder="状态" style={{ width: 110 }} value={filters.status} onChange={value => set('status', value)}
        options={CIRCUIT_STATES.map(value => ({ value }))} />
    </div>
    <Table<CircuitRow> size="middle" rowKey="id" loading={list.isLoading} dataSource={rows} scroll={{ x: 1080 }}
      pagination={paging.pagination(list.data?.total || 0)}
      locale={{ emptyText: <div className="rl-empty">{Object.values(filters).some(Boolean) || risky ? '没有符合条件的专线'
        : '还没有专线。先在“运营商与分类”里建好运营商，再新增专线；也可以用 Excel 一次导入。'}</div> }}
      columns={[
        { title: '电路编号', key: 'cid', width: 200, render: (_, row) => <div className="rl-owner">
          <Link to={`${ROOT}/circuits/${row.id}`} className="rl-strong-link">{row.cid}</Link>
          <small>{[row.provider?.name, row.type?.name].filter(Boolean).join(' · ')}</small></div> },
        { title: '两端', key: 'ends', render: (_, row) => <div className="rl-ends"><EndCell end={row.a} /><i aria-hidden="true">⟷</i><EndCell end={row.z} /></div> },
        { title: '带宽', dataIndex: 'bandwidth', width: 96, render: mbps },
        { title: '互联地址', key: 'interconnect', width: 140, render: (_, row) => row.interconnect
          ? <Link to={`${ROOT}/prefixes/${row.interconnect.id}`} className="rl-mono">{row.interconnect.network}</Link> : <span className="rl-muted">—</span> },
        { title: '状态', dataIndex: 'status', width: 84, render: value => <span className={`rl-state ${CIRCUIT_STATE_CLASS[value] || ''}`}>{value}</span> },
        { title: '单点', key: 'spof', width: 200, render: (_, row) => row.single_point_for?.length
          ? <span className="rl-flag bad" title={row.single_point_for.join('、')}>断了会让 {row.single_point_for.length} 个站点断开</span>
          : <span className="rl-muted">—</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 76, render: (_: unknown, row: CircuitRow) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.cid}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => void edit(row)} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.cid}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove(row)} />
        </Gap> }] : []),
      ]} />
    <p className="rl-note">“单点”指这条线一断，就有站点到不了核心站点；完整的检查在“站点韧性”里。专线类型、状态不限行业，只记录技术信息。</p>
    <CircuitDrawer open={drawer.open} circuit={drawer.circuit} onClose={() => setDrawer({ open: false })}
      onSaved={id => { setDrawer({ open: false }); refresh(); if (!drawer.circuit) navigate(`${ROOT}/circuits/${id}`) }} />
  </Layout>
}
