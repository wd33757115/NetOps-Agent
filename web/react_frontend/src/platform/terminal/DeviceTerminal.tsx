import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Drawer, Form, Input, InputNumber, Select, Space, Switch, message } from 'antd'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { api } from '../../config/api'

export const profiles = [
  ['comware-lldp-v1', 'Comware · LLDP'], ['comware-interfaces-v1', 'Comware · 接口'], ['comware-routes-v1', 'Comware · 路由'],
  ['nxos-lldp-v1', 'NX-OS · LLDP'], ['nxos-interfaces-v1', 'NX-OS · 接口'], ['nxos-routes-v1', 'NX-OS · 路由'],
].map(([value, label]) => ({ value, label }))

export interface DeviceConnection { connection_id: string; address: string; domain: string; expires_at: string; profiles: string[]; allow_terminal: boolean }

export function ConnectionForm({ address, domain, terminal = false, onCreated }: { address?: string; domain?: string; terminal?: boolean; onCreated: (value: DeviceConnection) => void }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const credentialMode = Form.useWatch('credential_mode', form) || 'temporary'
  return <Form form={form} layout="vertical" initialValues={{ address, domain: domain || 'default', port: 22, credential_mode: 'temporary', profiles: ['comware-lldp-v1'], allow_terminal: terminal }}
    onFinish={async values => {
      setBusy(true)
      const payload = { ...values }
      if (payload.credential_mode === 'vault') { delete payload.username; delete payload.password }
      try { const { data } = await api.post<DeviceConnection>('/apps/device-access/connections', payload); form.resetFields(['password', 'username']); onCreated(data) }
      catch (error) { message.error(error instanceof Error ? error.message : '连接授权失败') }
      finally { form.setFieldValue('password', ''); setBusy(false) }
    }}>
    <Form.Item label="管理域" name="domain" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
    <Space align="start"><Form.Item label="设备地址" name="address" rules={[{ required: true }]}><Input placeholder="10.10.1.1" /></Form.Item>
      <Form.Item label="SSH 端口" name="port"><InputNumber min={1} max={65535} /></Form.Item></Space>
    <Form.Item label="凭据来源" name="credential_mode"><Select options={[{ value: 'temporary', label: '人工输入临时凭据' }, { value: 'vault', label: '按设备作用域选择凭据集' }]} onChange={() => form.resetFields(['username', 'password'])} /></Form.Item>
    {credentialMode === 'temporary' ? <>
      <Form.Item label="用户名" name="username" preserve={false} rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item>
      <Form.Item label="密码" name="password" preserve={false} rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
    </> : <Alert type="info" message="使用本账号在该管理域和设备范围内的凭据集，最多尝试 3 集，每集一次。全部认证失败或无法连接时等待人工处理。" style={{ marginBottom: 16 }} />}
    <Form.Item label="允许的诊断命令集" name="profiles" rules={[{ required: true }]}><Select mode="multiple" options={profiles} /></Form.Item>
    <Form.Item label="允许人工 SSH 终端" name="allow_terminal" valuePropName="checked"><Switch disabled={terminal} /></Form.Item>
    <Form.Item label="允许受控诊断会话" name="allow_session" valuePropName="checked"><Switch /></Form.Item>
    <p style={{ color: '#667085' }}>授权有效期 1 小时，仅限本账号及此设备。密码不会保存在浏览器。主机密钥须由管理员预先核验。</p>
    <Button type="primary" htmlType="submit" loading={busy}>创建连接授权</Button>
  </Form>
}

function LiveTerminal({ connectionId }: { connectionId: string }) {
  const host = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState('正在申请终端票据…')
  useEffect(() => {
    let disposed = false
    let socket: WebSocket | undefined
    const terminal = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 3000, allowProposedApi: false, theme: { background: '#17212c', foreground: '#e5eaf0' } })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(host.current!)
    const resize = () => { fit.fit(); if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows })) }
    const observer = new ResizeObserver(resize)
    observer.observe(host.current!)
    const input = terminal.onData(data => {
      if (socket?.readyState === WebSocket.OPEN) {
        // Keep encoded frames below the server's 4 KB cap, including Unicode/paste.
        for (let i = 0; i < data.length; i += 512) socket.send(JSON.stringify({ type: 'input', data: data.slice(i, i + 512) }))
      }
    })
    void api.post('/apps/device-access/terminal-tickets', { connection_id: connectionId, request_id: crypto.randomUUID() }).then(({ data }) => {
      if (disposed) return
      const url = new URL(`/ws/v1/terminals/${data.session_id}`, window.location.href)
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(url)
      socket.onopen = () => socket?.send(JSON.stringify({ ticket: data.ticket }))
      socket.onmessage = event => {
        const payload = JSON.parse(event.data)
        if (payload.type === 'ready') { setStatus('已连接 · 空闲 2 分钟或会话 15 分钟后断开'); resize(); terminal.focus() }
        if (payload.type === 'output') terminal.write(payload.data)
        if (payload.type === 'closed') setStatus(`已断开${payload.reason ? ` · ${payload.reason}` : ''}`)
      }
      socket.onerror = () => setStatus('连接失败，请检查权限或凭据后重新连接')
      socket.onclose = () => setStatus(previous => previous.startsWith('已断开') ? previous : '连接已关闭')
    }).catch(error => { if (!disposed) setStatus(error.message || '无法打开终端') })
    return () => { disposed = true; observer.disconnect(); input.dispose(); socket?.close(); terminal.dispose() }
  }, [connectionId])
  return <><Alert message={status} type="info" style={{ marginBottom: 12 }} /><div ref={host} aria-label="设备 SSH 终端" style={{ height: '65vh', background: '#17212c', padding: 12 }} /></>
}

export default function DeviceTerminal({ open, onClose, address, domain, connection }: { open: boolean; onClose: () => void; address?: string; domain?: string; connection?: DeviceConnection }) {
  const [created, setCreated] = useState<DeviceConnection>()
  return <Drawer title={`SSH 终端${address ? ` · ${address}` : ''}`} open={open} onClose={() => { setCreated(undefined); onClose() }} width="min(100vw, 1000px)" destroyOnClose>
    {open && (created || connection) ? <LiveTerminal connectionId={(created || connection)!.connection_id} /> : open && <ConnectionForm address={address} domain={domain} terminal onCreated={setCreated} />}
  </Drawer>
}
