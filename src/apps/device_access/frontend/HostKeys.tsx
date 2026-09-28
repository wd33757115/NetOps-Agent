import React, { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Form, Input, InputNumber, Select, Space, Table, Tag, Typography, message } from 'antd'
import { api } from '@netops/config/api'

interface HostKey {
  domain: string; host: string; port: number; key_type: string; public_key: string
  fingerprint_sha256: string; observed_fingerprint?: string; status: string; revision: number
}
interface TrustState { keys: HostKey[]; policies: { domain: string; policy: string }[] }
const endpoint = '/apps/device-access/host-keys'

export default function HostKeys() {
  const [state, setState] = useState<TrustState>({ keys: [], policies: [] })
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const refresh = () => api.get<TrustState>(endpoint).then(({ data }) => setState(data)).catch(() => message.error('主机密钥读取失败'))
  useEffect(() => { void refresh() }, [])
  const open = (row?: HostKey) => {
    form.resetFields()
    form.setFieldsValue(row ? { ...row, confirm_fingerprint: '' } : { domain: 'default', port: 22, revision: 0, key_type: 'ssh-rsa' })
    setEditing(true)
  }
  return <main style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
    <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
      <Typography.Title level={2} style={{ margin: 0 }}>主机密钥</Typography.Title>
      <Button onClick={() => open()}>导入已核验公钥</Button>
    </Space>
    <Alert type="info" showIcon message="主机密钥按管理域、地址和端口固定。更换密钥前，请通过设备控制台等独立渠道核验指纹。" description="未配置的管理域沿用原有 known_hosts 校验。实验域可启用首次固定；严格模式只接受管理员已核验的密钥。" style={{ marginBottom: 20 }} />
    {!!state.keys.some(row => row.observed_fingerprint) && <Alert type="error" showIcon message="发现主机密钥变化，相关连接已拒绝。核实设备更换或异常后再更新信任。" style={{ marginBottom: 16 }} />}
    <Form layout="inline" initialValues={{ policy: 'strict' }} onFinish={async values => {
      try { await api.put(`${endpoint}/policy`, values); await refresh(); message.success('管理域策略已保存') }
      catch { message.error('管理域策略保存失败') }
    }} style={{ marginBottom: 16 }}>
      <Form.Item label="管理域" name="domain" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
      <Form.Item label="信任策略" name="policy"><Select style={{ width: 210 }} options={[{ value: 'strict', label: '严格：仅已核验' }, { value: 'tofu_pinned', label: '首次固定：变化即拒绝' }]} /></Form.Item>
      <Button htmlType="submit">保存域策略</Button>
    </Form>
    <Space wrap style={{ marginBottom: 16 }}>{state.policies.map(row => <Tag key={row.domain}>{row.domain} · {row.policy === 'strict' ? '严格' : '首次固定'}</Tag>)}</Space>
    <Table<HostKey> rowKey={row => `${row.domain}/${row.host}/${row.port}`} dataSource={state.keys} scroll={{ x: 950 }} columns={[
      { title: '管理域', dataIndex: 'domain' }, { title: '设备', render: (_, row) => `${row.host}:${row.port}` },
      { title: '状态', render: (_, row) => <Tag color={row.observed_fingerprint || row.status === 'revoked' ? 'red' : row.status === 'verified' ? 'green' : 'gold'}>{row.status === 'verified' ? '已核验' : row.status === 'revoked' ? '已撤销' : '已固定，未核验'}</Tag> },
      { title: '指纹', render: (_, row) => <div style={{ overflowWrap: 'anywhere' }}>{row.fingerprint_sha256}{row.observed_fingerprint && <div style={{ color: '#b42318' }}>变化后：{row.observed_fingerprint}</div>}</div> },
      { title: '操作', render: (_, row) => <Space><Button onClick={() => open(row)}>核验或更新</Button><Button danger disabled={row.status === 'revoked'} onClick={async () => {
        try { await api.post(`${endpoint}/revoke`, { domain: row.domain, host: row.host, port: row.port, revision: row.revision }); await refresh() }
        catch { message.error('撤销失败，请刷新后重试') }
      }}>撤销信任</Button></Space> },
    ]} />
    <Drawer title="核验设备公钥" open={editing} onClose={() => setEditing(false)} destroyOnClose width={580}>
      <Alert type="warning" message="请填写已独立核验的公钥与 SHA256 指纹。指纹变化时不要直接信任网络返回的新值。" style={{ marginBottom: 16 }} />
      <Form form={form} layout="vertical" onFinish={async values => {
        setBusy(true)
        try { await api.post(`${endpoint}/verify`, values); setEditing(false); await refresh() }
        catch { message.error('核验失败，请检查公钥与指纹；记录可能已变化，请刷新后重试') }
        finally { setBusy(false) }
      }}>
        <Form.Item name="revision" hidden><InputNumber /></Form.Item>
        <Form.Item label="管理域" name="domain" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
        <Form.Item label="设备 IP" name="host" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item label="SSH 端口" name="port" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
        <Form.Item label="公钥类型" name="key_type" rules={[{ required: true }]}><Input placeholder="ssh-ed25519" /></Form.Item>
        <Form.Item label="公钥 Base64" name="public_key" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={24000} /></Form.Item>
        <Form.Item label="独立核验的 SHA256 指纹" name="confirm_fingerprint" rules={[{ required: true, message: '请输入独立核验的指纹' }]}><Input placeholder="SHA256:..." /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>确认已核验并保存</Button>
      </Form>
    </Drawer>
  </main>
}
