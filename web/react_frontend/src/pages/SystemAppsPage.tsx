import React, { useState } from 'react'
import { Alert, Button, Drawer, Empty, Input, Modal, Space, Table, Tag, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import { useAuth } from '../context/AuthContext'
import SystemLayout from '../components/system/SystemLayout'
import PrincipalEditor, { AccessPreview } from '../components/system/PrincipalEditor'
import { systemApi, systemError, type AppRecord } from '../components/system/api'

const statuses: Record<string, string> = { enabled: '已启用', installed: '未启用', disabled: '已停用', discovered: '待安装', draining: '停用中', failed: '安装失败', installing: '安装中' }
export default function SystemAppsPage() {
  const query = useQuery(['system-access'], systemApi.access, { refetchOnWindowFocus: false })
  const cache = useQueryClient(), { refreshProfile } = useAuth()
  const [search, setSearch] = useState(''), [editing, setEditing] = useState<AppRecord | null>(null)
  const [users, setUsers] = useState<number[]>([]), [groups, setGroups] = useState<number[]>([])
  const [clearConfirm, setClearConfirm] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const originalUsers = editing?.grants.flatMap(g => g.user_id === null ? [] : [g.user_id]) || []
  const originalGroups = editing?.grants.flatMap(g => g.group_id === null ? [] : [g.group_id]) || []
  const same = (a: number[], b: number[]) => a.length === b.length && a.every(x => b.includes(x))
  const dirty = !same(users, originalUsers) || !same(groups, originalGroups)
  const open = (app: AppRecord) => {
    setEditing(app); setClearConfirm(false); setError(''); setUsers(app.grants.flatMap(g => g.user_id === null ? [] : [g.user_id])); setGroups(app.grants.flatMap(g => g.group_id === null ? [] : [g.group_id]))
  }
  return <SystemLayout title="应用授权" subtitle="按用户或用户组授予应用使用权，授权覆盖应用全部业务功能。" toolbar={<>
    <Input.Search aria-label="搜索应用" placeholder="搜索应用名称" value={search} onChange={e => setSearch(e.target.value)} allowClear />
    <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>
  </>}>
    <p className="system-help">管理员默认拥有已启用应用的使用权。普通用户的有效权限来自直接授权与用户组授权的并集。</p>
    {query.isError ? <Alert type="error" showIcon message="应用授权加载失败" description={systemError(query.error)} action={<Button onClick={() => query.refetch()}>重试</Button>} /> :
      <Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items.filter(a => a.name.includes(search) || a.id.includes(search))} scroll={{ x: 650 }} pagination={false} locale={{ emptyText: <Empty description="没有匹配的应用" /> }} columns={[
        { title: '应用', dataIndex: 'name', render: name => <strong>{name}</strong> },
        { title: '状态', dataIndex: 'state', render: state => <Tag color={state === 'enabled' ? 'green' : undefined}>{statuses[state] || state}</Tag> },
        { title: '直接授权', render: (_, row) => `${row.grants.filter(g => g.user_id !== null).length} 人` },
        { title: '用户组授权', render: (_, row) => `${row.grants.filter(g => g.group_id !== null).length} 个组` },
        { title: '操作', width: 180, render: (_, row) => <Button type="link" onClick={() => open(row)}>管理授权</Button> },
      ]} />}
    <Drawer title={`${editing?.name || ''} · 应用授权`} open={!!editing} width={940} onClose={() => { if (!busy) setEditing(null) }} maskClosable={!busy && !dirty} keyboard={!busy && !dirty} closable={!busy}
      footer={<div className="system-drawer-actions"><span>{dirty ? '有未保存的修改' : '授权已同步'}</span><Space><Button disabled={busy} onClick={() => setEditing(null)}>取消</Button><Button type="primary" loading={busy} disabled={!dirty || editing?.state === 'discovered'} onClick={async () => {
        if (!editing) return
        setBusy(true); setError('')
        try {
          await systemApi.saveAccess(editing.id, users, groups, editing.revision)
          setEditing(null); message.success('应用授权已保存'); await query.refetch(); await cache.invalidateQueries(['system-groups']); await refreshProfile()
        } catch (e) { setError(systemError(e)) } finally { setBusy(false) }
      }}>保存授权</Button></Space></div>}>
      {editing?.state !== 'enabled' && <Alert type="info" showIcon message={editing?.state === 'discovered' ? '请先完成应用安装，再保存授权。' : '应用当前不可用；授权会保留，启用后才可使用。'} style={{ marginBottom: 20 }} />}
      {error && <Alert type="error" showIcon message={error} description="修改未提交，编辑内容已保留。遇到冲突时请关闭并刷新，再重新核对。" style={{ marginBottom: 20 }} />}
      {!!editing && <>
        <div className="system-grant-summary"><span>直接授权 {users.length} 人 · 用户组授权 {groups.length} 组</span><Button danger disabled={busy || (!users.length && !groups.length)} onClick={() => setClearConfirm(true)}>撤销全部显式授权</Button></div>
        <PrincipalEditor key={editing.id + '-users'} title="直接授权用户" value={users} onChange={setUsers} disabled={busy} removeText="撤销" />
        <PrincipalEditor key={editing.id + '-groups'} title="授权用户组" kind="group" value={groups} onChange={setGroups} disabled={busy} removeText="撤销" />
        <AccessPreview users={users} groups={groups} available={editing.state === 'enabled'} />
      </>}
      <Modal title="撤销全部显式授权" open={clearConfirm} okText="确认清空" cancelText="取消" okButtonProps={{ danger: true }} onCancel={() => setClearConfirm(false)} onOk={() => { setUsers([]); setGroups([]); setClearConfirm(false) }}>
        <p>将移除该应用全部直接用户授权和用户组授权，admin 的系统权限保留。点击“保存授权”后生效。</p>
      </Modal>
    </Drawer>
  </SystemLayout>
}
