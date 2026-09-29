import React, { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Form, Input, Select, Space, Table, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import SystemLayout from '../components/system/SystemLayout'
import { systemApi, type Principal } from '../components/system/api'

// Device access scopes: which account may reach which management domain and CIDR.
// Checked by topology discovery, the SSH terminal and controlled diagnosis. The data and
// API stay in the device-access package; only the administration page lives here.
interface Scope { id: string; owner_id: string; domain: string; cidr: string; action: string; enabled: boolean; revision: number }
const endpoint = '/apps/device-access/grants'
const actions = [{ value: 'device.collect', label: '采集 / 受控诊断' }, { value: 'device.terminal', label: '人工 SSH 终端' }]

export default function SystemDeviceScopesPage() {
  const [rows, setRows] = useState<Scope[]>([])
  const [users, setUsers] = useState<Principal[]>([])
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState<File>()
  // The directory pages at most 100 accounts: listed owners are resolved by id, the picker
  // shows the first page and searches beyond it.
  const remember = (items: Principal[]) => setUsers(known => [...known.filter(u => !items.some(i => i.id === u.id)), ...items])
  const search = (text = '') => systemApi.directory({ kind: 'user', search: text, page_size: 100 }).then(({ items }) => remember(items)).catch(() => undefined)
  const refresh = () => api.get<Scope[]>(endpoint).then(({ data }) => {
    setRows(data)
    const ids = [...new Set(data.map(row => Number(row.owner_id)).filter(Number.isInteger))]
    if (ids.length) void systemApi.directory({ kind: 'user', ids, page_size: 100 }).then(({ items }) => remember(items)).catch(() => undefined)
  }).catch(() => message.error('访问范围读取失败'))
  useEffect(() => { void refresh(); void search() }, [])
  const account = (id: string) => {
    const user = users.find(u => String(u.id) === id)
    return user ? `${user.name}（ID ${id}）` : id
  }
  return <SystemLayout title="设备访问范围" subtitle="账号可访问的管理域与网段；拓扑发现、SSH 终端与受控诊断均按此校验"
    toolbar={<Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing(true)}>新增范围</Button>}>
    <Alert type="info" showIcon message="账号只能访问已启用范围内的设备。管理域须与发现任务、连接所用的管理域一致；人工 SSH 需同时具有采集和终端权限。" description="旧授权文件须导入一次。重复导入跳过已有记录，不会恢复已撤销的范围。" style={{ marginBottom: 20 }} />
    <Space style={{ marginBottom: 16 }}>
      <input type="file" accept=".json,application/json" aria-label="旧授权 JSON 文件" onChange={event => setFile(event.target.files?.[0])} />
      <Button disabled={!file} loading={busy} onClick={async () => {
        if (!file) return
        setBusy(true)
        try {
          if (file.size > 1024 * 1024) throw new Error('文件过大')
          const grants = JSON.parse(await file.text())
          const { data } = await api.post<{ added: number; skipped: number }>(`${endpoint}/import`, { grants })
          message.success(`已导入 ${data.added} 条，跳过 ${data.skipped} 条`); await refresh()
        } catch { message.error('导入失败，请检查文件格式、CIDR 和动作；最多 1000 条') }
        finally { setBusy(false) }
      }}>导入所选文件</Button>
    </Space>
    <Table<Scope> rowKey="id" dataSource={rows} columns={[
      { title: '账号', render: (_, row) => account(row.owner_id) }, { title: '管理域', dataIndex: 'domain' },
      { title: '网段', dataIndex: 'cidr' }, { title: '动作', render: (_, row) => actions.find(action => action.value === row.action)?.label },
      { title: '状态', render: (_, row) => row.enabled ? '启用' : '已撤销' },
      { title: '操作', render: (_, row) => <Button danger={row.enabled} onClick={async () => {
        try { await api.patch(`${endpoint}/${row.id}`, { revision: row.revision, enabled: !row.enabled }); await refresh() }
        catch { message.error('修改失败，授权可能已变化，请刷新后重试'); await refresh() }
      }}>{row.enabled ? '撤销' : '恢复授权'}</Button> },
    ]} />
    <Drawer title="新增设备访问范围" open={editing} onClose={() => setEditing(false)} destroyOnClose width={480}>
      <Form layout="vertical" initialValues={{ domain: 'default', action: 'device.collect' }} onFinish={async values => {
        setBusy(true)
        try { await api.post(endpoint, values); setEditing(false); await refresh() }
        catch { message.error('保存失败，请检查账号、网段及是否已有相同授权') }
        finally { setBusy(false) }
      }}>
        <Form.Item label="账号" name="owner_id" rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" placeholder="选择账号" onSearch={text => void search(text)}
            options={users.map(user => ({ value: String(user.id), label: `${user.name}（ID ${user.id}）` }))} />
        </Form.Item>
        <Form.Item label="管理域" name="domain" rules={[{ required: true }]} extra="与拓扑发现任务、设备连接中填写的管理域一致"><Input maxLength={64} /></Form.Item>
        <Form.Item label="CIDR 网段或单个 IP" name="cidr" rules={[{ required: true }]}><Input placeholder="10.10.1.0/24" /></Form.Item>
        <Form.Item label="允许动作" name="action" rules={[{ required: true }]}><Select options={actions} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>保存授权</Button>
      </Form>
    </Drawer>
  </SystemLayout>
}
