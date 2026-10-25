import React, { useState } from 'react'
import { Alert, Button, Drawer, Form, Input, Modal, Space as Gap, Table, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { Link } from 'react-router-dom'
import Layout, { ROOT } from '../Layout'
import { ZoneModal, useRefresh } from '../components/Forms'
import { SiteModal } from '../components/CircuitForms'
import { pageLocally } from '../components/Paging'
import { errorText, ledger, type Overview, type Region, type Site, type ZonePageRow } from '../api'

export default function ZonesPage() {
  const refresh = useRefresh()
  const overview = useQuery('rl-overview', ledger.overview)
  // The routing domain only means something when there is more than one.
  const several = (overview.data?.spaces.length || 0) > 1
  const zones = useQuery('rl-zones', ledger.zones)
  const [zoneModal, setZoneModal] = useState<{ open: boolean; zone?: ZonePageRow | null }>({ open: false })
  const [locations, setLocations] = useState(false)
  const canEdit = Boolean(zones.data?.can_edit)

  const remove = (zone: ZonePageRow) => Modal.confirm({
    title: `删除网络区域 ${zone.name}？`, content: '还有网段归属这个区域时不能删除。', okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await ledger.remove('zones', zone.id); message.success('网络区域已删除'); refresh() } catch (error) { message.error(errorText(error)); throw error } },
  })

  return <Layout active="zones" crumbs={['资源台账', '网络区域']} title="网络区域"
    subtitle="区域的网段来自网段规划里划到“网络区域”层级的块。"
    actions={<>
      <Button onClick={() => setLocations(true)}>地域与站点</Button>
      {canEdit && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setZoneModal({ open: true })}>新增网络区域</Button>}
    </>}>
    {zones.error ? <Alert type="error" showIcon message={errorText(zones.error)} /> : null}
    <Table<ZonePageRow> size="middle" rowKey="id" loading={zones.isLoading} dataSource={zones.data?.zones || []} pagination={pageLocally()} scroll={{ x: 900 }}
      locale={{ emptyText: <div className="rl-empty">还没有网络区域。先在“地域与站点”里建好站点，再新增区域；也可以用 Excel 导入时一并建立。</div> }}
      columns={[
        { title: '地域 / 机房', key: 'where', width: 160, render: (_, row) => <div>{row.where?.site}<small className="rl-sub">{row.where?.region}</small></div> },
        { title: '网络区域', key: 'name', width: 170, render: (_, row) => <div><strong style={{ fontWeight: 550 }}>{row.name}</strong>{row.title && <small className="rl-sub">{row.title}</small>}</div> },
        { title: '类型', dataIndex: 'kind', width: 72 },
        { title: '网段（划到这个区域的块）', key: 'prefixes', render: (_, row) => row.prefixes.length
          ? <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>{row.prefixes.map(p => <Link key={p.id} to={`${ROOT}/prefixes/${p.id}`}>{p.network}{several && <span className="rl-muted" style={{ fontSize: 12 }}> {p.space}</span>}</Link>)}</div>
          : <span className="rl-muted">还没有网段划到这个区域</span> },
        { title: '安全等级', dataIndex: 'security_level', width: 90, render: value => value || <span className="rl-muted">—</span> },
        { title: '末级网段', dataIndex: 'subnets', width: 86, render: value => `${value} 个` },
        { title: '使用率', dataIndex: 'usage', width: 76, render: value => value === null ? <span className="rl-muted">—</span> : <span style={{ color: value >= 80 ? 'var(--rl-warn)' : undefined }}>{value}%</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 88, render: (_: unknown, row: ZonePageRow) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => setZoneModal({ open: true, zone: row })} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.name}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove(row)} />
        </Gap> }] : []),
      ]} />
    <p className="rl-note">在网段规划里把一块地址划到某个网络区域后，这里自动列出；它下面的业务系统、子系统网段都算在这个区域里。</p>
    <ZoneModal open={zoneModal.open} overview={overview.data} zone={zoneModal.zone} onClose={() => setZoneModal({ open: false })}
      onSaved={() => { setZoneModal({ open: false }); refresh() }} />
    <Locations open={locations} overview={overview.data} canEdit={canEdit} onClose={() => setLocations(false)} onChanged={refresh} />
  </Layout>
}

function Locations({ open, overview, canEdit, onClose, onChanged }: { open: boolean; overview?: Overview; canEdit: boolean; onClose: () => void; onChanged: () => void }) {
  const [region] = Form.useForm<Region>()
  const [editing, setEditing] = useState<{ region?: Region }>({})
  const [siteModal, setSiteModal] = useState<{ open: boolean; site?: Site | null }>({ open: false })
  const regions = overview?.regions || []
  const sites = overview?.sites || []
  const act = async (work: () => Promise<unknown>, done: string, reset?: () => void) => {
    try { await work(); message.success(done); reset?.(); onChanged() } catch (error) { message.error(errorText(error)) }
  }
  const ask = (title: string, content: string, work: () => Promise<unknown>, done: string) => Modal.confirm({
    title, content, okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => { try { await work(); message.success(done); onChanged() } catch (error) { message.error(errorText(error)); throw error } },
  })
  const editRegion = (row?: Region) => { setEditing(e => ({ ...e, region: row })); region.setFieldsValue(row || { name: '', note: '' } as Region) }
  return <Drawer open={open} onClose={onClose} title="地域与站点" width={820} destroyOnClose>
    <h3 style={{ marginTop: 0 }}>地域</h3>
    <Table<Region> size="small" rowKey="id" dataSource={regions} pagination={pageLocally(10)} locale={{ emptyText: '还没有地域' }}
      columns={[{ title: '名称', dataIndex: 'name' }, { title: '站点', key: 'sites', render: (_, row) => sites.filter(s => s.region_id === row.id).map(s => s.name).join('、') || <span className="rl-muted">—</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 90, render: (_: unknown, row: Region) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => editRegion(row)} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.name}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => ask(`删除地域 ${row.name}？`, '地域下还有站点、或有网段划给它时不能删除。', () => ledger.remove('regions', row.id), '地域已删除')} />
        </Gap> }] : [])]} />
    {canEdit && <Form form={region} layout="inline" style={{ margin: '12px 0 28px' }}
      onFinish={values => act(() => ledger.save('regions', values, editing.region?.id), editing.region ? '地域已保存' : '地域已新增', () => editRegion())}>
      <Form.Item name="name" rules={[{ required: true, message: '请填写地域名称' }]}><Input placeholder="地域名称，如 西安" maxLength={64} /></Form.Item>
      <Button htmlType="submit" type="primary">{editing.region ? '保存地域' : '新增地域'}</Button>
      {editing.region && <Button type="link" onClick={() => editRegion()}>取消编辑</Button>}
    </Form>}
    <h3 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>站点
      {canEdit && <Button type="link" onClick={() => setSiteModal({ open: true })}>新增站点</Button>}</h3>
    <Table<Site> size="small" rowKey="id" dataSource={sites} pagination={pageLocally(10)} locale={{ emptyText: '还没有站点' }}
      columns={[{ title: '名称', dataIndex: 'name', render: (value, row) => <span>{value}{row.core && <span className="rl-flag ok" style={{ marginLeft: 6 }}>核心</span>}</span> },
        { title: '地域', key: 'region', width: 90, render: (_, row) => regions.find(r => r.id === row.region_id)?.name },
        { title: '类型', dataIndex: 'kind', width: 70 },
        { title: '地址', dataIndex: 'address', render: value => value || <span className="rl-muted">—</span> },
        ...(canEdit ? [{ title: '', key: 'ops', width: 90, render: (_: unknown, row: Site) => <Gap size={0}>
          <Button type="text" size="small" aria-label={`编辑 ${row.name}`} icon={<EditOutlined aria-hidden="true" />} onClick={() => setSiteModal({ open: true, site: row })} />
          <Button type="text" size="small" danger aria-label={`删除 ${row.name}`} icon={<DeleteOutlined aria-hidden="true" />} onClick={() => ask(`删除站点 ${row.name}？`, '站点下还有网络区域、有网段划给它、或有专线接在它上面时不能删除。', () => ledger.remove('sites', row.id), '站点已删除')} />
        </Gap> }] : [])]} />
    <p className="rl-note">核心站点（如数据中心）是“站点韧性”的检查目标；分支、门店、合作方等也在这里建，作为专线的端点。</p>
    <SiteModal open={siteModal.open} site={siteModal.site} overview={overview} onClose={() => setSiteModal({ open: false })}
      onSaved={() => { setSiteModal({ open: false }); onChanged() }} />
  </Drawer>
}
