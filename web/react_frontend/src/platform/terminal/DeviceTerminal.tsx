import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Form, Input, Modal, Typography, type InputRef } from 'antd'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { api } from '../../config/api'

/** A saved session, or a bare address that logs in with the default username/password. */
export type TerminalTarget = { session_id: string } | { host: string; port?: number }
export interface Login { username: string; password: string }
export interface HostKey { host: string; port: number; status: string; key_type: string; fingerprint: string; observed_key_type?: string | null; observed_fingerprint?: string | null }

const fingerprintStyle: React.CSSProperties = { margin: '4px 0 12px', padding: '8px 10px', background: '#f5f7fa', borderRadius: 6, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 12, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }
const LOGIN_REASONS = ['authentication_failed', 'credentials_required', 'credential_expired']
const HOST_KEY_REASONS = ['host_key_unknown', 'host_key_changed']
const reasons: Record<string, string> = {
  authentication_failed: '用户名或密码不正确', credentials_required: '需要用户名和密码', credential_expired: '需要重新输入用户名和密码',
  host_key_unknown: '首次连接，等待确认主机密钥', host_key_changed: '主机密钥已变化，等待确认',
  terminal_expired: '已达到空闲 2 分钟或会话 15 分钟上限', connection_failed: '无法连接设备', timeout: '连接或命令等待超时',
  not_authorized: '当前账号不能使用 SSH 终端', terminal_unavailable: '终端服务不可用',
  cancelled: '已停止会话', interaction_required: '设备要求交互输入，请通过控制台处理', command_timeout: '命令超时，已停止会话',
}
const hasCommandControls = (value: string) => Array.from(value).some(character => {
  const code = character.charCodeAt(0)
  return code < 32 || code === 127 || code === 0x2028 || code === 0x2029
})

export const readHostKey = (host: string, port: number) => api.get<HostKey>(`/apps/device-access/host-keys/${host}/${port}`).then(({ data }) => data)
export const acceptHostKey = (key: HostKey) => api.post('/apps/device-access/host-keys/accept', { host: key.host, port: key.port, fingerprint: key.observed_fingerprint || key.fingerprint })

export function LoginModal({ open, title, allowSave = false, onSubmit, onCancel }: { open: boolean; title: string; allowSave?: boolean; onSubmit: (login: Login, save: boolean) => void; onCancel: () => void }) {
  const [form] = Form.useForm()
  const close = () => { form.resetFields(); onCancel() }
  return <Modal open={open} title={title} okText="登录" cancelText="取消" onCancel={close} destroyOnClose
    onOk={() => void form.validateFields().then(values => { form.resetFields(); onSubmit({ username: values.username, password: values.password }, !!values.save) }).catch(() => undefined)}>
    <Form form={form} layout="vertical" preserve={false}>
      <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}><Input autoComplete="off" autoFocus /></Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password autoComplete="new-password" /></Form.Item>
      {allowSave && <Form.Item name="save" valuePropName="checked" extra="登录成功后加密保存到此会话"><Checkbox>保存到会话</Checkbox></Form.Item>}
    </Form>
  </Modal>
}

export function HostKeyModal({ hostKey, busy, onAccept, onCancel }: { hostKey?: HostKey; busy?: boolean; onAccept: () => void; onCancel: () => void }) {
  const changed = !!hostKey?.observed_fingerprint
  return <Modal open={!!hostKey} title={changed ? '主机密钥已变化' : '确认主机密钥'} onCancel={onCancel} destroyOnClose
    footer={[<Button key="cancel" onClick={onCancel}>取消</Button>, <Button key="accept" type="primary" danger={changed} loading={busy} onClick={onAccept}>{changed ? '替换并连接' : '信任并连接'}</Button>]}>
    {hostKey && <>
      {changed
        ? <Alert type="error" showIcon message="设备返回的主机密钥与已保存的不同" description="可能是设备重装或更换了密钥，也可能有人冒充该设备。请通过控制台等其他途径核对后再替换。" style={{ marginBottom: 16 }} />
        : <p>首次连接 {hostKey.host}:{hostKey.port}。请核对设备的主机密钥指纹，确认后保存，以后连接会自动校验。</p>}
      {changed && <><Typography.Text type="secondary">已保存（{hostKey.key_type}）</Typography.Text><pre style={fingerprintStyle}>{hostKey.fingerprint}</pre></>}
      <Typography.Text type="secondary">{changed ? '设备当前' : '设备'}（{changed ? hostKey.observed_key_type : hostKey.key_type}）</Typography.Text>
      <pre style={fingerprintStyle}>{hostKey.observed_fingerprint || hostKey.fingerprint}</pre>
    </>}
  </Modal>
}

export function LiveTerminal({ target, height = '65vh' }: { target: TerminalTarget; height?: string }) {
  const host = useRef<HTMLDivElement>(null)
  const commandInput = useRef<InputRef>(null)
  const connection = useRef<WebSocket>()
  const pending = useRef<string>()
  const submittedCommand = useRef('')
  const history = useRef<string[]>([])
  const historyPosition = useRef(0)
  const historyDraft = useRef('')
  const next = useRef<{ login?: Login; save?: boolean }>({})
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState('正在连接…')
  const [closed, setClosed] = useState(false)
  const [connected, setConnected] = useState(false)
  const [command, setCommand] = useState('')
  const [executing, setExecuting] = useState(false)
  const [commandNotice, setCommandNotice] = useState('')
  const [asking, setAsking] = useState<string>()
  const [hostKey, setHostKey] = useState<HostKey>()
  const [busy, setBusy] = useState(false)
  const key = JSON.stringify(target)
  const connect = (login?: Login, save = false) => { next.current = { login, save }; setAttempt(value => value + 1) }

  useEffect(() => {
    let disposed = false
    let ended = false
    let socket: WebSocket | undefined
    const { login, save } = next.current
    next.current = {}
    connection.current = undefined; pending.current = undefined; submittedCommand.current = ''
    history.current = []; historyPosition.current = 0; historyDraft.current = ''
    setClosed(false); setConnected(false); setExecuting(false); setCommand(''); setCommandNotice(''); setStatus('正在连接…')
    // Output only: xterm keystrokes and paste must never bypass command validation.
    const terminal = new Terminal({ disableStdin: true, cursorBlink: false, fontSize: 14, scrollback: 3000, allowProposedApi: false, theme: { background: '#17212c', foreground: '#e5eaf0' } })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(host.current!)
    const resize = () => { fit.fit(); if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows })) }
    const observer = new ResizeObserver(resize)
    observer.observe(host.current!)
    const stop = (reason?: string | null, where?: { host: string; port: number }) => {
      if (disposed || ended) return
      ended = true
      pending.current = undefined
      setClosed(true); setConnected(false); setExecuting(false)
      setStatus(reason ? `已断开 · ${reasons[reason] || reason}` : '已断开')
      if (reason && LOGIN_REASONS.includes(reason)) setAsking(reason)
      if (reason && HOST_KEY_REASONS.includes(reason) && where) void readHostKey(where.host, where.port).then(value => { if (!disposed) setHostKey(value) }).catch(() => undefined)
    }
    const body = { request_id: crypto.randomUUID(), ...JSON.parse(key), ...(login ? { login, save } : {}) }
    void api.post('/apps/device-access/terminal-tickets', body).then(({ data }) => {
      if (disposed) return
      const url = new URL(`/ws/v1/terminals/${data.session_id}`, window.location.href)
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(url)
      connection.current = socket
      socket.onopen = () => socket?.send(JSON.stringify({ ticket: data.ticket }))
      socket.onmessage = event => {
        const payload = JSON.parse(event.data)
        if (disposed || ended) return
        if (payload.type === 'ready') { setConnected(true); setStatus('已连接 · 空闲 2 分钟或会话 15 分钟后断开'); resize(); commandInput.current?.focus() }
        if (payload.type === 'output') terminal.write(payload.data)
        if (pending.current && payload.request_id === pending.current) {
          if (payload.type === 'command_sent') setCommand('')
          if (payload.type === 'command_rejected' || payload.type === 'command_finished') {
            if (payload.type === 'command_rejected') setCommand(submittedCommand.current)
            pending.current = undefined
            setExecuting(false)
            setCommandNotice(payload.type === 'command_rejected' ? payload.message || '命令不在允许范围内' : payload.status === 'failed' ? '命令执行失败，请查看输出。' : '')
          }
        }
        if (payload.type === 'closed') stop(payload.reason, payload.host ? { host: payload.host, port: payload.port } : undefined)
      }
      socket.onerror = () => stop('connection_failed')
      socket.onclose = () => stop()
    }).catch(error => { if (!disposed) { setClosed(true); setStatus(error.message || '无法打开终端') } })
    return () => { disposed = true; connection.current = undefined; pending.current = undefined; submittedCommand.current = ''; observer.disconnect(); socket?.close(); terminal.dispose() }
  }, [key, attempt])
  useEffect(() => { if (connected && !executing) commandInput.current?.focus() }, [connected, executing])

  const sendCommand = () => {
    const socket = connection.current
    const text = command
    if (!connected || closed || pending.current || socket?.readyState !== WebSocket.OPEN || !text.trim()) return
    if (hasCommandControls(text)) { setCommandNotice('请一次输入一条完整命令，不支持控制字符或多行粘贴。'); return }
    const requestId = crypto.randomUUID()
    pending.current = requestId
    submittedCommand.current = text
    setExecuting(true); setCommandNotice('')
    if (history.current[history.current.length - 1] !== text) history.current = [...history.current.slice(-49), text]
    historyPosition.current = history.current.length; historyDraft.current = ''
    try { socket.send(JSON.stringify({ type: 'command', command: text, request_id: requestId })) }
    catch { pending.current = undefined; setExecuting(false); setCommandNotice('命令未发送，请检查连接。') }
  }

  const stopSession = () => {
    const socket = connection.current
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'cancel' }))
    socket?.close()
    pending.current = undefined
    setConnected(false); setExecuting(false); setClosed(true); setStatus('已断开 · 已停止会话')
  }

  return <div className="device-terminal" style={{ display: 'flex', flexDirection: 'column', height, minHeight: 240 }}>
    <Alert type={closed ? 'warning' : 'info'} message={status} style={{ marginBottom: 12 }} action={closed ? <Button size="small" onClick={() => connect()}>重新连接</Button> : connected ? <Button size="small" onClick={stopSession}>停止会话</Button> : undefined} />
    <div ref={host} aria-label="设备 SSH 终端" style={{ flex: 1, minHeight: 0, background: '#17212c', padding: 12, borderRadius: 8 }} />
    <form onSubmit={event => { event.preventDefault(); sendCommand() }} style={{ display: 'flex', gap: 8, marginTop: 12 }}>
      <Input ref={commandInput} aria-label="设备命令" placeholder="输入完整命令，按 Enter 执行" autoComplete="off" spellCheck={false} value={command} disabled={!connected || executing}
        onChange={event => setCommand(event.target.value)}
        onPaste={event => { if (hasCommandControls(event.clipboardData.getData('text'))) { event.preventDefault(); setCommandNotice('请一次输入一条完整命令，不支持控制字符或多行粘贴。') } }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) { if (event.key === 'Enter') event.preventDefault(); return }
          if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
          event.preventDefault()
          if (historyPosition.current === history.current.length) historyDraft.current = command
          historyPosition.current = Math.max(0, Math.min(history.current.length, historyPosition.current + (event.key === 'ArrowUp' ? -1 : 1)))
          setCommand(historyPosition.current === history.current.length ? historyDraft.current : history.current[historyPosition.current])
        }} />
      <Button type="primary" htmlType="submit" loading={executing} disabled={!connected || !command.trim()}>执行</Button>
    </form>
    <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 6 }}>↑ ↓ 查看本次会话历史。不支持远端 Tab 补全或交互确认，请输入完整命令。</Typography.Text>
    {commandNotice && <Alert type="warning" showIcon message={commandNotice} style={{ marginTop: 8 }} />}
    <LoginModal open={!!asking} title={asking === 'authentication_failed' ? '登录失败，请重新输入' : '输入用户名和密码'} allowSave={'session_id' in target}
      onCancel={() => setAsking(undefined)} onSubmit={(login, save) => { setAsking(undefined); connect(login, save) }} />
    <HostKeyModal hostKey={hostKey} busy={busy} onCancel={() => setHostKey(undefined)} onAccept={async () => {
      if (!hostKey) return
      setBusy(true)
      try { await acceptHostKey(hostKey); setHostKey(undefined); connect() }
      catch (error) { setStatus(error instanceof Error ? error.message : '主机密钥未保存，请重新连接'); setHostKey(undefined) }
      finally { setBusy(false) }
    }} />
  </div>
}

/** Terminal for a device address, e.g. a topology node; logs in with the default username/password. */
export default function DeviceTerminal({ open, onClose, address }: { open: boolean; onClose: () => void; address?: string }) {
  return <Drawer title={`SSH 终端${address ? ` · ${address}` : ''}`} open={open} onClose={onClose} width="min(100vw, 1000px)" destroyOnClose>
    {open && address && <LiveTerminal target={{ host: address, port: 22 }} height="calc(100vh - 140px)" />}
  </Drawer>
}
