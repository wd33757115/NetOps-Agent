import React from 'react'
import { Alert, Segmented, Select, Table } from 'antd'
import { useQuery } from 'react-query'
import { Link, useSearchParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { RouteLine } from '../components/Routes'
import { pageLocally } from '../components/Paging'
import { errorText, ledger, type ElementKind, type ImpactResult } from '../api'

const KINDS: { value: ElementKind; label: string }[] = [
  { value: 'circuit', label: '专线' }, { value: 'provider', label: '运营商' }, { value: 'network', label: '运营商网络' },
  { value: 'pop', label: '局端' }, { value: 'device', label: '设备' }, { value: 'risk', label: '共享风险' },
]
type Row = ImpactResult['sites'][number]

export default function ImpactPage() {
  const [params, setParams] = useSearchParams()
  const kind = (params.get('kind') as ElementKind) || 'risk'
  const id = params.get('id') || undefined
  const page = useQuery(['rl-impact', kind, id], () => ledger.impact(id ? kind : undefined, id), { keepPreviousData: true })
  const options = page.data?.options?.[kind] || []
  const impact = id ? page.data?.impact : undefined
  const counts = impact?.counts || {}

  return <Layout active="impact" crumbs={['资源台账', '故障影响']} title="故障影响"
    subtitle="运营商发来割接通知、或者一处出了故障时，先算一下会断掉什么。">
    {page.error ? <Alert type="error" showIcon message={errorText(page.error)} /> : null}
    <div className="rl-pick-bar">
      <Segmented value={kind} options={KINDS} onChange={value => setParams({ kind: String(value) })} />
      <Select showSearch optionFilterProp="label" style={{ flex: 1, maxWidth: 460 }} placeholder={`选择${KINDS.find(k => k.value === kind)?.label}`}
        value={id} onChange={value => setParams({ kind, id: value })}
        options={options.map(o => ({ value: o.id, label: `${o.name}（${o.circuits} 条在用专线）` }))}
        notFoundContent={<span className="rl-muted">没有带在用专线的{KINDS.find(k => k.value === kind)?.label}</span>} />
    </div>
    {!impact ? <div className="rl-empty">选一个对象，看它出故障时会影响哪些站点。只有在用的专线参与计算。</div> : <>
      <div className="rl-summary-strip static">
        <div><strong>{impact.circuits.length}</strong><span>条专线受影响</span></div>
        <div className="bad"><strong>{counts.down || 0}</strong><span>个站点会断开</span></div>
        <div className="warn"><strong>{counts.single || 0}</strong><span>个站点只剩一条路</span></div>
        <div className="ok"><strong>{counts.fine || 0}</strong><span>个站点不受影响</span></div>
      </div>
      <div className="rl-impact">
        <Table<Row> size="middle" rowKey={row => row.site.id} dataSource={impact.sites} pagination={pageLocally()}
          locale={{ emptyText: <div className="rl-empty">没有站点会断开或只剩一条路。</div> }}
          columns={[
            { title: '站点', key: 'site', width: 160, render: (_, row) => <div className="rl-owner"><strong>{row.site.name}</strong>
              <small>{row.site.core ? '核心站点' : row.site.kind}</small></div> },
            { title: '故障前的路径（划线的是受影响的专线）', key: 'paths', render: (_, row) => row.paths.map((path, index) => <RouteLine key={index} hops={path} />) },
            { title: '影响', key: 'state', width: 100, render: (_, row) => row.state === 'down'
              ? <span className="rl-row-state down">断开</span> : <span className="rl-row-state single">只剩一条</span> },
          ]} />
        <div>
          <div className="rl-card"><h4 className="rl-card-title">受影响的专线</h4>
            {impact.circuits.length ? <ul className="rl-list">{impact.circuits.map(c => <li key={c.id}>
              <Link to={`${ROOT}/circuits/${c.id}`} className="rl-strong-link">{c.cid}</Link>
              <small className="rl-sub">{c.provider}{c.support_phone ? ` · 报障 ${c.support_phone}` : ' · 报障电话未登记'}</small></li>)}</ul>
              : <p className="rl-muted">没有在用专线。</p>}</div>
          <div className="rl-card" style={{ marginTop: 14 }}><h4 className="rl-card-title">受影响站点上的业务系统</h4>
            {impact.systems.length ? <ul className="rl-list">{impact.systems.map(s => <li key={s.system}>{s.system}
              <small className="rl-sub">{s.sites.map(x => `${x.name} · ${x.hosts} 台主机`).join('；')}</small></li>)}</ul>
              : <p className="rl-muted">主机台账里没有主机落在受影响的站点。</p>}</div>
        </div>
      </div>
    </>}
    <p className="rl-note">影响只按专线台账计算；设备上实际的路由策略（例如没有配置的备份路由）不在计算范围内。</p>
  </Layout>
}
