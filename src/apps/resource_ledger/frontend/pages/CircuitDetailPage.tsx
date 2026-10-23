import React, { useState } from 'react'
import { Alert, Button, Modal, message } from 'antd'
import { CopyOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { CircuitDrawer } from '../components/CircuitForms'
import { CIRCUIT_STATE_CLASS } from '../components/Routes'
import { errorText, ledger, type CircuitDetail, type CircuitEnd } from '../api'

const mbps = (value: number | null) => value === null ? '' : value >= 1000 && value % 1000 === 0 ? `${value / 1000} Gbps` : `${value} Mbps`

/** What to read out on the phone to the provider: one block, copied in one go. */
function ticketText(c: CircuitDetail) {
  const end = (label: string, e: CircuitEnd | null) => e?.place
    ? `${label} ${e.place.name}${e.device ? ` ${e.device}` : ''}${e.interface ? ` ${e.interface}` : ''}${e.pop ? `，局端 ${e.pop.name}` : ''}` : null
  const lines = [
    [c.provider?.name, c.support.support_phone && `报障 ${c.support.support_phone}`, c.support.account && `客户编号 ${c.support.account}`].filter(Boolean).join(' · '),
    [`电路编号 ${c.cid}`, end('A 端', c.a), end('Z 端', c.z)].filter(Boolean).join(' · '),
    [c.a?.ip && c.z?.ip ? `互联 ${c.a.ip} ↔ ${c.z.ip}` : c.interconnect && `互联网段 ${c.interconnect.network}`,
      c.routing && `${c.routing}${c.local_as || c.remote_as ? ` ${c.local_as || '?'} ↔ ${c.remote_as || '?'}` : ''}`, c.vlan && `VLAN ${c.vlan}`]
      .filter(Boolean).join(' · '),
  ]
  return lines.filter(Boolean).join('\n')
}

function End({ side, end }: { side: 'A' | 'Z'; end: CircuitEnd | null }) {
  if (!end?.place) return <div className="rl-end empty"><div className="rl-end-label"><span>{side} 端</span></div><h3>只有一端</h3>
    <p className="rl-muted">例如互联网专线不关心运营商那一侧。</p></div>
  return <div className="rl-end">
    <div className="rl-end-label"><span>{side} 端</span><span>{end.place.kind === 'site' ? '站点' : '运营商网络'}</span></div>
    <h3>{end.place.name}</h3><div className="rl-end-where">{end.place.detail || ' '}</div>
    <dl className="rl-kv">
      {end.device && <><dt>设备接口</dt><dd>{end.device}{end.interface ? ` ${end.interface}` : ''}</dd></>}
      {end.port_speed && <><dt>端口速率</dt><dd>{mbps(end.port_speed)}</dd></>}
      {end.xconnect && <><dt>配线</dt><dd>{end.xconnect}</dd></>}
      {end.pop && <><dt>接入局端</dt><dd>{end.pop.name}</dd></>}
      {end.ip && <><dt>地址</dt><dd className="rl-mono">{end.ip}{!end.allocated && <span className="rl-muted" style={{ fontFamily: 'inherit' }}>（未在网段规划中分配）</span>}</dd></>}
      {!end.device && !end.port_speed && !end.xconnect && !end.pop && !end.ip && <><dt>&nbsp;</dt><dd className="rl-muted">没有登记设备和局端</dd></>}
    </dl>
  </div>
}

export default function CircuitDetailPage() {
  const { circuitId = '' } = useParams()
  const navigate = useNavigate()
  const refresh = useRefresh()
  const detail = useQuery(['rl-circuit', circuitId], () => ledger.circuit(circuitId))
  const [editing, setEditing] = useState(false)
  const c = detail.data
  const crumbs = [<Link key="list" to={`${ROOT}/circuits`}>专线</Link>, c?.cid || '']
  if (detail.error) return <Layout active="circuits" crumbs={crumbs} title="专线"><Alert type="error" showIcon message={errorText(detail.error)} /></Layout>
  if (!c) return <Layout active="circuits" crumbs={crumbs} title="专线"><div className="rl-empty">正在读取…</div></Layout>
  const copy = async () => {
    try { await navigator.clipboard.writeText(ticketText(c)); message.success('报障信息已复制') } catch { message.error('无法访问剪贴板，请手动选择文字复制') }
  }
  const remove = () => Modal.confirm({
    title: `删除专线 ${c.cid}？`, content: '两端的互联地址会释放，互联网段转为待回收。只是停用时，建议把状态改为“已拆除”保留记录。',
    okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.remove('circuits', c.id); message.success('专线已删除'); refresh(); navigate(`${ROOT}/circuits`) } catch (error) { message.error(errorText(error)); throw error } },
  })
  const states = { down: ['rl-row-state down', '断开'], single: ['rl-row-state single', '只剩一条'] } as const
  return <Layout active="circuits" crumbs={crumbs} title={c.cid}
    subtitle={<span>{[c.provider?.name, c.type?.name, c.bandwidth !== null && `承诺 ${mbps(c.bandwidth)}`, c.install_date && `开通于 ${c.install_date}`].filter(Boolean).join(' · ')}
      {' '}<span className={`rl-state ${CIRCUIT_STATE_CLASS[c.status] || ''}`}>{c.status}</span></span>}
    actions={c.can_edit && <>
      <Button icon={<EditOutlined aria-hidden="true" />} onClick={() => setEditing(true)}>编辑</Button>
      <Button danger icon={<DeleteOutlined aria-hidden="true" />} onClick={remove}>删除</Button>
    </>}>
    <div className="rl-ticket"><div><h4>报障信息</h4><pre>{ticketText(c) || '运营商还没有登记报障电话'}</pre></div>
      <Button type="primary" icon={<CopyOutlined aria-hidden="true" />} onClick={() => void copy()}>复制报障信息</Button></div>
    <div className="rl-span">
      <End side="A" end={c.a} />
      <div className="rl-wire"><div><strong>{mbps(c.bandwidth) || '带宽未填'}</strong><span>{[c.type?.name, c.vlan && `VLAN ${c.vlan}`].filter(Boolean).join(' · ')}</span></div></div>
      <End side="Z" end={c.z} />
    </div>
    {c.description && <p className="rl-note" style={{ marginTop: -8 }}>说明：{c.description}</p>}
    <div className="rl-grid2">
      <div className="rl-card"><h4 className="rl-card-title">这条线断了会怎样{c.impact && <Link to={`${ROOT}/impact?kind=circuit&id=${c.id}`}>在故障影响中查看</Link>}</h4>
        {c.status !== '在用' ? <p className="rl-muted">只有“在用”的专线参与计算；这条线现在是“{c.status}”。</p>
          : c.impact && c.impact.sites.length ? <ul className="rl-list">{c.impact.sites.map(row => <li key={row.site.id}>
              <span className={states[row.state][0]}>{states[row.state][1]}</span> {row.site.name}</li>)}
              <li><span className="rl-row-state fine">不受影响</span> 其余 {c.impact.counts.fine || 0} 个站点</li></ul>
            : <p className="rl-muted">没有站点会因为这条线断开或只剩一条路。</p>}
      </div>
      <div className="rl-card"><h4 className="rl-card-title">故障域</h4>
        {c.domains.length ? <dl className="rl-domains">{c.domains.map((d, i) => <React.Fragment key={i}>
          <dt>{d.kind_name}</dt><dd>{d.name}{d.auto && <span className="rl-auto">自动比对</span>}
            {d.shared_with.length > 0 && <span className="rl-flag warn" title={d.shared_with.join('、')}>另有 {d.shared_with.length} 条在用专线：{d.shared_with.slice(0, 3).join('、')}{d.shared_with.length > 3 ? ' 等' : ''}</span>}</dd>
        </React.Fragment>)}</dl> : <p className="rl-muted">只有“在用”的专线参与比对。</p>}
        <dl className="rl-domains" style={{ marginTop: 10 }}>
          <dt>互联网段</dt><dd>{c.interconnect ? <Link to={`${ROOT}/prefixes/${c.interconnect.id}`} className="rl-mono">{c.interconnect.network}</Link> : <span className="rl-muted">未登记</span>}</dd>
          <dt>路由</dt><dd>{c.routing ? `${c.routing}${c.local_as || c.remote_as ? ` · ${c.local_as || '?'} ↔ ${c.remote_as || '?'}` : ''}` : <span className="rl-muted">未登记</span>}</dd>
          {c.support.contact && <><dt>客户经理</dt><dd>{c.support.contact}</dd></>}
          {c.note && <><dt>备注</dt><dd>{c.note}</dd></>}
        </dl>
      </div>
    </div>
    <p className="rl-note">最后修改：{c.updated_by}，{new Date(c.updated_at).toLocaleString('zh-CN', { hour12: false })}。</p>
    <CircuitDrawer open={editing} circuit={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh() }} />
  </Layout>
}
