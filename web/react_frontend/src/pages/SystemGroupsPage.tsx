import React, { useState } from 'react'
import { Alert, Button, Drawer, Empty, Form, Input, Modal, Space, Table, Tag, message } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import { Link } from 'react-router-dom'
import SystemLayout from '../components/system/SystemLayout'
import PrincipalEditor from '../components/system/PrincipalEditor'
import { systemApi, systemError, type GroupRecord } from '../components/system/api'
import { useAuth } from '../context/AuthContext'

export default function SystemGroupsPage() {
  const query = useQuery(['system-groups'], systemApi.groups, { refetchOnWindowFocus: false })
  const cache = useQueryClient(), { refreshProfile } = useAuth()
  const [search, setSearch] = useState(''), [editing, setEditing] = useState<GroupRecord | null | undefined>()
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [deleting, setDeleting] = useState<GroupRecord | null>(null)
  const [form] = Form.useForm()
  const [members, setMembers] = useState<number[]>([])
  const refresh = async () => { await query.refetch(); await cache.invalidateQueries(['system-access']); await refreshProfile() }
  const open = (group: GroupRecord | null) => { setEditing(group); setMembers(group?.member_ids || []); setError(''); form.setFieldsValue({ name: group?.name || '', member_ids: group?.member_ids || [] }) }
  const save = async () => {
    try {
      const values = await form.validateFields(); setBusy(true); setError('')
      await systemApi.saveGroup({ ...values, member_ids: members, revision: editing?.revision }, editing?.id)
      setEditing(undefined); message.success('用户组已保存'); await refresh()
    } catch (e) { if (!(e as { errorFields?: unknown }).errorFields) setError(systemError(e)) } finally { setBusy(false) }
  }
  return <SystemLayout title="用户组" subtitle="按业务组织成员，通过用户组统一分配应用使用权。" toolbar={<>
    <Input.Search aria-label="搜索用户组" placeholder="搜索用户组" value={search} onChange={e => setSearch(e.target.value)} allowClear />
    <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>
    <Button type="primary" icon={<PlusOutlined />} disabled={busy} onClick={() => open(null)}>新建用户组</Button>
  </>}>
    {query.isError ? <Alert type="error" showIcon message="用户组加载失败" description={systemError(query.error)} action={<Button onClick={() => query.refetch()}>重试</Button>} /> :
      <Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items.filter(g => g.name.toLowerCase().includes(search.toLowerCase()))} scroll={{ x: 700 }} pagination={{ pageSize: 10, showSizeChanger: true }}
        locale={{ emptyText: <Empty description="没有匹配的用户组" /> }} columns={[
          { title: '用户组', dataIndex: 'name', render: (name, row) => <Space><strong>{name}</strong>{row.protected && <Tag>系统组</Tag>}</Space> },
          { title: '成员', render: (_, row) => `${row.member_ids.length} 人` },
          { title: '应用授权', render: (_, row) => row.app_ids.length ? <Link to="/system/apps">{row.app_ids.length} 个应用</Link> : '未授权应用' },
          { title: '操作', width: 210, fixed: 'right', render: (_, row) => <Space><Button type="link" disabled={busy} onClick={() => open(row)}>编辑成员</Button><Button type="link" danger disabled={busy || row.protected} onClick={() => { setError(''); setDeleting(row) }}>删除</Button></Space> },
        ]} />}
    <Drawer title={editing ? `用户组 · ${editing.name}` : '新建用户组'} width={860} open={editing !== undefined} onClose={() => { if (!busy) setEditing(undefined) }} maskClosable={!busy} keyboard={!busy} closable={!busy}
      footer={<Space><Button onClick={() => setEditing(undefined)} disabled={busy}>取消</Button><Button type="primary" loading={busy} onClick={save}>保存用户组</Button></Space>}>
      {editing?.protected && <Alert type="info" showIcon message={editing.name === 'admin' ? 'admin · 全部系统权限' : 'netops-config · 应用授权管理'} description="默认组不能删除或改名；可以添加、移出成员。至少保留一名启用的 admin 管理员。" style={{ marginBottom: 20 }} />}
      {error && <Alert type="error" showIcon message={error} description="编辑内容已保留。若出现冲突，请关闭后刷新列表，再重新编辑。" style={{ marginBottom: 20 }} />}
      <Form form={form} layout="vertical" disabled={busy}>
        <Form.Item name="name" label="用户组名称" rules={[{ required: true, whitespace: true, message: '请输入组名' }, { max: 150 }]}><Input disabled={editing?.protected} placeholder="例如：数据中心运维组" /></Form.Item>

      </Form>
      {editing !== undefined && <PrincipalEditor key={editing?.id ?? "new"} title="已加入成员" value={members} onChange={setMembers} disabled={busy} help="移出成员会撤销该组提供的权限，账号及其他授权保留。修改后请保存用户组。" />}
      {!!editing?.app_ids.length && <p>该组已获 {editing.app_ids.length} 个应用授权。<Link to="/system/apps">查看应用授权</Link></p>}
    </Drawer>
    <Modal title="删除用户组" open={!!deleting} okText="删除用户组" okButtonProps={{ danger: true }} confirmLoading={busy} cancelButtonProps={{ disabled: busy }} maskClosable={!busy} keyboard={!busy} closable={!busy} onCancel={() => setDeleting(null)} onOk={async () => {
      if (!deleting) return
      setBusy(true); setError('')
      try { await systemApi.deleteGroup(deleting.id, deleting.revision); setDeleting(null); message.success('用户组已删除'); await refresh() } catch (e) { setError(systemError(e)) } finally { setBusy(false) }
    }}>
      <p>删除“{deleting?.name}”将解除 {deleting?.member_ids.length} 名成员的组关系，并撤销该组的 {deleting?.app_ids.length} 个应用授权。</p>
      <p>用户账号、直接授权和其他用户组授权会保留。</p>{error && <Alert type="error" message={error} />}
    </Modal>
  </SystemLayout>
}
