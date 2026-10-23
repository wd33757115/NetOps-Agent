import { Table } from 'antd'
import { hasSettings, type PolicyPlan } from '../services/firewallPolicy'
import PolicySettings from './PolicySettings'

export default function FirewallBusinessPaths({ plan }: { plan: PolicyPlan }) {
  const requests = new Map(plan.requests.map(request => [request.row, request]))
  const rows = plan.evidence.map(evidence => ({ ...evidence, request: requests.get(evidence.row) }))
  const timed = hasSettings(plan.requests)
  return <section className="fw-business-paths fw-device-detail" aria-label="业务系统与拓扑路径">
    <h2>业务系统与拓扑路径</h2>
    <p className="fw-result-note">按申请行和地址分段展示生成方案。尚未下发设备，拓扑路径不代表实际连通性。</p>
    <Table rowKey="id" dataSource={rows} size="middle" scroll={{ x: timed ? 1040 : 900 }}
      pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }}
      expandable={{ expandedRowRender: row => <div className="fw-evidence">
        <strong>申请表第 {row.row} 行 · 关联设备策略</strong>
        {plan.rules.filter(rule => rule.evidence_ids.includes(row.id)).map(rule => <p key={rule.id}>
          {rule.device} · {rule.source_zone} → {rule.destination_zone} · {rule.protocol.toUpperCase()} {rule.ports.join(', ') || '无需端口'}
        </p>)}
      </div> }}
      columns={[
        { title: '申请行', dataIndex: 'row', width: 72 },
        { title: '源业务系统 / 地址', key: 'source', width: 210, render: (_, row) => <div className="fw-address">
          <span>{row.request?.source_system || '未填写'}</span><small>{row.source_segments.flatMap(s => s.addresses).join('、')}</small>
        </div> },
        { title: '目的业务系统 / 地址', key: 'destination', width: 210, render: (_, row) => <div className="fw-address">
          <span>{row.request?.destination_system || '未填写'}</span><small>{row.destination_segments.flatMap(s => s.addresses).join('、')}</small>
        </div> },
        { title: '服务', key: 'service', width: 115, render: (_, row) => <div className="fw-address">
          <span>{row.request?.proto.toUpperCase()}</span><small>{row.request?.port || '无需端口'}</small>
        </div> },
        ...(timed ? [{ title: '有效期 / 长连接', key: 'settings', width: 196, render: (_: unknown, row: (typeof rows)[number]) => row.request && <PolicySettings item={row.request} /> }] : []),
        { title: '拓扑路径', key: 'path', render: (_, row) => <div className="fw-address">
          <small>{row.path_source === 'explicit' ? '指定路径' : '拓扑推导路径'}</small>
          <span>{row.path.map(hop => hop.join(' / ')).join(' → ')}</span>
        </div> },
      ]} />
  </section>
}
