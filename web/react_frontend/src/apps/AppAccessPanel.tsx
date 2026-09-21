import React, { useState } from 'react'
import { Alert, Button, Card, Select, Space, message } from 'antd'
import { useQuery } from 'react-query'
import { api } from '../config/api'
import { useAuth } from '../context/AuthContext'

interface AppAccess {
  id: string; name: string; state: string
  grants: { user_id: number | null; group_id: number | null }[]
}
interface AccessData {
  items: AppAccess[]; users: { id: number; username: string }[]; groups: { id: number; name: string }[]
}

function AppGrants({ app, data, reload }: { app: AppAccess; data: AccessData; reload: () => void }) {
  const [users, setUsers] = useState(app.grants.flatMap(g => g.user_id === null ? [] : [g.user_id]))
  const [groups, setGroups] = useState(app.grants.flatMap(g => g.group_id === null ? [] : [g.group_id]))
  const [saving, setSaving] = useState(false)
  const status = ({ enabled: '已启用', installed: '未启用', installing: '安装中', disabled: '已停用', draining: '停用中', discovered: '待安装', failed: '安装失败' } as Record<string, string>)[app.state] || '准备中'
  return <Card size="small" title={`${app.name} · ${status}`}>
    <Space direction="vertical" style={{ width: '100%' }}>
      <Select aria-label={`${app.name}授权用户`} mode="multiple" placeholder="授权用户" style={{ width: '100%' }} value={users} onChange={setUsers} options={data.users.map(u => ({ value: u.id, label: u.username }))} />
      <Select aria-label={`${app.name}授权用户组`} mode="multiple" placeholder="授权用户组" style={{ width: '100%' }} value={groups} onChange={setGroups} options={data.groups.map(g => ({ value: g.id, label: g.name }))} />
      <Button loading={saving} disabled={app.state === 'discovered'} onClick={async () => {
        setSaving(true)
        try { await api.put(`/platform/apps/admin/${app.id}/`, { user_ids: users, group_ids: groups }); reload(); message.success('应用授权已保存') }
        catch { message.error('授权保存失败，请刷新后重试') }
        finally { setSaving(false) }
      }}>保存应用授权</Button>
    </Space>
  </Card>
}

export default function AppAccessPanel() {
  const { refreshProfile } = useAuth()
  const { data, error, refetch } = useQuery(['application-access-admin'], async () => (await api.get<AccessData>('/platform/apps/admin/')).data)
  return <section aria-label="应用使用权限" style={{ margin: '16px 0' }}>
    <h2>应用使用权限</h2>
    <p>授权后可以使用该应用的全部业务功能。管理员默认拥有已启用应用的使用权。</p>
    {error ? <Alert type="error" message="无法读取应用授权" /> : null}
    <Space direction="vertical" style={{ width: '100%' }}>{data?.items.map(app => <AppGrants key={app.id + JSON.stringify(app.grants)} app={app} data={data} reload={() => { void refetch(); void refreshProfile() }} />)}</Space>
  </section>
}
