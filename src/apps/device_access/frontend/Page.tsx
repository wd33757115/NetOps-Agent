import React, { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Empty, Select, Space, Table, Tabs, Typography, message } from 'antd'
import { api } from '@netops/config/api'
import DeviceTerminal, { ConnectionForm, DeviceConnection, profiles } from '@netops/platform/terminal/DeviceTerminal'
import CredentialSets from './CredentialSets'
import HostKeys from './HostKeys'
import { useAuth } from '@netops/context/AuthContext'

function ConnectionsPage() {
  const [connections, setConnections] = useState<DeviceConnection[]>([])
  const [create, setCreate] = useState(false)
  const [terminal, setTerminal] = useState<DeviceConnection>()
  const [selected, setSelected] = useState<DeviceConnection>()
  const [profile, setProfile] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<unknown>()
  const refresh = () => api.get('/apps/device-access/connections').then(({ data }) => setConnections(data)).catch(() => message.error('无法读取连接授权'))
  useEffect(() => { void refresh() }, [])
  return <main style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
    <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}><div><Typography.Title level={2} style={{ margin: 0 }}>设备访问</Typography.Title><Typography.Text type="secondary">人工 SSH 与 DSH 诊断共用的连接授权</Typography.Text></div><Button type="primary" onClick={() => setCreate(true)}>新建连接授权</Button></Space>
    <Alert type="info" message="临时凭据仅登录一次；凭据集模式最多尝试 3 集，每集一次。无法连接或全部认证失败后需要人工处理。诊断命令集目前提供 Comware 和 NX-OS 的 LLDP、接口、路由采集。" style={{ marginBottom: 20 }} />
    <Table rowKey="connection_id" dataSource={connections} locale={{ emptyText: <Empty description="还没有有效连接，请人工提供设备凭据" /> }} columns={[
      { title: '设备', dataIndex: 'address' }, { title: '管理域', dataIndex: 'domain' },
      { title: '授权到期', dataIndex: 'expires_at', render: value => new Date(value).toLocaleString() },
      { title: '操作', render: (_, row) => <Space><Button disabled={!row.allow_terminal} onClick={() => setTerminal(row)}>SSH 终端</Button><Button onClick={() => { setSelected(row); setProfile(row.profiles[0]); setResult(undefined) }}>诊断采集</Button><Button danger onClick={() => void api.delete(`/apps/device-access/connections/${row.connection_id}`).then(refresh).catch(() => message.error('撤销失败'))}>撤销</Button></Space> },
    ]} />
    <Typography.Paragraph type="secondary">连接授权供人工终端与后端诊断调用。主机密钥与账号可访问的网段由管理员配置。</Typography.Paragraph>
    <Drawer open={create} onClose={() => setCreate(false)} title="新建连接授权" destroyOnClose width={480}><ConnectionForm onCreated={() => { setCreate(false); void refresh() }} /></Drawer>
    <DeviceTerminal open={!!terminal} onClose={() => setTerminal(undefined)} connection={terminal} address={terminal?.address} />
    <Drawer open={!!selected} onClose={() => setSelected(undefined)} title={`诊断采集 · ${selected?.address || ''}`} width={700} destroyOnClose>
      <Space><Select aria-label="诊断命令集" value={profile} onChange={setProfile} options={profiles.filter(p => selected?.profiles.includes(p.value))} style={{ width: 240 }} /><Button type="primary" loading={busy} disabled={!profile} onClick={async () => {
        setBusy(true)
        try { const { data } = await api.post('/apps/device-access/collect', { request_id: crypto.randomUUID(), connection_id: selected?.connection_id, profile_id: profile }); setResult(data) }
        catch (error) { message.error(error instanceof Error ? error.message : '采集失败') }
        finally { setBusy(false) }
      }}>执行一次采集</Button></Space>
      {result !== undefined && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 20 }}>{JSON.stringify(result, null, 2)}</pre>}
    </Drawer>
  </main>
}

export default function Page() {
  const { user } = useAuth()
  return <Tabs style={{ padding: '0 16px' }} items={[
    { key: 'connections', label: '连接授权', children: <ConnectionsPage /> },
    { key: 'credentials', label: '凭据集', children: <CredentialSets /> },
    ...(user?.role === 'admin' ? [{ key: 'host-keys', label: '主机密钥', children: <HostKeys /> }] : []),
  ]} />
}
