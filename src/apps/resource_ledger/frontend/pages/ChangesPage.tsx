import React, { useState } from 'react'
import { Alert, Input, Select, Table } from 'antd'
import { useQuery } from 'react-query'
import Layout from '../Layout'
import { useServerPage } from '../components/Paging'
import { errorText, ledger, type ChangeRow } from '../api'

const ENTITIES = ['网段', 'IP 地址', '保留范围', '主机', '业务系统', '子系统', '网络区域', '站点', '地域', '路由域', '规划层级',
  '专线', '运营商', '运营商网络', '局端', '专线类型', '共享风险']

export default function ChangesPage() {
  const [entity, setEntity] = useState<string>()
  const [query, setQuery] = useState('')
  const paging = useServerPage()
  const changes = useQuery(['rl-changes', entity, query, paging.offset, paging.limit],
    () => ledger.changes({ entity, q: query || undefined, offset: paging.offset, limit: paging.limit }), { keepPreviousData: true })

  return <Layout active="changes" crumbs={['资源台账', '变更记录']} title="变更记录" subtitle="台账的每一次新增、修改、分配、释放和导入都记录在这里。">
    <div className="rl-tools">
      <Select allowClear showSearch placeholder="对象" style={{ width: 140 }} value={entity} onChange={value => { setEntity(value); paging.reset() }} options={ENTITIES.map(value => ({ value }))} />
      <Input.Search allowClear placeholder="搜索网段、IP、主机、专线或操作人" style={{ maxWidth: 300 }} onSearch={value => { setQuery(value.trim()); paging.reset() }} />
    </div>
    {changes.error ? <Alert type="error" showIcon message={errorText(changes.error)} /> : null}
    <Table<ChangeRow> size="middle" rowKey="id" loading={changes.isLoading} dataSource={changes.data?.items || []} pagination={paging.pagination(changes.data?.total || 0)}
      locale={{ emptyText: '还没有变更记录' }}
      columns={[
        { title: '时间', dataIndex: 'at', width: 170, render: value => new Date(value).toLocaleString('zh-CN', { hour12: false }) },
        { title: '操作人', dataIndex: 'actor_name', width: 110 },
        { title: '操作', dataIndex: 'action', width: 70 },
        { title: '对象', dataIndex: 'label', width: 230, render: value => <strong style={{ fontWeight: 550 }}>{value}</strong> },
        { title: '内容', key: 'detail', render: (_, row) => row.detail?.length
          ? <ul className="rl-change-detail">{row.detail.map((c, i) => <li key={i}>{c.field}：{c.before === null || c.before === undefined ? '' : <><del>{String(c.before)}</del> → </>}{c.after === null || c.after === undefined ? '（清空）' : String(c.after)}</li>)}</ul>
          : <span className="rl-muted">—</span> },
      ]} />
  </Layout>
}
