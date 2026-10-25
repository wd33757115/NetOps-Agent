import React, { useMemo, useState } from 'react'
import { Alert, Button, Input, Modal, Segmented, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { AddressModal, AllocateModal, BlockModal, CarveModal, ReservationModal, useRefresh } from '../components/Forms'
import { LevelChip, OwnerCell, PathLine, State, UsageCell, ZoneCell } from '../components/Cells'
import { pageLocally } from '../components/Paging'
import { errorText, ipText, ipValue, ledger, type AddressRow, type PrefixDetail, type TreeRow } from '../api'

type Cell = { value: number; kind: 'system' | 'gateway' | 'allocated' | 'reserved' | 'free' | 'outside'; label: string }

export default function PrefixDetailPage() {
  const { prefixId = '' } = useParams()
  const navigate = useNavigate()
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  const detail = useQuery(['rl-prefix', prefixId], () => ledger.prefix(prefixId))
  const [editing, setEditing] = useState(false)
  const [carving, setCarving] = useState<{ within?: { first: string; last: string } } | null>(null)
  const [allocating, setAllocating] = useState<{ open: boolean; ip?: string }>({ open: false })
  const [reserving, setReserving] = useState(false)
  const [address, setAddress] = useState<AddressRow | null>(null)
  const [selected, setSelected] = useState<number>()
  const [block, setBlock] = useState<number>()
  const [filter, setFilter] = useState('')
  const prefix = detail.data

  if (detail.error) return <Layout active="prefixes" crumbs={[<Link key="a" to={ROOT}>网段规划</Link>]} title="网段">
    <Alert type="error" showIcon message={errorText(detail.error)} action={<Button onClick={() => navigate(ROOT)}>返回网段规划</Button>} />
  </Layout>
  if (!prefix) return <Layout active="prefixes" crumbs={[<Link key="a" to={ROOT}>网段规划</Link>]} title="网段"><div className="rl-empty">正在读取…</div></Layout>

  const remove = () => Modal.confirm({
    title: `删除网段 ${prefix.network}？`,
    content: prefix.child_count ? '它的子网段会保留，并上移一级。' : '只能删除没有已分配地址和保留范围的网段。',
    okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => {
      try { await ledger.remove('prefixes', prefix.id); message.success('网段已删除'); refresh(); navigate(prefix.ancestors.length ? `${ROOT}?root=${top}` : ROOT) }
      catch (error) { message.error(errorText(error)); throw error }
    },
  })
  const leaf = !prefix.child_rows
  const top = prefix.ancestors[0]?.id || prefix.id

  return <Layout active="prefixes"
    crumbs={[<Link key="root" to={`${ROOT}?root=${top}`}>网段规划</Link>, prefix.network]}
    title={<>{prefix.network}{prefix.title && <span className="rl-title-owner">{prefix.title}</span>}</>}
    subtitle={<span className="rl-path">
      {prefix.ancestors.map((a, index) => <React.Fragment key={a.id}>{index > 0 && <i>›</i>}
        <Link to={`${ROOT}/prefixes/${a.id}`} title={a.network}>{a.title || a.network}</Link></React.Fragment>)}
      {!prefix.ancestors.length && <span>地址资源</span>}
    </span>}
    actions={prefix.can_edit && <>
      <Button icon={<EditOutlined aria-hidden="true" />} onClick={() => setEditing(true)}>编辑网段</Button>
      <Button onClick={() => setCarving({})}>划分下一级</Button>
      <Button danger icon={<DeleteOutlined aria-hidden="true" />} onClick={remove}>删除</Button>
      {leaf && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} disabled={!prefix.next_free || ['保留', '待回收'].includes(prefix.status)}
        onClick={() => setAllocating({ open: true })}>分配下一个空闲 IP</Button>}
    </>}>
    <dl className="rl-facts">
      <div><dt>层级</dt><dd><LevelChip level={prefix.level} root={!prefix.parent_id} /></dd></div>
      <div className="wide"><dt>归属</dt><dd><PathLine path={prefix.path} /></dd></div>
      <div><dt>网络区域</dt><dd><ZoneCell zone={prefix.zone} /></dd></div>
      <div><dt>VLAN</dt><dd>{prefix.vlan ?? <span className="rl-muted">—</span>}</dd></div>
      <div><dt>网关</dt><dd>{prefix.gateway || <span className="rl-muted">—</span>}</dd></div>
      <div><dt>状态</dt><dd><State value={prefix.status} /></dd></div>
      <div><dt>负责人</dt><dd>{prefix.owner || <span className="rl-muted">—</span>}</dd></div>
      <div><dt>申请工单号</dt><dd>{prefix.ticket || <span className="rl-muted">—</span>}</dd></div>
    </dl>
    {(prefix.purpose || prefix.note) && <p className="rl-note" style={{ marginTop: -10, marginBottom: 18 }}>
      {[prefix.purpose && prefix.purpose !== prefix.title && `用途：${prefix.purpose}`, prefix.note && `备注：${prefix.note}`].filter(Boolean).join('　')}</p>}
    {leaf ? <LeafView prefix={prefix} selected={selected} setSelected={setSelected} block={block} setBlock={setBlock}
      onAllocate={ip => setAllocating({ open: true, ip })} onEdit={setAddress} onReserve={() => setReserving(true)} refresh={refresh}
      filter={filter} setFilter={setFilter} />
      : <SummaryView prefix={prefix} onCarve={within => setCarving({ within })} />}
    <BlockModal open={editing} prefix={prefix} overview={overview.data} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh() }} />
    <CarveModal open={Boolean(carving)} parentId={prefix.id} within={carving?.within} overview={overview.data}
      onClose={() => setCarving(null)} onSaved={id => { setCarving(null); refresh(); navigate(`${ROOT}/prefixes/${id}`) }} />
    {leaf && <>
      <AllocateModal open={allocating.open} prefix={prefix} ip={allocating.ip} onClose={() => setAllocating({ open: false })}
        onDone={row => { setSelected(ipValue(row.ip)); refresh() }} />
      <ReservationModal open={reserving} prefix={prefix} onClose={() => setReserving(false)} onSaved={() => { setReserving(false); refresh() }} />
      <AddressModal open={Boolean(address)} address={address} onClose={() => setAddress(null)} onSaved={() => { setAddress(null); refresh() }} />
    </>}
  </Layout>
}

function SummaryView({ prefix, onCarve }: { prefix: PrefixDetail; onCarve: (within?: { first: string; last: string }) => void }) {
  const rows = prefix.child_rows || []
  return <>
    <div className="rl-scope"><h2>下一级</h2><span>已划分 {prefix.usage.percent}%，地址分配在最末一级的网段里进行</span></div>
    {Boolean(prefix.stray_addresses?.length) && <Alert type="warning" showIcon style={{ marginBottom: 12 }}
      message={`有 ${prefix.stray_addresses!.length} 个地址直接登记在这个汇总网段上，不属于任何子网段`}
      description={prefix.stray_addresses!.slice(0, 10).map(a => a.ip).join('、')} />}
    <Table<TreeRow> size="middle" pagination={pageLocally(50)} rowKey={row => row.type === 'gap' ? `gap-${row.first}` : row.id}
      rowClassName={row => row.type === 'gap' ? 'rl-gap' : ''} dataSource={rows} scroll={{ x: 960 }}
      columns={[
        { title: '网段', key: 'network', width: 170, render: (_, row) => row.type === 'gap'
          ? <span className="rl-cidr">未划分</span> : <span className="rl-cidr"><Link to={`${ROOT}/prefixes/${row.id}`}>{row.network}</Link></span> },
        { title: '层级', key: 'level', width: 104, onCell: row => (row.type === 'gap' ? { colSpan: 3 } : {}), render: (_, row) => row.type === 'gap'
          ? `${row.first} – ${row.last}${row.blocks ? `，可划 ${row.blocks} 个 /${row.unit}` : `，共 ${row.size} 个地址`}` : <LevelChip level={row.level} /> },
        { title: '归属', key: 'owner', onCell: row => (row.type === 'gap' ? { colSpan: 0 } : {}), render: (_, row) => row.type === 'gap' ? null : <OwnerCell row={row} /> },
        { title: '状态', key: 'status', width: 84, onCell: row => (row.type === 'gap' ? { colSpan: 0 } : {}), render: (_, row) => row.type === 'gap' ? null : <State value={row.status} /> },
        { title: '使用率', key: 'usage', width: 180, render: (_, row) => row.type === 'gap'
          ? (prefix.can_edit ? <Button type="link" size="small" onClick={() => onCarve({ first: row.first, last: row.last })}>划分</Button> : null) : <UsageCell usage={row.usage} /> },
      ]} />
  </>
}

function LeafView({ prefix, selected, setSelected, block, setBlock, onAllocate, onEdit, onReserve, refresh, filter, setFilter }: {
  prefix: PrefixDetail; selected?: number; setSelected: (value?: number) => void; block?: number; setBlock: (value: number) => void
  onAllocate: (ip?: string) => void; onEdit: (row: AddressRow) => void; onReserve: () => void; refresh: () => void
  filter: string; setFilter: (value: string) => void
}) {
  const first = ipValue(prefix.first_ip!), last = ipValue(prefix.last_ip!)
  const usableFirst = prefix.usable ? ipValue(prefix.usable.first) : first
  const usableLast = prefix.usable ? ipValue(prefix.usable.last) : last
  const blocks = useMemo(() => {
    const result: number[] = []
    for (let value = first; value <= last; value += 256) result.push(value)
    return result
  }, [first, last])
  const start = block !== undefined && block >= first && block <= last ? block : first
  const byValue = useMemo(() => new Map((prefix.addresses || []).map(a => [ipValue(a.ip), a])), [prefix.addresses])
  const reservations = useMemo(() => (prefix.reservations || []).map(r => ({ ...r, lo: ipValue(r.first), hi: ipValue(r.last) })), [prefix.reservations])
  const gateway = prefix.gateway ? ipValue(prefix.gateway) : undefined
  const kindOf = (value: number): Cell['kind'] => {
    if (value < first || value > last) return 'outside'
    if (value < usableFirst || value > usableLast) return 'system'
    if (value === gateway) return 'gateway'
    if (byValue.has(value)) return 'allocated'
    if (reservations.some(r => r.lo <= value && value <= r.hi)) return 'reserved'
    return 'free'
  }
  // Up to a /24 at a time, starting on a row of 16 so small prefixes do not show empty rows.
  const cells: Cell[] = []
  const small = last - first + 1 <= 256
  const windowStart = small ? first - (first % 16) : start - (start % 256)
  const windowEnd = small ? last : Math.min(windowStart + 255, last)
  const span = Math.ceil((windowEnd - windowStart + 1) / 16) * 16
  for (let offset = 0; offset < span; offset++) {
    const value = windowStart + offset
    const kind = kindOf(value)
    const octet = value % 256
    cells.push({ value, kind, label: kind === 'system' ? (value === first ? '网' : '播') : kind === 'gateway' ? '关' : String(octet) })
  }
  const pick = selected !== undefined ? selected : undefined
  const pickedKind = pick !== undefined ? kindOf(pick) : undefined
  const pickedAddress = pick !== undefined ? byValue.get(pick) : undefined
  const pickedReservation = pick !== undefined ? reservations.find(r => r.lo <= pick && pick <= r.hi) : undefined
  const release = (row: AddressRow) => Modal.confirm({
    title: `释放 ${row.ip}？`, content: '释放后这个地址可以再次分配。', okText: '释放', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.release(row.id); message.success('地址已释放'); setSelected(undefined); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })
  const unreserve = (id: string) => Modal.confirm({
    title: '删除这段保留范围？', okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.remove('reservations', id); message.success('保留范围已删除'); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })
  const rows = (prefix.addresses || []).filter(a => !filter || `${a.ip} ${a.hostname || ''} ${a.dns_name || ''} ${a.note || ''}`.toLowerCase().includes(filter.toLowerCase()))
  const stats = prefix.stats!

  return <>
    <div className="rl-detail">
      <section>
        <div className="rl-map-head"><h2>地址分布</h2>
          <div className="rl-legend">
            <span><i style={{ background: 'var(--rl-alloc)', borderColor: 'var(--rl-alloc)' }} />已分配</span>
            <span><i style={{ background: 'var(--rl-resv-bg)', borderColor: '#efc77e' }} />保留</span>
            <span><i style={{ background: 'var(--rl-gw)', borderColor: 'var(--rl-gw)' }} />网关</span>
            <span><i />空闲</span>
            <span><i style={{ background: '#eef1f5', borderColor: '#eef1f5' }} />网络 / 广播</span>
          </div>
        </div>
        {blocks.length > 1 && <div className="rl-blocks"><Segmented size="small" value={windowStart}
          onChange={value => setBlock(Number(value))} options={blocks.slice(0, 64).map(value => ({ value, label: `${ipText(value)}/24` }))} />
          {blocks.length > 64 && <span className="rl-muted">只列出前 64 段</span>}</div>}
        <div className="rl-map" role="grid" aria-label={`${prefix.network} 地址分布`}>
          <div />{Array.from({ length: 16 }, (_, i) => <div key={i} className="rl-col">{i}</div>)}
          {Array.from({ length: Math.ceil(cells.length / 16) }, (_, row) => <React.Fragment key={row}>
            <div className="rl-row">.{(windowStart % 256) + row * 16}</div>
            {cells.slice(row * 16, row * 16 + 16).map(cell => <button type="button" key={cell.value} className={`rl-cell ${cell.kind}`}
              aria-pressed={cell.value === pick} aria-label={`${ipText(cell.value)} ${{ system: '网络或广播地址', gateway: '网关', allocated: '已分配', reserved: '保留', free: '空闲', outside: '' }[cell.kind]}`}
              disabled={cell.kind === 'outside'} onClick={() => setSelected(cell.value)}>{cell.label}</button>)}
          </React.Fragment>)}
        </div>
      </section>
      <aside>
        <div className="rl-stats">
          <div><strong>{stats.allocated}</strong><span>已分配（含网关）</span></div>
          <div><strong>{stats.reserved}</strong><span>保留</span></div>
          <div><strong>{stats.free}</strong><span>空闲</span></div>
        </div>
        <UsageCell usage={prefix.usage} />
        <div className="rl-card" style={{ marginTop: 16 }}>
          {pick === undefined ? <><h3>选一个地址</h3><p className="rl-card-sub">点左边的格子查看地址的分配情况。</p></>
            : <>
              <h3>{ipText(pick)}</h3>
              <p className="rl-card-sub">{{ system: pick === first ? '网络地址，不能分配' : '广播地址，不能分配', gateway: '网关地址', allocated: '已分配', reserved: '在保留范围内', free: '空闲', outside: '' }[pickedKind!]}</p>
              {pickedAddress && <>
                <dl className="rl-kv">
                  <dt>用途</dt><dd>{pickedAddress.kind}</dd>
                  {pickedAddress.host
                    ? <><dt>主机</dt><dd><Link to={`${ROOT}/hosts?q=${pickedAddress.ip}`}>{pickedAddress.host.hostname}</Link>，{pickedAddress.host.status}</dd></>
                    : pickedAddress.circuit
                      ? <><dt>专线</dt><dd><Link to={`${ROOT}/circuits/${pickedAddress.circuit.id}`}>{pickedAddress.circuit.cid}</Link> 的 {pickedAddress.circuit.side} 端</dd></>
                      : <><dt>名称</dt><dd>{pickedAddress.hostname || <span className="rl-muted">未填写</span>}</dd></>}
                  <dt>DNS 名</dt><dd>{pickedAddress.dns_name || <span className="rl-muted">未填写</span>}</dd>
                  {pickedAddress.note && <><dt>备注</dt><dd>{pickedAddress.note}</dd></>}
                  <dt>分配</dt><dd>{pickedAddress.assigned_by}，{pickedAddress.assigned_at.slice(0, 10)}</dd>
                </dl>
                {prefix.can_edit && <div className="rl-card-actions"><Button size="small" onClick={() => onEdit(pickedAddress)}>编辑</Button>
                  {pickedAddress.host || pickedAddress.circuit ? <span className="rl-muted" style={{ fontSize: 12.5 }}>
                      {pickedAddress.host ? '在主机台账里移除后才能释放' : '在专线里修改或拆除后释放'}</span>
                    : <Button size="small" danger onClick={() => release(pickedAddress)}>释放地址</Button>}</div>}
              </>}
              {pickedReservation && <dl className="rl-kv"><dt>保留用途</dt><dd>{pickedReservation.purpose}</dd><dt>范围</dt><dd>{pickedReservation.first} – {pickedReservation.last}</dd></dl>}
              {pickedKind === 'free' && prefix.can_edit && !['保留', '待回收'].includes(prefix.status) &&
                <div className="rl-card-actions"><Button type="primary" size="small" onClick={() => onAllocate(ipText(pick))}>分配这个地址</Button></div>}
            </>}
        </div>
        <div className="rl-section"><h3>保留范围{prefix.can_edit && <Button size="small" type="link" onClick={onReserve}>新增保留</Button>}</h3>
          {reservations.length ? <ul className="rl-ranges">{reservations.map(r => <li key={r.id}>
            <span>{r.first} – {r.last}：{r.purpose}</span>
            {prefix.can_edit && <Button type="text" size="small" danger aria-label={`删除保留范围 ${r.first} – ${r.last}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => unreserve(r.id)} />}
          </li>)}</ul> : <p className="rl-note">没有保留范围</p>}
        </div>
      </aside>
    </div>
    <div className="rl-section">
      <div className="rl-scope" style={{ justifyContent: 'space-between' }}><h2>已分配地址</h2>
        <Input.Search allowClear placeholder="搜索 IP、名称或 DNS 名" style={{ maxWidth: 280 }} value={filter} onChange={event => setFilter(event.target.value)} /></div>
      {prefix.addresses_truncated && <Alert type="info" showIcon style={{ marginBottom: 10 }} message="地址较多，只列出前 5000 个" />}
      <Table<AddressRow> size="middle" rowKey="id" dataSource={rows} pagination={pageLocally()}
        onRow={row => ({ onClick: () => { const value = ipValue(row.ip); setSelected(value); setBlock(value - (value % 256)) } })}
        locale={{ emptyText: '还没有分配地址' }}
        columns={[
          { title: 'IP', dataIndex: 'ip', width: 140, render: value => <strong style={{ fontWeight: 550 }}>{value}</strong> },
          { title: '用途', dataIndex: 'kind', width: 100 },
          { title: '主机 / 名称', key: 'host', render: (_, row) => row.host
            ? <span>{row.host.hostname}<small className="rl-sub">主机台账，{row.host.status}</small></span>
            : row.circuit ? <span><Link to={`${ROOT}/circuits/${row.circuit.id}`}>{row.circuit.cid}</Link><small className="rl-sub">专线 {row.circuit.side} 端互联地址</small></span>
            : row.hostname || <span className="rl-muted">未关联主机</span> },
          { title: 'DNS 名', dataIndex: 'dns_name', render: value => value || <span className="rl-muted">—</span> },
          { title: '分配', key: 'assigned', width: 170, render: (_, row) => `${row.assigned_by}，${row.assigned_at.slice(0, 10)}` },
        ]} />
    </div>
  </>
}
