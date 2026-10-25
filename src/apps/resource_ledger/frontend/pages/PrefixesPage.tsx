import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Select, Table, type TableProps } from 'antd'
import { EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { BlockModal, CarveModal, LevelsModal, ResourceModal, SpacesModal, useRefresh } from '../components/Forms'
import { LevelChip, OwnerCell, State, UsageCell } from '../components/Cells'
import { pageLocally } from '../components/Paging'
import { PREFIX_STATES, errorText, ledger, type PrefixRow, type Resource, type SearchResult, type TreeRow } from '../api'

export default function PrefixesPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  const resources = useQuery('rl-resources', ledger.resources)
  const [filter, setFilter] = useState('')
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [level, setLevel] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)
  const [carving, setCarving] = useState<{ parentId: string; within?: { first: string; last: string } } | null>(null)
  const [editing, setEditing] = useState<PrefixRow | null>(null)
  const [levelsOpen, setLevelsOpen] = useState(false)
  const [spacesOpen, setSpacesOpen] = useState(false)

  const list = resources.data?.resources || []
  const spaces = resources.data?.spaces || []
  const levels = resources.data?.levels || []
  const several = spaces.length > 1
  const rootId = params.get('root') || list[0]?.id
  const current = list.find(item => item.id === rootId)
  useEffect(() => { setCollapsed(new Set()) }, [rootId])
  const tree = useQuery(['rl-subtree', rootId], () => ledger.subtree(rootId!), { enabled: Boolean(current) })
  const results = useQuery(['rl-search', search], () => ledger.search(search), { enabled: Boolean(search) })
  const canEdit = Boolean(resources.data?.can_edit)
  const deepest = Math.max(0, ...levels.map(lv => lv.position))

  const groups = useMemo(() => {
    const visible = list.filter(item => !filter || `${item.network} ${item.title || ''} ${item.purpose || ''}`.toLowerCase().includes(filter.toLowerCase()))
    if (!several) return [{ name: '', items: visible }]
    return spaces.map(space => ({ name: space.name, items: visible.filter(item => item.space.id === space.id) })).filter(group => group.items.length)
  }, [list, filter, several, spaces])

  const rows = useMemo(() => {
    const all = tree.data?.rows || []
    const parents = new Map(all.filter((r): r is PrefixRow => r.type === 'prefix').map(r => [r.id, r.parent_id]))
    const underCollapsed = (id: string | null) => {
      for (let node = id; node; node = parents.get(node) || null) if (collapsed.has(node)) return true
      return false
    }
    const filtered = level || status
    return all.filter(row => {
      if (row.type === 'prefix') {
        if (filtered) return (!level || (level === 'none' ? !row.level : row.level?.id === level)) && (!status || row.status === status)
        return !underCollapsed(row.parent_id)
      }
      return !filtered && !underCollapsed(row.parent_id)
    })
  }, [tree.data, collapsed, level, status])

  const toggle = (id: string) => setCollapsed(previous => {
    const next = new Set(previous)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  const open = (id: string) => { setParams({ root: id }); setSearch(''); setQuery('') }
  // Blocks above the last level are carved further; the last level hands out addresses.
  const carvable = (row: PrefixRow) => row.child_count > 0 || !row.level || row.level.position < deepest
  const gapCols = 5
  const hideInGap = (row: TreeRow) => (row.type === 'gap' ? { colSpan: 0 } : {})

  const columns: TableProps<TreeRow>['columns'] = [
    { title: '网段', key: 'network', width: 196, render: (_, row) => row.type === 'gap'
      ? <div className="rl-cidr" style={{ paddingLeft: row.depth * 16 + 20 }}>未划分</div>
      : <div className="rl-cidr" style={{ paddingLeft: row.depth * 16 }}>
          <button type="button" className="rl-caret" disabled={!row.child_count} onClick={() => toggle(row.id)}
            aria-label={collapsed.has(row.id) ? `展开 ${row.network}` : `收起 ${row.network}`} aria-expanded={row.child_count ? !collapsed.has(row.id) : undefined}>
            {row.child_count ? (collapsed.has(row.id) ? '▸' : '▾') : ''}
          </button>
          <Link to={`${ROOT}/prefixes/${row.id}`}>{row.network}</Link>
        </div> },
    // An unplanned stretch reads as one sentence across the middle columns.
    { title: '层级', key: 'level', width: 92, onCell: row => (row.type === 'gap' ? { colSpan: gapCols } : {}), render: (_, row) => row.type === 'gap'
      ? <span>{row.first} – {row.last}{row.blocks ? `，可划 ${row.blocks} 个 /${row.unit}` : `，共 ${row.size} 个地址`}</span>
      : <LevelChip level={row.level} root={!row.parent_id} /> },
    { title: '归属', key: 'owner', onCell: hideInGap, render: (_, row) => row.type === 'gap' ? null : <OwnerCell row={row} /> },
    { title: 'VLAN', key: 'vlan', width: 56, onCell: hideInGap, render: (_, row) => row.type === 'gap' ? null : row.vlan ?? <span className="rl-muted">—</span> },
    { title: '状态', key: 'status', width: 80, onCell: hideInGap, render: (_, row) => row.type === 'gap' ? null : <State value={row.status} /> },
    { title: '使用率', key: 'usage', width: 150, onCell: hideInGap, render: (_, row) => row.type === 'gap' ? null : <UsageCell usage={row.usage} /> },
    { title: '', key: 'action', width: 72, render: (_, row) => {
      if (!canEdit) return null
      if (row.type === 'gap') return <Button type="link" size="small" onClick={() => setCarving({ parentId: row.parent_id, within: { first: row.first, last: row.last } })}>划分</Button>
      return carvable(row)
        ? <Button type="link" size="small" onClick={() => setCarving({ parentId: row.id })}>划分</Button>
        : <Button type="link" size="small" onClick={() => navigate(`${ROOT}/prefixes/${row.id}`)}>分配 IP</Button>
    } },
  ]
  const rootRow = tree.data?.rows.find((r): r is PrefixRow => r.type === 'prefix' && r.id === rootId)

  return <Layout active="prefixes" crumbs={['资源台账', '网段规划']} title="网段规划"
    subtitle="从总体地址资源出发，按地域、机房、网络区域、业务系统、子系统逐级划分；下级自动继承上级的归属。"
    actions={canEdit && <>
      <Button onClick={() => navigate(`${ROOT}/import`)}>导入</Button>
      <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setAdding(true)}>新增地址资源</Button>
    </>}>
    {resources.error ? <Alert type="error" showIcon message={errorText(resources.error)} /> : null}
    <div className="rl-split">
      <aside className="rl-spaces" aria-label="地址资源">
        <Input allowClear prefix={<SearchOutlined aria-hidden="true" />} placeholder="搜索地址资源" value={filter} onChange={event => setFilter(event.target.value)} />
        <div className="rl-spaces-count">共 {list.length} 段地址资源</div>
        {groups.map(group => <div key={group.name}>
          {group.name && <div className="rl-space-group">{group.name}<span>{group.items.length}</span></div>}
          {group.items.map(item => <ResourceButton key={item.id} item={item} current={item.id === rootId} onClick={() => open(item.id)} />)}
        </div>)}
        {!list.length && !resources.isLoading && <p className="rl-note">先新增一段总体地址，例如 10.1.0.0/16，再逐级划分。也可以用 Excel 一次导入。</p>}
        <div className="rl-levels">
          <h4>规划层级{canEdit && <Button type="link" size="small" onClick={() => setLevelsOpen(true)}>调整</Button>}</h4>
          {levels.length ? <ol>{levels.map(lv => <li key={lv.id}><LevelChip level={lv} /><small>{lv.default_length ? `默认 /${lv.default_length}` : '按上级建议'}</small></li>)}</ol>
            : <p className="rl-note">没有规划层级，网段只按包含关系分级。</p>}
          {canEdit && <Button type="link" size="small" className="rl-domains" onClick={() => setSpacesOpen(true)}>
            {several ? `${spaces.length} 个路由域` : '路由域'}：管理</Button>}
        </div>
      </aside>
      <section style={{ minWidth: 0 }}>
        <div className="rl-tools">
          <Input.Search allowClear style={{ maxWidth: 420 }} placeholder="搜索 IP、网段、系统或区域" value={query}
            onChange={event => { setQuery(event.target.value); if (!event.target.value) setSearch('') }} onSearch={value => setSearch(value.trim())} enterButton="搜索" />
          {!search && <>
            <Select allowClear placeholder="层级" style={{ width: 140 }} value={level} onChange={setLevel}
              options={[...levels.map(lv => ({ value: lv.id, label: lv.title })), { value: 'none', label: '未分层' }]} />
            <Select allowClear placeholder="状态" style={{ width: 110 }} value={status} onChange={setStatus} options={PREFIX_STATES.map(value => ({ value }))} />
          </>}
        </div>
        {search ? <SearchResults query={search} several={several} data={results.data} loading={results.isLoading} error={results.error} />
          : current ? <>
            <div className="rl-scope"><h2>{current.network}{current.title ? ` ${current.title}` : ''}</h2>
              <span>{[current.planned !== null && `已划分 ${current.planned}%`, current.usage !== null && `地址使用率 ${current.usage}%`,
                several && `路由域 ${current.space.name}`].filter(Boolean).join('，') || '还没有划分'}</span>
              {canEdit && rootRow && <Button type="text" size="small" icon={<EditOutlined aria-hidden="true" />} onClick={() => setEditing(rootRow)}>编辑</Button>}
            </div>
            <Table<TreeRow> className="rl-tree" tableLayout="fixed" size="middle" rowKey={row => row.type === 'gap' ? `gap-${row.parent_id}-${row.first}` : row.id}
              loading={tree.isLoading} columns={columns} dataSource={rows} pagination={pageLocally(50)} scroll={{ x: 840 }}
              rowClassName={row => row.type === 'gap' ? 'rl-gap' : ''}
              locale={{ emptyText: level || status ? '没有符合条件的网段' : '还没有网段' }} />
            <p className="rl-note">使用率：最末一级的网段按已分配、保留和网关地址计算；上层的块显示已划分出去的比例。主机的 IP 只能从它所属业务系统（子系统）的网段里分配。</p>
          </> : !resources.isLoading && <div className="rl-empty">
            还没有地址资源。{canEdit && <Button type="link" onClick={() => setAdding(true)}>新增地址资源</Button>}
          </div>}
      </section>
    </div>
    <ResourceModal open={adding} spaces={spaces} onClose={() => setAdding(false)} onSaved={id => { setAdding(false); refresh(); open(id) }} />
    <CarveModal open={Boolean(carving)} parentId={carving?.parentId} within={carving?.within} overview={overview.data}
      onClose={() => setCarving(null)} onSaved={() => { setCarving(null); refresh() }} />
    <BlockModal open={Boolean(editing)} prefix={editing} overview={overview.data} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh() }} />
    <LevelsModal open={levelsOpen} levels={levels} onClose={() => setLevelsOpen(false)} onSaved={() => { setLevelsOpen(false); refresh() }} />
    <SpacesModal open={spacesOpen} spaces={spaces} onClose={() => setSpacesOpen(false)} onSaved={refresh} />
  </Layout>
}

function ResourceButton({ item, current, onClick }: { item: Resource; current: boolean; onClick: () => void }) {
  const hot = (item.planned ?? 0) >= 80
  return <button type="button" className="rl-space" aria-current={current} onClick={onClick}>
    <span className="rl-space-name">{item.network}{item.planned !== null && <span className={hot ? 'hot' : ''}>{item.planned}%</span>}</span>
    <small>{item.title || item.purpose || '未命名'}</small>
  </button>
}

function SearchResults({ query, several, data, loading, error }: {
  query: string; several: boolean; data?: { type: string; results: SearchResult[] }; loading: boolean; error: unknown
}) {
  if (error) return <Alert type="error" showIcon message={errorText(error)} />
  if (loading || !data) return <div className="rl-empty">正在搜索…</div>
  if (!data.results.length) return <div className="rl-empty">没有找到“{query}”。</div>
  return <div>
    <div className="rl-scope"><h2>搜索“{query}”</h2><span>共 {data.results.length} 条，跨全部地址资源</span></div>
    <div className="rl-results">{data.results.map((result, index) => <div className="rl-result" key={index}>
      <div>{result.kind === 'circuit' ? <><strong>{result.cid}</strong> 专线<span className="rl-sub">{result.status}</span></>
      : result.kind === 'ip' ? <>
        <strong>{query}</strong> {result.address
          ? `已分配${result.address.host ? `给主机 ${result.address.host.hostname}` : result.address.circuit ? `给专线 ${result.address.circuit.cid} 的 ${result.address.circuit.side} 端`
            : result.address.hostname ? `给 ${result.address.hostname}` : ''}（${result.address.kind}）`
          : result.reservation ? `在保留范围内（${result.reservation.purpose}）` : '未分配'}
        {result.prefix && <span className="rl-sub">属于 {result.prefix.network}，{result.prefix.path.map(step => step.name).join(' › ') || result.prefix.title || '未归属'}</span>}
      </> : result.kind === 'prefix' ? <>
        <strong>{result.network}</strong> {result.title}
        <span className="rl-sub">{[result.level, result.relation && (result.relation === '相同' ? '就是这个网段' : result.relation), several && result.space.name].filter(Boolean).join('，') || result.status}</span>
      </> : <>
        <strong>{result.ip}</strong> {result.host ? `主机 ${result.host.hostname}` : result.hostname || ''}
        <span className="rl-sub">{result.kind}{result.dns_name ? `，${result.dns_name}` : ''}{several ? `，${result.space.name}` : ''}</span>
      </>}</div>
      {result.kind === 'ip' && result.prefix ? <Link to={`${ROOT}/prefixes/${result.prefix.id}`}>查看网段</Link>
        : result.kind === 'prefix' ? <Link to={`${ROOT}/prefixes/${result.id}`}>查看网段</Link>
        : result.kind === 'circuit' ? <Link to={`${ROOT}/circuits/${result.id}`}>查看专线</Link> : <span />}
    </div>)}</div>
  </div>
}

