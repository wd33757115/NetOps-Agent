import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Checkbox, ConfigProvider, Empty, Form, Input, InputNumber, Modal, Tabs, Tooltip, message } from 'antd'
import { ArrowLeftOutlined, CodeOutlined, DeleteOutlined, EditOutlined, KeyOutlined, LockOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import { useAuth } from '@netops/context/AuthContext'
import { LiveTerminal } from '@netops/platform/terminal/DeviceTerminal'
import './SshTerminal.css'

export interface SavedSession { id: string; name: string; host: string; port: number; username: string | null; uses_default: boolean }
interface DefaultLogin { username: string | null; configured: boolean }
interface OpenTab { key: string; session: SavedSession }
const endpoint = '/apps/device-access'
const errorText = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback

function SessionModal({ session, open, onClose, onSaved }: { session?: SavedSession; open: boolean; onClose: () => void; onSaved: () => void }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) form.setFieldsValue(session ? { ...session, username: session.username || '', password: '' } : { name: '', host: '', port: 22, username: '', password: '' }) }, [open, session, form])
  return <Modal open={open} title={session ? `编辑会话 · ${session.name}` : '新建会话'} okText="保存" cancelText="取消" confirmLoading={busy} onCancel={onClose} destroyOnClose
    onOk={() => void form.validateFields().then(async values => {
      const username = (values.username || '').trim()
      const password = values.password || ''
      if (!session && !!username !== !!password) { message.error('用户名和密码需要同时填写，或都留空使用默认'); return }
      setBusy(true)
      try {
        if (session) await api.patch(`${endpoint}/sessions/${session.id}`, { name: values.name, host: values.host, port: values.port, username, ...(password ? { password } : {}) })
        else await api.post(`${endpoint}/sessions`, { name: values.name, host: values.host, port: values.port, ...(username ? { username, password } : {}) })
        onSaved(); onClose()
      } catch (error) { message.error(errorText(error, '会话保存失败')) }
      finally { form.setFieldValue('password', ''); setBusy(false) }
    }).catch(() => undefined)}>
    <Form form={form} layout="vertical" preserve={false}>
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}><Input maxLength={128} placeholder="例如 核心交换机 A" /></Form.Item>
      <div className="ssh-form-row">
        <Form.Item name="host" label="地址" rules={[{ required: true, message: '请输入设备 IP' }]}><Input placeholder="10.10.1.1" /></Form.Item>
        <Form.Item name="port" label="端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
      </div>
      <Form.Item name="username" label="用户名" extra={session ? '清空用户名则改用默认用户名密码' : '用户名和密码都留空则使用默认用户名密码'}><Input maxLength={128} autoComplete="off" /></Form.Item>
      <Form.Item name="password" label="密码"><Input.Password maxLength={1024} autoComplete="new-password" placeholder={session?.username ? '留空保留原密码' : undefined} /></Form.Item>
    </Form>
  </Modal>
}

function LoginForm({ open, title, extra, onClose, onSubmit }: { open: boolean; title: string; extra?: string; onClose: () => void; onSubmit: (values: { username: string; password: string }) => Promise<void> }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  return <Modal open={open} title={title} okText="保存" cancelText="取消" confirmLoading={busy} onCancel={() => { form.resetFields(); onClose() }} destroyOnClose
    onOk={() => void form.validateFields().then(async values => {
      setBusy(true)
      try { await onSubmit(values); form.resetFields(); onClose() }
      catch (error) { message.error(errorText(error, '保存失败')) }
      finally { form.setFieldValue('password', ''); setBusy(false) }
    }).catch(() => undefined)}>
    {extra && <p className="ssh-modal-note">{extra}</p>}
    <Form form={form} layout="vertical" preserve={false}>
      <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}><Input maxLength={128} autoComplete="off" /></Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password maxLength={1024} autoComplete="new-password" /></Form.Item>
    </Form>
  </Modal>
}

export default function Page() {
  const { user } = useAuth()
  const [sessions, setSessions] = useState<SavedSession[]>([])
  const [defaults, setDefaults] = useState<DefaultLogin>({ username: null, configured: false })
  const [checked, setChecked] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [tabs, setTabs] = useState<OpenTab[]>([])
  const [active, setActive] = useState<string>()
  const [editing, setEditing] = useState<SavedSession | 'new'>()
  const [dialog, setDialog] = useState<'default' | 'batch'>()
  const refresh = () => Promise.all([
    api.get<SavedSession[]>(`${endpoint}/sessions`).then(({ data }) => { setSessions(data); setChecked(ids => ids.filter(id => data.some(row => row.id === id))) }),
    api.get<DefaultLogin>(`${endpoint}/default-login`).then(({ data }) => setDefaults(data)),
  ]).catch(error => message.error(errorText(error, '会话读取失败')))
  useEffect(() => { void refresh() }, [])
  const visible = useMemo(() => {
    const term = search.trim().toLowerCase()
    return term ? sessions.filter(row => `${row.name} ${row.host}`.toLowerCase().includes(term)) : sessions
  }, [sessions, search])
  const open = (session: SavedSession) => {
    const key = `${session.id}:${crypto.randomUUID()}`
    setTabs(current => [...current, { key, session }]); setActive(key)
  }
  const close = (key: string) => {
    const rest = tabs.filter(tab => tab.key !== key)
    setTabs(rest)
    if (active === key) setActive(rest[rest.length - 1]?.key)
  }
  const toggle = (id: string, value: boolean) => setChecked(current => value ? [...current, id] : current.filter(item => item !== id))
  const remove = () => Modal.confirm({
    title: `删除 ${checked.length} 个会话？`, content: '已打开的终端不受影响。', okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => {
      try { for (const id of checked) await api.delete(`${endpoint}/sessions/${id}`) }
      catch (error) { message.error(errorText(error, '删除失败')) }
      setChecked([]); await refresh()
    },
  })
  const single = checked.length === 1 ? sessions.find(row => row.id === checked[0]) : undefined

  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 9, colorText: '#242b38', colorBorder: '#dce1e9', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 36 } }}>
    <div className="ssh-app">
      <header className="ssh-toolbar">
        <div className="ssh-breadcrumb"><Link className="ssh-back" to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span className="ssh-breadcrumb-divider">/</span><span className="ssh-toolbar-title">SSH 终端</span></div>
        <span className="ssh-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span>
      </header>
      <div className="ssh-window">
        <aside className="ssh-sidebar" aria-label="会话管理器">
          <div className="ssh-product"><span className="ssh-product-icon"><CodeOutlined aria-hidden="true" /></span><div><strong>SSH 终端</strong><span>SESSION MANAGER</span></div></div>
          <div className="ssh-nav-label"><span>会话 <em>{sessions.length}</em></span>
            <div className="ssh-session-actions">
              <Tooltip title="新建会话"><Button type="text" size="small" aria-label="新建会话" icon={<PlusOutlined />} onClick={() => setEditing('new')} /></Tooltip>
              <Tooltip title="编辑会话"><Button type="text" size="small" aria-label="编辑会话" icon={<EditOutlined />} disabled={!single} onClick={() => setEditing(single)} /></Tooltip>
              <Tooltip title="批量修改用户名密码"><Button type="text" size="small" aria-label="批量修改用户名密码" icon={<KeyOutlined />} disabled={!checked.length} onClick={() => setDialog('batch')} /></Tooltip>
              <Tooltip title="删除会话"><Button type="text" size="small" aria-label="删除会话" icon={<DeleteOutlined />} disabled={!checked.length} onClick={remove} /></Tooltip>
            </div>
          </div>
          <Input className="ssh-search" aria-label="搜索会话" placeholder="搜索名称或地址" prefix={<SearchOutlined aria-hidden="true" />} value={search} onChange={event => setSearch(event.target.value)} allowClear />
          {checked.length > 0 && <div className="ssh-selection">已选 {checked.length} 个<Button type="link" size="small" onClick={() => setChecked([])}>清除</Button></div>}
          <ul className="ssh-sessions">
            {visible.map(row => <li key={row.id} className={checked.includes(row.id) ? 'checked' : ''}>
              <Checkbox aria-label={`选择 ${row.name}`} checked={checked.includes(row.id)} onChange={event => toggle(row.id, event.target.checked)} />
              <button type="button" title="双击打开终端" onDoubleClick={() => open(row)} onKeyDown={event => { if (event.key === 'Enter') open(row) }}>
                <strong>{row.name}</strong><span>{row.host}{row.port !== 22 ? `:${row.port}` : ''} · {row.uses_default ? '默认登录' : row.username}</span>
              </button>
            </li>)}
          </ul>
          {!sessions.length && <div className="ssh-sessions-empty">还没有会话<Button type="link" onClick={() => setEditing('new')}>新建会话</Button></div>}
          <div className="ssh-sidebar-bottom">
            <button type="button" className="ssh-default" onClick={() => setDialog('default')}><LockOutlined aria-hidden="true" /><div><strong>默认用户名密码</strong><span>{defaults.configured ? `${defaults.username} · 密码已加密保存` : '未设置 · 点击设置'}</span></div></button>
          </div>
        </aside>
        <main className="ssh-main">
          {tabs.length ? <Tabs className="ssh-tabs" type="editable-card" hideAdd activeKey={active} onChange={setActive}
            onEdit={(key, action) => { if (action === 'remove' && typeof key === 'string') close(key) }}
            items={tabs.map(tab => ({ key: tab.key, label: tab.session.name, children: <LiveTerminal target={{ session_id: tab.session.id }} height="calc(100dvh - 190px)" /> }))} />
            : <div className="ssh-empty">
              <div className="ssh-eyebrow">SSH 终端</div><h1>打开一个会话</h1>
              <p>在左侧双击会话打开终端，多个终端以标签页并排。没有填写用户名密码的会话使用默认用户名密码；登录失败时会提示重新输入。</p>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessions.length ? '双击会话开始' : '先新建一个会话'}>{!sessions.length && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setEditing('new')}>新建会话</Button>}</Empty>
            </div>}
        </main>
      </div>
      <SessionModal open={!!editing} session={editing === 'new' ? undefined : editing} onClose={() => setEditing(undefined)} onSaved={() => void refresh()} />
      <LoginForm open={dialog === 'default'} title="默认用户名密码" extra="没有单独填写用户名密码的会话和拓扑节点的终端使用这一组。密码加密保存，不会显示。" onClose={() => setDialog(undefined)}
        onSubmit={async values => { await api.put(`${endpoint}/default-login`, values); await refresh() }} />
      <LoginForm open={dialog === 'batch'} title={`修改 ${checked.length} 个会话的用户名密码`} onClose={() => setDialog(undefined)}
        onSubmit={async values => { await api.post(`${endpoint}/sessions/login`, { ...values, session_ids: checked }); await refresh() }} />
    </div>
  </ConfigProvider>
}
