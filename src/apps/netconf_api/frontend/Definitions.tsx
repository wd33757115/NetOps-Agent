import React, { useMemo, useState } from 'react'
import { Alert, Button, Empty, Input, Modal, Select, Table, Tag } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import type { DefinitionSummary } from './api'

export function statusTags(row: Pick<DefinitionSummary, 'published_version' | 'has_draft'>) {
  return <>
    {row.published_version ? <Tag color="blue">已发布 v{row.published_version}</Tag> : <Tag>未发布</Tag>}
    {row.has_draft && <Tag color="gold">有草稿</Tag>}
  </>
}

export const SKELETON = (id: string) => ({
  id,
  description: '',
  vendor: '',
  platforms: [],
  check_item: '',
  namespace: '',
  datastore: 'state',
  module: '',
  path: 'top/Module/Table/Row',
  keys: [],
  values: {},
})

/** Step 1: browse the definitions by vendor, module and check item. */
export default function Definitions({ rows, loading, error, onOpen, onCreate }: {
  rows: DefinitionSummary[]
  loading: boolean
  error: string
  onOpen: (id: string) => void
  onCreate: (id: string, from?: string) => Promise<void>
}) {
  const [vendor, setVendor] = useState<string>()
  const [checkItem, setCheckItem] = useState<string>()
  const [module, setModule] = useState<string>()
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const [newId, setNewId] = useState('')
  const [template, setTemplate] = useState<string>()
  const [createError, setCreateError] = useState('')
  const vendors = useMemo(() => [...new Set(rows.map(row => row.vendor || '').filter(Boolean))].sort(), [rows])
  const items = useMemo(() => [...new Set(rows.map(row => row.check_item || '').filter(Boolean))].sort(), [rows])
  const modules = useMemo(() => [...new Set(rows.map(row => row.module || '').filter(Boolean))].sort(), [rows])
  const shown = rows.filter(row =>
    (!vendor || row.vendor === vendor)
    && (!checkItem || row.check_item === checkItem)
    && (!module || row.module === module)
    && (!search || `${row.id} ${row.description}`.toLowerCase().includes(search.toLowerCase())))
  async function create() {
    const id = newId.trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(id)) { setCreateError('定义 id 只能用字母、数字、点、下划线和连字符，且以字母或数字开头'); return }
    if (rows.some(row => row.id === id)) { setCreateError('已有同名定义'); return }
    setCreateError('')
    try {
      await onCreate(id, template)
      setCreating(false); setNewId(''); setTemplate(undefined)
    } catch (err) {
      setCreateError((err as Error).message)
    }
  }
  return <>
    <div className="nc-filters">
      <Select allowClear placeholder="厂商" aria-label="按厂商筛选" value={vendor} onChange={setVendor} options={vendors.map(v => ({ value: v, label: v }))} style={{ width: 160 }} />
      <Select allowClear showSearch placeholder="模块" aria-label="按模块筛选" value={module} onChange={setModule} options={modules.map(v => ({ value: v, label: v }))} style={{ width: 160 }} />
      <Select allowClear showSearch placeholder="检查项" aria-label="按检查项筛选" value={checkItem} onChange={setCheckItem} options={items.map(v => ({ value: v, label: v }))} style={{ width: 260 }} />
      <Input allowClear prefix={<SearchOutlined aria-hidden="true" />} placeholder="搜索定义 id 或说明" aria-label="搜索定义" value={search} onChange={event => setSearch(event.target.value)} style={{ width: 260 }} />
      <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setCreating(true)} style={{ marginLeft: 'auto' }}>新建定义</Button>
    </div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    <Table<DefinitionSummary>
      rowKey="id"
      loading={loading}
      dataSource={shown}
      pagination={false}
      locale={{ emptyText: <Empty description={rows.length ? '没有符合筛选条件的定义' : '还没有定义'} /> }}
      onRow={row => ({ onClick: () => onOpen(row.id), className: 'nc-clickable' })}
      columns={[
        { title: '定义', dataIndex: 'id', render: (id: string, row) => <div><button type="button" className="nc-link nc-mono" onClick={() => onOpen(id)}>{id}</button>{row.description && <div className="nc-muted">{row.description}</div>}</div> },
        { title: '检查项', dataIndex: 'check_item', render: (value: string | null) => value ? <span className="nc-mono">{value}</span> : <span className="nc-muted">仅供关联</span> },
        { title: '设备与模块', render: (_, row) => <>{`${row.vendor || ''} ${row.platforms.join(', ')}`}{row.module && <div className="nc-muted nc-mono">{row.module}</div>}</> },
        { title: '状态', render: (_, row) => statusTags(row) },
      ]}
    />
    <Modal open={creating} title="新建定义" okText="创建草稿" cancelText="取消" onOk={create} onCancel={() => { setCreating(false); setCreateError('') }} destroyOnClose>
      <p className="nc-muted">新设备型号或新的表：从一条相近的定义复制，或从空白骨架开始。创建后是草稿，试运行通过再发布。</p>
      <label className="nc-field">定义 id<Input value={newId} onChange={event => setNewId(event.target.value)} placeholder="例如 h3c-comware7.vrrp" /></label>
      <div className="nc-field"><span>复制自</span><Select allowClear aria-label="复制自" placeholder="空白骨架" value={template} onChange={setTemplate} options={rows.map(row => ({ value: row.id, label: row.id }))} style={{ width: '100%' }} /></div>
      {createError && <Alert type="error" showIcon message={createError} />}
    </Modal>
  </>
}
