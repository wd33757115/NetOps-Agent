import React, { useEffect, useState } from 'react'
import { Alert, Button, Empty, Input, Space, Table, Tag } from 'antd'
import { PlusOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import { systemApi, systemError, type DirectoryRequest, type Principal } from './api'

function useDirectory(body: DirectoryRequest) {
  const [search, setSearch] = useState(''), [keyword, setKeyword] = useState(''), [page, setPage] = useState(1)
  useEffect(() => { if (search === keyword) return; const timer = setTimeout(() => { setKeyword(search); setPage(1) }, 250); return () => clearTimeout(timer) }, [search, keyword])
  const query = useQuery(['system-directory', body, keyword, page], () => systemApi.directory({ ...body, search: keyword, page, page_size: 10 }), { refetchOnWindowFocus: false })
  useEffect(() => { if (query.data && page > Math.max(1, Math.ceil(query.data.total / 10))) setPage(Math.max(1, Math.ceil(query.data.total / 10))) }, [query.data, page])
  return { query, search, setSearch, page, setPage }
}

export default function PrincipalEditor({ title, kind = 'user', value, onChange, disabled, removeText = '移出', help }: {
  title: string; kind?: 'user' | 'group'; value: number[]; onChange: (ids: number[]) => void; disabled?: boolean; removeText?: string; help?: string
}) {
  const [adding, setAdding] = useState(false), [checked, setChecked] = useState<React.Key[]>([])
  const { query, search, setSearch, page, setPage } = useDirectory({ kind, ...(adding ? { exclude_ids: value } : { ids: value }) })
  const switchMode = (next: boolean) => { setAdding(next); setChecked([]); setSearch(''); setPage(1) }
  const apply = (ids: number[]) => {
    onChange(adding ? Array.from(new Set([...value, ...ids])) : value.filter(id => !ids.includes(id)))
    setChecked([])
  }
  return <section className="system-principals" aria-label={title}>
    <div className="system-principals-heading"><h3>{title} <span>{value.length}</span></h3><Button disabled={disabled} icon={adding ? <ArrowLeftOutlined /> : <PlusOutlined />} onClick={() => switchMode(!adding)}>{adding ? '返回已选列表' : `添加${kind === 'user' ? '用户' : '用户组'}`}</Button></div>
    {help && <p className="system-help">{help}</p>}
    <div className="system-principals-tools"><Input.Search aria-label={`搜索${title}`} placeholder={kind === 'user' ? '搜索用户名或邮箱' : '搜索用户组'} allowClear value={search} onChange={e => setSearch(e.target.value)} />
      <Button disabled={disabled || !checked.length} danger={!adding} onClick={() => apply(checked.map(Number))}>{adding ? '添加所选' : `批量${removeText}`} {checked.length || ''}</Button></div>
    {adding && <p className="system-help">选择需要添加的{kind === 'user' ? '用户' : '用户组'}，支持跨页选择。修改后请保存。</p>}
    {query.isError ? <Alert type="error" message={systemError(query.error)} action={<Button onClick={() => query.refetch()}>重试</Button>} /> :
      <Table<Principal> size="small" rowKey="id" loading={query.isFetching} dataSource={query.data?.items || []} scroll={{ x: 420 }}
        rowSelection={{ selectedRowKeys: checked, onChange: setChecked, preserveSelectedRowKeys: true, getCheckboxProps: row => ({ disabled, 'aria-label': `选择 ${row.name}` }) }}
        pagination={{ current: page, total: query.data?.total || 0, pageSize: 10, showSizeChanger: false, onChange: setPage, showTotal: total => `共 ${total} 项` }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配项' : adding ? '没有可添加的对象' : '暂无已选对象'} /> }}
        columns={[
          { title: kind === 'user' ? '用户' : '用户组', dataIndex: 'name', render: (name, row) => <div><strong>{name}</strong>{row.email && <div className="system-principal-meta">{row.email}</div>}</div> },
          { title: kind === 'user' ? '状态' : '成员', width: 100, render: (_, row) => kind === 'group' ? `${row.member_count} 人` : <Tag color={row.is_active ? 'green' : undefined}>{row.is_active ? '已启用' : '已停用'}</Tag> },
          { title: '操作', width: 85, fixed: 'right', render: (_, row) => <Button type="link" danger={!adding} disabled={disabled} aria-label={`${adding ? '添加' : removeText} ${row.name}`} onClick={() => apply([row.id])}>{adding ? '添加' : removeText}</Button> },
        ]} />}
  </section>
}

export function AccessPreview({ users, groups, available }: { users: number[]; groups: number[]; available: boolean }) {
  const { query, search, setSearch, page, setPage } = useDirectory({ kind: 'user', access: { user_ids: users, group_ids: groups } })
  return <section className="system-principals" aria-label="权限来源预览">
    <div className="system-principals-heading"><h3>权限来源预览</h3><span>{query.data?.total ?? '—'} 人</span></div>
    <p className="system-help">撤销直接授权后，用户仍可能通过用户组获得权限。admin 的全部系统权限不能在这里撤销。</p>
    <Input.Search aria-label="搜索授权来源用户" placeholder="搜索用户名或邮箱" value={search} onChange={e => setSearch(e.target.value)} allowClear />
    {query.isError ? <Alert type="error" message={systemError(query.error)} action={<Button onClick={() => query.refetch()}>重试</Button>} /> :
      <Table<Principal> rowKey="id" size="small" loading={query.isFetching} dataSource={query.data?.items} scroll={{ x: 440 }} pagination={{ current: page, pageSize: 10, total: query.data?.total, showSizeChanger: false, onChange: setPage }} columns={[
        { title: '用户', dataIndex: 'name' },
        { title: '授权来源', dataIndex: 'sources', render: sources => <Space wrap>{sources.map((s: string) => <Tag key={s}>{s}</Tag>)}</Space> },
        { title: '可用状态', render: (_, row) => !row.is_active ? '账号已停用' : available ? '可使用' : '应用未启用' },
      ]} />}
  </section>
}
