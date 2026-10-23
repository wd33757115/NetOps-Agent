import React, { useState } from 'react'
import { Alert, Button, Input, Select, Table } from 'antd'
import { EditOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import Layout from '../Layout'
import { useRefresh } from '../components/Forms'
import { SiteModal } from '../components/CircuitForms'
import { useServerPage } from '../components/Paging'
import { RouteLine } from '../components/Routes'
import { SITE_KINDS, errorText, ledger, type Site, type SiteReport, type SiteState } from '../api'

// What the ledger shows about a site, most pressing first. These are facts, not a pass or fail.
const STATES: { key: SiteState; title: string; tone?: string }[] = [
  { key: 'cut', title: '到不了核心站点', tone: 'bad' }, { key: 'single', title: '有单点', tone: 'warn' },
  { key: 'clear', title: '没有发现单点' }, { key: 'idle', title: '没有在用专线' }, { key: 'none', title: '没有检查目标' },
]

export default function ResiliencePage() {
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<string>()
  const [state, setState] = useState<SiteState>()
  const paging = useServerPage(20)
  const report = useQuery(['rl-resilience', query, kind, state, paging.offset, paging.limit],
    () => ledger.resilience({ q: query || undefined, kind, state, offset: paging.offset, limit: paging.limit }), { keepPreviousData: true })
  const [editing, setEditing] = useState<Site | null>(null)
  const canEdit = Boolean(report.data?.can_edit)
  const counts = report.data?.counts || {}
  const core = report.data?.core || []
  const edit = (row: SiteReport) => setEditing((overview.data?.sites || []).find(s => s.id === row.site.id) || null)
  const target = core.length === 1 ? core[0].name : '核心站点'
  const pick = (next?: SiteState) => { setState(next); paging.reset() }

  return <Layout active="resilience" crumbs={['资源台账', '站点韧性']} title="站点韧性"
    subtitle="按在用专线自动算出每个站点到核心站点的路径，再逐一假设一处故障，列出会让它断开的单点。是否可以接受，由你根据业务来判断。"
    actions={<span className="rl-core-note">核心站点：{core.length ? core.map(c => c.name).join('、') : '还没有，请在站点上标注'}</span>}>
    {report.error ? <Alert type="error" showIcon message={errorText(report.error)} /> : null}
    {report.data && !core.length && <Alert type="info" showIcon style={{ marginBottom: 14 }}
      message="先把数据中心等汇聚站点标为核心站点（点击站点右侧的编辑），其他站点才有路径可算。" />}
    <div className="rl-summary-strip">
      <button type="button" className={!state ? 'on' : ''} onClick={() => pick(undefined)}><strong>{report.data?.all ?? 0}</strong><span>站点</span></button>
      {STATES.map(item => <button type="button" key={item.key} className={`${item.tone || ''} ${state === item.key ? 'on' : ''}`}
        onClick={() => pick(state === item.key ? undefined : item.key)}><strong>{counts[item.key] || 0}</strong><span>{item.title}</span></button>)}
    </div>
    <div className="rl-tools">
      <Input.Search allowClear placeholder="搜索站点" style={{ maxWidth: 280 }} onSearch={value => { setQuery(value.trim()); paging.reset() }}
        onChange={event => { if (!event.target.value) { setQuery(''); paging.reset() } }} />
      <Select allowClear placeholder="站点类型" style={{ width: 130 }} value={kind} onChange={value => { setKind(value); paging.reset() }}
        options={SITE_KINDS.map(value => ({ value }))} />
      <Button onClick={() => void report.refetch()}>重新计算</Button>
    </div>
    <Table<SiteReport> size="middle" rowKey={row => row.site.id} loading={report.isLoading} dataSource={report.data?.sites || []}
      pagination={paging.pagination(report.data?.total || 0)} scroll={{ x: 960 }}
      locale={{ emptyText: <div className="rl-empty">{query || kind || state ? '没有符合条件的站点' : '还没有接了专线的站点。'}</div> }}
      columns={[
        { title: '站点', key: 'site', width: 180, render: (_, row) => <div className="rl-owner"><strong>{row.site.name}</strong>
          <small>{[row.site.core ? '核心站点' : row.site.kind, row.site.region].filter(Boolean).join(' · ')}</small></div> },
        { title: `到${target}的路径`, key: 'paths', render: (_, row) => row.paths.length
          ? <>{row.paths.map((path, index) => <RouteLine key={index} hops={path} />)}
            <small className="rl-sub">{row.paths.length} 条路径</small></>
          : <span className="rl-muted">{row.state === 'idle' ? '没有在用专线' : row.note || '没有路径'}</span> },
        { title: '单点', key: 'spof', width: 300, render: (_, row) => row.single_points.length
          ? <div className="rl-spof">{row.single_points.map((item, index) => <span key={index} className="rl-flag warn">
              <b>{item.kind_name}</b>{item.name}</span>)}</div>
          : <span className="rl-muted">{row.state === 'clear' ? '没有发现单点' : '—'}</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 50, render: (_: unknown, row: SiteReport) =>
          <Button type="text" size="small" aria-label={`编辑站点 ${row.site.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => edit(row)} /> }] : []),
      ]} />
    <p className="rl-note">路径由在用专线的两端自动连成：站点 → 专线 → 运营商网络 → 专线 → 站点，只经过“站点间互通”的运营商网络和核心站点。
      “单点”是指它一出故障，这个站点就到不了任何核心站点：运营商、运营商网络、局端、本端设备、单条专线从已录信息自动比对，光缆、管道等共享风险靠手工标注。
      这里只看台账，设备上实际配置的路由不在计算范围内；单点是否需要整改，请结合业务重要性判断。</p>
    <SiteModal open={Boolean(editing)} site={editing} overview={overview.data} onClose={() => setEditing(null)}
      onSaved={() => { setEditing(null); refresh() }} />
  </Layout>
}
