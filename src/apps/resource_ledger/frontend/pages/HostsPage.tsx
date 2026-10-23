import React, { useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space as Gap, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link, useSearchParams } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { useRefresh } from '../components/Forms'
import { HostModal } from '../components/InventoryForms'
import { useServerPage } from '../components/Paging'
import { ENVIRONMENTS, HOST_STATES, errorText, ledger, type HostRow } from '../api'

export default function HostsPage() {
  const [params, setParams] = useSearchParams()
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  const paging = useServerPage()
  const [modal, setModal] = useState<{ open: boolean; host?: HostRow | null }>({ open: false })
  const filters = {
    q: params.get('q') || undefined, system_id: params.get('system') || undefined, subsystem_id: params.get('subsystem') || undefined,
    status: params.get('status') || undefined, environment: params.get('environment') || undefined,
  }
  const hosts = useQuery(['rl-hosts', filters, paging.offset, paging.limit], () => ledger.hosts({ ...filters, offset: paging.offset, limit: paging.limit }),
    { keepPreviousData: true })
  const canEdit = Boolean(hosts.data?.can_edit)
  const set = (key: string, value?: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    if (key === 'system') next.delete('subsystem')
    setParams(next)
    paging.reset()
  }
  const remove = (host: HostRow) => Modal.confirm({
    title: `删除主机 ${host.hostname}？`, okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    content: host.ips.length ? `它的地址 ${host.ips.map(ip => ip.ip).join('、')} 会保留在网段里，需要时再到网段里释放。` : undefined,
    onOk: async () => { try { await ledger.remove('hosts', host.id); message.success('主机已删除'); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })
  const subsystems = (overview.data?.subsystems || []).filter(s => s.system_id === filters.system_id)
  const total = hosts.data?.total || 0

  return <Layout active="hosts" crumbs={['资源台账', '主机台账']} title="主机台账"
    subtitle="主机挂在业务系统下，状态和服务端口记在主机上；IP 从所属系统的网段里取，位置随网段带出。"
    actions={canEdit && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setModal({ open: true })}>新增主机</Button>}>
    {hosts.error ? <Alert type="error" showIcon message={errorText(hosts.error)} /> : null}
    <div className="rl-tools">
      <Input.Search allowClear placeholder="搜索主机名、IP、负责人或标签" style={{ maxWidth: 320 }} defaultValue={filters.q} key={filters.q}
        onSearch={value => set('q', value.trim() || undefined)} />
      <Select allowClear showSearch optionFilterProp="label" placeholder="业务系统" style={{ width: 180 }} value={filters.system_id}
        onChange={value => set('system', value)} options={(overview.data?.systems || []).map(s => ({ value: s.id, label: s.name }))} />
      {filters.system_id && subsystems.length > 0 && <Select allowClear placeholder="子系统" style={{ width: 170 }} value={filters.subsystem_id}
        onChange={value => set('subsystem', value)} options={subsystems.map(s => ({ value: s.id, label: s.name }))} />}
      <Select allowClear placeholder="状态" style={{ width: 110 }} value={filters.status} onChange={value => set('status', value)} options={HOST_STATES.map(value => ({ value }))} />
      <Select allowClear placeholder="环境" style={{ width: 100 }} value={filters.environment} onChange={value => set('environment', value)} options={ENVIRONMENTS.map(value => ({ value }))} />
      <span className="rl-muted">共 {total} 台</span>
    </div>
    <Table<HostRow> size="middle" rowKey="id" loading={hosts.isLoading} dataSource={hosts.data?.hosts || []} scroll={{ x: 1100 }}
      pagination={paging.pagination(total)}
      locale={{ emptyText: <div className="rl-empty">{Object.values(filters).some(Boolean) ? '没有符合条件的主机' : '还没有主机。先在业务系统里划好地址块，再新增主机。'}</div> }}
      columns={[
        { title: '主机名', key: 'hostname', width: 190, render: (_, row) => <div className="rl-owner"><strong>{row.hostname}</strong>
          <small>{[row.role, row.kind, row.os].filter(Boolean).join('，')}</small></div> },
        { title: '业务系统', key: 'system', width: 180, render: (_, row) => <div>
          <Link to={`${ROOT}/hosts?system=${row.system.id}`} onClick={paging.reset}>{row.system.name}</Link>
          {row.subsystem && <small className="rl-sub">{row.subsystem.name}</small>}</div> },
        { title: 'IP 地址', key: 'ips', width: 220, render: (_, row) => row.ips.length ? <div className="rl-iplist">{row.ips.map(ip => <div key={ip.id}>
          {ip.prefix ? <Link to={`${ROOT}/prefixes/${ip.prefix.id}`}>{ip.ip}</Link> : ip.ip}
          {ip.zone && <small>{[ip.zone.site, ip.zone.name].filter(Boolean).join(' ')}</small>}
        </div>)}</div> : <span className="rl-muted">没有 IP</span> },
        { title: '服务端口', key: 'services', render: (_, row) => row.services.length
          ? <div className="rl-ports">{row.services.map(s => <span key={s.id} title={s.name || undefined}>{s.text}</span>)}</div>
          : <span className="rl-muted">—</span> },
        { title: '环境', dataIndex: 'environment', width: 64 },
        { title: '状态', dataIndex: 'status', width: 86, render: value => <span className={`rl-host-state h-${value}`}>{value}</span> },
        { title: '负责人', dataIndex: 'owner', width: 90, render: value => value || <span className="rl-muted">—</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 76, render: (_: unknown, row: HostRow) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.hostname}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => setModal({ open: true, host: row })} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.hostname}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove(row)} />
        </Gap> }] : []),
      ]} />
    <HostModal open={modal.open} host={modal.host} overview={overview.data}
      defaults={filters.system_id ? { system_id: filters.system_id, subsystem_id: filters.subsystem_id } : undefined}
      onClose={() => setModal({ open: false })} onSaved={() => { setModal({ open: false }); refresh() }} />
  </Layout>
}
