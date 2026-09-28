import React, { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Empty, Form, Input, InputNumber, Select, Space, Table, Tag, Typography, message } from 'antd'
import { api } from '@netops/config/api'
import { useAuth } from '@netops/context/AuthContext'

interface CredentialMetadata {
  id: string; name: string; username: string; domain: string
  scope_kind: 'domain' | 'cidr' | 'device'; scope_value: string
  priority: number; disabled: boolean; has_enable_secret: boolean; rotated_at: string
}
const endpoint = '/apps/device-access/credential-sets'
const scopeLabels = { domain: '管理域', cidr: '网段', device: '设备' }

export default function CredentialSets() {
  const { user } = useAuth()
  const canManage = user?.role === 'admin' || user?.role === 'operator'
  const [rows, setRows] = useState<CredentialMetadata[]>([])
  const [creating, setCreating] = useState(false)
  const [rotating, setRotating] = useState<CredentialMetadata>()
  const [busy, setBusy] = useState(false)
  const [createForm] = Form.useForm()
  const [rotateForm] = Form.useForm()
  const kind = Form.useWatch('scope_kind', createForm) || 'domain'
  const refresh = () => api.get<CredentialMetadata[]>(endpoint).then(({ data }) => setRows(data)).catch(() => message.error('凭据集列表读取失败'))
  useEffect(() => { void refresh() }, [])
  const closeCreate = () => { createForm.resetFields(); setCreating(false) }
  const closeRotate = () => { rotateForm.resetFields(); setRotating(undefined) }

  return <main style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
    <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
      <Typography.Title level={2} style={{ margin: 0 }}>凭据集</Typography.Title>
      {canManage && <Button type="primary" onClick={() => setCreating(true)}>新建凭据集</Button>}
    </Space>
    <Alert type="info" showIcon message="密码加密保存且无法查看。凭据只在所属管理域、作用域和当前账号的设备访问授权内使用。" description="优先使用设备上次成功的凭据，其次按设备、最长网段、管理域匹配；优先级数字越大越优先。单次登录最多尝试 3 个凭据集，连接或主机密钥错误需人工处理。" style={{ marginBottom: 20 }} />
    <Table<CredentialMetadata> rowKey="id" dataSource={rows} scroll={{ x: 850 }} locale={{ emptyText: <Empty description="还没有凭据集" /> }} columns={[
      { title: '名称', dataIndex: 'name' }, { title: '用户名', dataIndex: 'username' }, { title: '管理域', dataIndex: 'domain' },
      { title: '作用域', render: (_, row) => `${scopeLabels[row.scope_kind]} · ${row.scope_value}` },
      { title: '优先级', dataIndex: 'priority' },
      { title: '状态', render: (_, row) => <Tag color={row.disabled ? 'default' : 'green'}>{row.disabled ? '已禁用' : '可匹配'}</Tag> },
      { title: '特权密码', render: (_, row) => row.has_enable_secret ? '已设置' : '未设置' },
      { title: '更新时间', dataIndex: 'rotated_at', render: value => new Date(value).toLocaleString() },
      { title: '操作', render: (_, row) => canManage ? <Space>
        <Button onClick={() => { rotateForm.resetFields(); setRotating(row) }}>轮换密码</Button>
        <Button danger={!row.disabled} onClick={async () => {
          try { await api.patch(`${endpoint}/${row.id}`, { disabled: !row.disabled }); await refresh() }
          catch { message.error('凭据状态更新失败') }
        }}>{row.disabled ? '启用' : '禁用'}</Button>
      </Space> : '只读' },
    ]} />
    <Drawer title="新建凭据集" open={creating} onClose={closeCreate} destroyOnClose width={480}>
      <Form form={createForm} layout="vertical" preserve={false} initialValues={{ domain: 'default', scope_kind: 'domain', priority: 0 }} onFinish={async values => {
        setBusy(true)
        try {
          const payload = { ...values, scope_value: values.scope_kind === 'domain' ? values.domain : values.scope_value }
          if (!payload.enable_secret) delete payload.enable_secret
          await api.post(endpoint, payload)
          closeCreate(); await refresh()
        } catch { message.error('保存失败，请检查参数和权限后重新输入密码') }
        finally { createForm.resetFields(['password', 'enable_secret']); setBusy(false) }
      }}>
        <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input maxLength={128} /></Form.Item>
        <Form.Item label="管理域" name="domain" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
        <Form.Item label="作用域类型" name="scope_kind"><Select options={Object.entries(scopeLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
        {kind !== 'domain' && <Form.Item label={kind === 'cidr' ? 'CIDR 网段' : '设备 IP 或 uid:标识'} name="scope_value" rules={[{ required: true }]}><Input maxLength={256} /></Form.Item>}
        <Form.Item label="优先级" name="priority"><InputNumber min={-1000} max={1000} precision={0} /></Form.Item>
        <Form.Item label="用户名" name="username" rules={[{ required: true }]}><Input maxLength={128} autoComplete="off" /></Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true }]}><Input.Password maxLength={1024} autoComplete="new-password" /></Form.Item>
        <Form.Item label="特权密码（可选）" name="enable_secret"><Input.Password maxLength={1024} autoComplete="new-password" /></Form.Item>
        <Button htmlType="submit" type="primary" loading={busy}>加密保存</Button>
      </Form>
    </Drawer>
    <Drawer title={`轮换密码 · ${rotating?.name || ''}`} open={!!rotating} onClose={closeRotate} destroyOnClose width={440}>
      <Form form={rotateForm} layout="vertical" preserve={false} onFinish={async values => {
        setBusy(true)
        try {
          const payload: { password: string; enable_secret?: string | null } = { password: values.password }
          if (values.clear_enable) payload.enable_secret = null
          else if (values.enable_secret) payload.enable_secret = values.enable_secret
          await api.post(`${endpoint}/${rotating?.id}/rotate`, payload)
          closeRotate(); await refresh()
        } catch { message.error('轮换失败，请检查权限后重新输入密码') }
        finally { rotateForm.resetFields(['password', 'enable_secret']); setBusy(false) }
      }}>
        <Form.Item label="新密码" name="password" rules={[{ required: true }]}><Input.Password maxLength={1024} autoComplete="new-password" /></Form.Item>
        <Form.Item label="新特权密码（留空保留）" name="enable_secret"><Input.Password maxLength={1024} autoComplete="new-password" /></Form.Item>
        <Form.Item name="clear_enable" valuePropName="checked"><Checkbox>清除原特权密码</Checkbox></Form.Item>
        <Button htmlType="submit" type="primary" loading={busy}>保存新密码</Button>
      </Form>
    </Drawer>
  </main>
}
