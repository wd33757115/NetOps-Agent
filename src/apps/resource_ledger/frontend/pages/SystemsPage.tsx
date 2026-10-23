import React, { useMemo, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space as Gap, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { SubsystemModal, SystemModal } from '../components/InventoryForms'
import { pageLocally } from '../components/Paging'
import { IMPORTANCE, blockSize, errorText, ipValue, ledger, type SystemBlock, type SystemRow } from '../api'

type SubRow = SystemRow['subsystems'][number]

export default function SystemsPage() {
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  const systems = useQuery('rl-systems', ledger.systems)
  const [filter, setFilter] = useState('')
  const [importance, setImportance] = useState<string>()
  const [systemModal, setSystemModal] = useState<{ open: boolean; system?: SystemRow | null }>({ open: false })
  const [subModal, setSubModal] = useState<{ open: boolean; systemId?: string; subsystem?: SubRow | null }>({ open: false })
  const canEdit = Boolean(systems.data?.can_edit)
  const rows = useMemo(() => (systems.data?.systems || []).filter(system =>
    (!importance || system.importance === importance)
    && (!filter || [system.name, system.short_name, system.department, system.business_owner, system.ops_owner, ...system.subsystems.map(s => s.name)]
      .filter(Boolean).join(' ').toLowerCase().includes(filter.toLowerCase()))), [systems.data, filter, importance])

  const remove = (kind: 'systems' | 'subsystems', id: string, name: string) => Modal.confirm({
    title: `删除${kind === 'systems' ? '业务系统' : '子系统'} ${name}？`, content: '还有主机或网段归属它时不能删除。',
    okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.remove(kind, id); message.success('已删除'); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })

  return <Layout active="systems" crumbs={['资源台账', '业务系统']} title="业务系统"
    subtitle="地址按业务划分：每个系统、子系统在网段规划里有自己的地址块，主机只从这些块里取 IP。"
    actions={canEdit && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setSystemModal({ open: true })}>新增业务系统</Button>}>
    {systems.error ? <Alert type="error" showIcon message={errorText(systems.error)} /> : null}
    <div className="rl-tools">
      <Input.Search allowClear placeholder="搜索系统、子系统、部门或负责人" style={{ maxWidth: 360 }} value={filter} onChange={event => setFilter(event.target.value)} />
      <Select allowClear placeholder="重要性" style={{ width: 110 }} value={importance} onChange={setImportance} options={IMPORTANCE.map(value => ({ value }))} />
    </div>
    <Table<SystemRow> size="middle" rowKey="id" loading={systems.isLoading} dataSource={rows} pagination={pageLocally()} scroll={{ x: 1000 }}
      expandable={{ expandedRowRender: system => <Subsystems system={system} canEdit={canEdit}
        onAdd={() => setSubModal({ open: true, systemId: system.id })}
        onEdit={sub => setSubModal({ open: true, systemId: system.id, subsystem: sub })}
        onRemove={sub => remove('subsystems', sub.id, sub.name)} />,
        rowExpandable: () => true }}
      locale={{ emptyText: <div className="rl-empty">还没有业务系统。可以在这里新增，也可以在网段规划里划分时直接新建。</div> }}
      columns={[
        { title: '业务系统', key: 'name', width: 220, render: (_, row) => <div className="rl-owner"><strong>{row.name}</strong>
          <small>{[row.short_name, row.department].filter(Boolean).join('，') || `${row.subsystems.length} 个子系统`}</small></div> },
        { title: '重要性', dataIndex: 'importance', width: 84, render: value => <span className={`rl-importance i-${value}`}>{value}</span> },
        { title: '负责人', key: 'owners', width: 170, render: (_, row) => row.business_owner || row.ops_owner
          ? <div>{row.business_owner || '—'}<small className="rl-sub">运维 {row.ops_owner || '—'}</small></div> : <span className="rl-muted">未填写</span> },
        { title: '地址块', key: 'blocks', render: (_, row) => <Blocks blocks={outermost([...row.blocks, ...row.subsystems.flatMap(s => s.blocks)])} /> },
        { title: '主机', key: 'hosts', width: 150, render: (_, row) => <HostCount counts={row.hosts} systemId={row.id} /> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 96, render: (_: unknown, row: SystemRow) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => setSystemModal({ open: true, system: row })} />
          <Button type="text" size="small" aria-label={`为 ${row.name} 新增子系统`} icon={<PlusOutlined aria-hidden="true" />} onClick={() => setSubModal({ open: true, systemId: row.id })} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.name}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove('systems', row.id, row.name)} />
        </Gap> }] : []),
      ]} />
    <p className="rl-note">地址块来自网段规划：划到“业务系统”或“子系统”层级的网段会列在这里，位置按它上面的地域、机房、网络区域带出。</p>
    <SystemModal open={systemModal.open} system={systemModal.system} onClose={() => setSystemModal({ open: false })}
      onSaved={() => { setSystemModal({ open: false }); refresh() }} />
    <SubsystemModal open={subModal.open} systemId={subModal.systemId} subsystem={subModal.subsystem} overview={overview.data}
      onClose={() => setSubModal({ open: false })} onSaved={() => { setSubModal({ open: false }); refresh() }} />
  </Layout>
}

/** The blocks not inside another of the list: a system row need not repeat its subsystems' blocks. */
function outermost(blocks: SystemBlock[]) {
  const range = (block: SystemBlock) => {
    const [ip, length] = block.network.split('/')
    return [ipValue(ip), ipValue(ip) + blockSize(Number(length)) - 1]
  }
  return blocks.filter(block => {
    const [first, last] = range(block)
    return !blocks.some(other => {
      if (other === block || other.space !== block.space) return false
      const [a, b] = range(other)
      return a <= first && last <= b && (a !== first || b !== last)
    })
  })
}

function Blocks({ blocks }: { blocks: SystemBlock[] }) {
  if (!blocks.length) return <span className="rl-muted">还没有地址块，去网段规划里划分</span>
  return <div className="rl-blocklist">{blocks.slice(0, 4).map(block => <div key={block.id}>
    <Link to={`${ROOT}/prefixes/${block.id}`}>{block.network}</Link>
    <small>{block.where.join(' ') || '未划到机房'}{block.usage !== null ? `，已用 ${block.usage}%` : ''}</small>
  </div>)}{blocks.length > 4 && <small className="rl-muted">另有 {blocks.length - 4} 块</small>}</div>
}

function HostCount({ counts, systemId, subsystemId }: { counts: Record<string, number>; systemId: string; subsystemId?: string }) {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0)
  if (!total) return <span className="rl-muted">没有主机</span>
  const query = `system=${systemId}${subsystemId ? `&subsystem=${subsystemId}` : ''}`
  return <div><Link to={`${ROOT}/hosts?${query}`}>{total} 台</Link>
    <small className="rl-sub">{Object.entries(counts).map(([status, n]) => `${status} ${n}`).join('，')}</small></div>
}

function Subsystems({ system, canEdit, onAdd, onEdit, onRemove }: {
  system: SystemRow; canEdit: boolean; onAdd: () => void; onEdit: (sub: SubRow) => void; onRemove: (sub: SubRow) => void
}) {
  return <div className="rl-subsystems">
    {system.subsystems.length ? <table>
      <thead><tr><th>子系统</th><th>地址块</th><th>主机</th>{canEdit && <th />}</tr></thead>
      <tbody>{system.subsystems.map(sub => <tr key={sub.id}>
        <td><div className="rl-owner"><strong>{sub.name}</strong>{(sub.owner || sub.note) && <small>{[sub.owner, sub.note].filter(Boolean).join('，')}</small>}</div></td>
        <td><Blocks blocks={sub.blocks} /></td>
        <td><HostCount counts={sub.hosts} systemId={system.id} subsystemId={sub.id} /></td>
        {canEdit && <td><Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${sub.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => onEdit(sub)} />
          <Button type="text" size="small" danger aria-label={`删除 ${sub.name}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => onRemove(sub)} />
        </Gap></td>}
      </tr>)}</tbody>
    </table> : <p className="rl-note" style={{ margin: 0 }}>没有子系统；主机可以直接挂在业务系统下。</p>}
    {canEdit && <Button type="link" icon={<PlusOutlined aria-hidden="true" />} onClick={onAdd} style={{ paddingLeft: 0 }}>新增子系统</Button>}
  </div>
}
