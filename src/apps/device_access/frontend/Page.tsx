import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, AutoComplete, Button, Checkbox, ConfigProvider, Empty, Form, Input, InputNumber, Modal, Table, Tabs, Tooltip, message } from 'antd'
import { ArrowLeftOutlined, CodeOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FileTextOutlined, KeyOutlined, LockOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import { useAuth } from '@netops/context/AuthContext'
import { LiveTerminal } from '@netops/platform/terminal/DeviceTerminal'
import './SshTerminal.css'

export interface SavedSession { id: string; name: string; host: string; port: number; username: string | null; uses_default: boolean }
interface DefaultLogin { username: string | null; configured: boolean }
interface OpenTab { key: string; session: SavedSession }
interface CommandPolicy { patterns: string[]; version: number; updated_at: string | null; updated_by: string | null }
interface BatchCommand { command: string; status: string; output: string; error?: string; truncated?: boolean }
interface BatchResult { session_id: string; name: string; host: string; status: string; commands: BatchCommand[]; error?: string }
interface BatchRun { id: string; status: string; results: BatchResult[]; cancel_requested?: boolean; notice?: string }
interface AuditItem { id: string; created_at: string; user_id: string; action: string; status: string; detail: Record<string, unknown> }
interface AuditPage { items: AuditItem[]; next_cursor: string | null }
interface SessionLog { id: string; filename: string; device_name: string; host: string; port: number; source_app: string; actor_id: string; started_at: string; updated_at: string; status: string }
interface SessionLogPage { items: SessionLog[]; next_cursor: string | null }
interface SessionLogDetail extends SessionLog { content: string }
const endpoint = '/apps/device-access'
const errorText = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback
const activeBatch = (status?: string) => !!status && ['queued', 'pending', 'running', 'cancel_requested'].includes(status)
const statuses: Record<string, string> = { queued: '排队中', pending: '待执行', running: '执行中', active: '进行中', connecting: '连接中', connected: '已连接', closed: '已关闭', disconnected: '已断开', cancel_requested: '正在停止', completed: '已完成', success: '成功', succeeded: '成功', failed: '失败', cancelled: '已停止', rejected: '已拒绝', denied: '已拒绝', skipped: '已跳过', not_executed: '未执行', interrupted: '已中断', waiting_for_input: '已中断' }
const statusLabel = (status: string) => statuses[status] || status
const actions: Record<string, string> = {
  connecting: '连接设备', connected: '连接成功', connection_failed: '连接失败', disconnected: '连接断开',
  command_requested: '请求执行命令', command_finished: '命令结束', command_denied: '命令被拒绝', command_not_executed: '命令未执行',
  terminal_requested: '打开终端', terminal_closed: '终端关闭', terminal_input_rejected: '终端输入被拒绝',
  batch_requested: '开始批量执行', batch_cancel_requested: '停止批量执行', batch_device_finished: '批量设备执行结束', batch_finished: '批量执行结束',
  session_created: '新建会话', session_updated: '修改会话', session_deleted: '删除会话', session_login_updated: '修改会话登录', session_login_saved: '保存会话登录', default_login_updated: '修改默认登录',
  host_key_observed: '发现主机密钥', host_key_changed: '主机密钥变化', host_key_restored: '主机密钥恢复', host_key_accepted: '确认主机密钥', policy_changed: '修改命令白名单',
}
const actionLabel = (action: string) => actions[action.replace(/^ssh\./, '')] || action
const dateLabel = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const sourceLabel = (source: string) => ({ 'device-access': 'SSH 终端', topology: '拓扑', assistant: '知识问答', inspection: '巡检', 'rule-governance': '规则治理', 'device-management': '设备管理' }[source] || source)

function downloadFilename(disposition: string, fallback: string) {
  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition)
  if (encoded) {
    try { return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, '')).replace(/[\\/]/g, '_') }
    catch { return fallback }
  }
  return /filename\s*=\s*"([^"]+)"/i.exec(disposition)?.[1].replace(/[\\/]/g, '_') || fallback
}

function SessionLogPanel({ admin }: { admin: boolean }) {
  const [host, setHost] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState<SessionLogPage>({ items: [], next_cursor: null })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<SessionLog>()
  const [detail, setDetail] = useState<SessionLogDetail>()
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [detailVersion, setDetailVersion] = useState(0)
  const [downloading, setDownloading] = useState<string>()
  const [downloadError, setDownloadError] = useState('')
  const [managementOpen, setManagementOpen] = useState(false)
  const generation = useRef({ value: 0 })
  const read = useCallback(async (before?: string) => {
    const current = ++generation.current.value
    setLoading(true); setError('')
    if (!before) setPage({ items: [], next_cursor: null })
    try {
      const { data } = await api.get<SessionLogPage>(`${endpoint}/session-logs`, { params: { ...(query ? { host: query } : {}), limit: 50, before: before || 0 } })
      if (generation.current.value === current) setPage(previous => ({ items: before ? [...previous.items, ...data.items] : data.items, next_cursor: data.next_cursor }))
    } catch (reason) { if (generation.current.value === current) setError(errorText(reason, '会话日志读取失败，请重试')) }
    finally { if (generation.current.value === current) setLoading(false) }
  }, [query])
  useEffect(() => { const tracker = generation.current; void read(); return () => { tracker.value++ } }, [read])
  const selectedId = selected?.id
  useEffect(() => {
    setDetail(undefined); setDetailError('')
    if (!selectedId) return
    let disposed = false
    setDetailLoading(true)
    void api.get<SessionLogDetail>(`${endpoint}/session-logs/${encodeURIComponent(selectedId)}`).then(({ data }) => {
      if (!disposed) setDetail(data)
    }).catch(reason => { if (!disposed) setDetailError(errorText(reason, '日志内容读取失败，请重试')) }).finally(() => { if (!disposed) setDetailLoading(false) })
    return () => { disposed = true }
  }, [selectedId, detailVersion])
  const download = async (item: SessionLog) => {
    if (downloading) return
    setDownloading(item.id); setDownloadError('')
    try {
      const response = await api.get<Blob>(`${endpoint}/session-logs/${encodeURIComponent(item.id)}/download`, { responseType: 'blob' })
      const url = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      try {
        link.href = url
        link.download = downloadFilename(String(response.headers?.['content-disposition'] || ''), item.filename)
        document.body.appendChild(link); link.click()
      } finally { link.remove(); URL.revokeObjectURL(url) }
    } catch (reason) { setDownloadError(`${item.filename}：${errorText(reason, '下载失败，请重试')}`) }
    finally { setDownloading(undefined) }
  }
  return <section className="ssh-panel" aria-label="会话日志">
    <div className="ssh-panel-heading"><div><h2>会话日志</h2><p>{admin ? '所有用户的连接记录，一次连接一个日志文件。' : '自己的连接记录，一次连接一个日志文件。'}</p></div><Button onClick={() => setManagementOpen(true)}>管理记录</Button></div>
    <form className="ssh-audit-filters ssh-log-filters" onSubmit={event => { event.preventDefault(); const next = host.trim(); if (next === query) void read(); else setQuery(next) }}>
      <Input aria-label="日志设备地址" placeholder="按设备地址筛选" value={host} onChange={event => setHost(event.target.value)} allowClear />
      <Button htmlType="submit" loading={loading}>查询</Button><Button disabled={loading} onClick={() => void read()}>刷新</Button>
    </form>
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => void read()}>重试</Button>} />}
    {!selected && downloadError && <Alert type="error" showIcon message={downloadError} />}
    <Table<SessionLog> className="ssh-log-table" rowKey="id" size="small" dataSource={page.items} loading={loading} pagination={false} tableLayout="fixed" locale={{ emptyText: '暂无会话日志' }} columns={[
      { title: '日志文件', key: 'file', render: (_, item) => <div><button className="ssh-log-file" type="button" onClick={() => { setDownloadError(''); setSelected(item) }}><FileTextOutlined aria-hidden="true" /><span>{item.filename}</span></button><div className="ssh-log-meta">{item.host}{item.port !== 22 ? `:${item.port}` : ''} · {dateLabel(item.started_at)} · {sourceLabel(item.source_app)}{admin && <span> · 用户 {item.actor_id}</span>}</div></div> },
      { title: '状态', dataIndex: 'status', key: 'status', width: 70, render: statusLabel },
      { title: '下载', key: 'download', width: 56, render: (_, item) => <Tooltip title="下载日志"><Button type="text" size="small" icon={<DownloadOutlined />} aria-label={`下载 ${item.filename}`} disabled={!!downloading && downloading !== item.id} loading={downloading === item.id} onClick={() => void download(item)} /></Tooltip> },
    ]} />
    {page.next_cursor && <Button className="ssh-load-more" loading={loading} onClick={() => void read(page.next_cursor!)}>加载更多</Button>}
    <Modal open={!!selected} title={selected?.filename || '会话日志'} width="min(1000px, calc(100vw - 32px))" onCancel={() => setSelected(undefined)} destroyOnClose footer={[
      <Button key="close" onClick={() => setSelected(undefined)}>关闭</Button>,
      <Button key="refresh" disabled={detailLoading} onClick={() => setDetailVersion(value => value + 1)}>刷新内容</Button>,
      <Button key="download" type="primary" icon={<DownloadOutlined />} loading={downloading === selected?.id && !!selected} disabled={!!downloading && downloading !== selected?.id} onClick={() => { if (selected) void download(selected) }}>下载日志</Button>,
    ]}>
      {selected && <p className="ssh-modal-note">{selected.device_name || selected.host} · {selected.host}:{selected.port} · {dateLabel(selected.started_at)} · {sourceLabel(selected.source_app)}</p>}
      {downloadError && <Alert type="error" showIcon message={downloadError} />}
      {detailError && <Alert type="error" showIcon message={detailError} action={<Button size="small" onClick={() => setDetailVersion(value => value + 1)}>重试</Button>} />}
      {detailLoading ? <p role="status" className="ssh-modal-note">正在读取日志…</p> : detail?.id === selectedId && <pre className="ssh-session-log" aria-label="日志内容">{detail?.content}</pre>}
    </Modal>
    <Modal open={managementOpen} title="管理记录" width="min(1000px, calc(100vw - 32px))" onCancel={() => setManagementOpen(false)} footer={<Button onClick={() => setManagementOpen(false)}>关闭</Button>} destroyOnClose>
      {managementOpen && <AuditPanel admin={admin} />}
    </Modal>
  </section>
}

function CommandPolicyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [policy, setPolicy] = useState<CommandPolicy>()
  const [patterns, setPatterns] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let disposed = false
    setLoading(true); setPolicy(undefined); setError('')
    void api.get<CommandPolicy>(`${endpoint}/command-policy`).then(({ data }) => {
      if (!disposed) { setPolicy(data); setPatterns(data.patterns.join('\n')) }
    }).catch(reason => { if (!disposed) setError(errorText(reason, '白名单读取失败')) }).finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [open])
  const save = async () => {
    setSaving(true); setError('')
    try {
      await api.put<CommandPolicy>(`${endpoint}/command-policy`, { patterns: patterns.split(/\r?\n/).filter(line => line.trim()), version: policy!.version })
      message.success('命令白名单已保存'); onClose()
    } catch (reason) { setError(errorText(reason, '白名单保存失败')) }
    finally { setSaving(false) }
  }
  return <Modal open={open} title="全局命令白名单" okText="保存" cancelText="取消" onCancel={onClose} onOk={() => void save()} confirmLoading={saving} okButtonProps={{ disabled: loading || !policy }} destroyOnClose>
    <p className="ssh-modal-note">每行一个正则表达式，完整命令匹配任一规则即可执行。规则对终端、批量执行和应用调用统一生效，保存后立即使用。终端在按回车时检查提示符后的整行，设备缩写（如 dis）也须被规则覆盖。白名单用于防误操作，只读边界请用设备账号角色或 TACACS 命令授权。</p>
    <p className="ssh-modal-note"><code>.*</code> 允许全部命令；清空后禁止所有命令。</p>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
    <Input.TextArea aria-label="命令白名单规则" value={patterns} onChange={event => setPatterns(event.target.value)} rows={8} disabled={loading || !policy} spellCheck={false} placeholder="例如 ^display .*$" />
    {policy && <p className="ssh-modal-note">版本 {policy.version}{policy.updated_at ? ` · ${dateLabel(policy.updated_at)}` : ''}{policy.updated_by ? ` · 更新人 ${policy.updated_by}` : ''}</p>}
  </Modal>
}

function BatchPanel({ selected }: { selected: SavedSession[] }) {
  const [commands, setCommands] = useState('')
  const [run, setRun] = useState<BatchRun>()
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  const [pollVersion, setPollVersion] = useState(0)
  const request = useRef<{ signature: string; id: string }>()
  const running = activeBatch(run?.status)
  const targets = running && run?.results.length ? run.results : selected
  useEffect(() => {
    if (!run?.id || !running) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const { data } = await api.get<BatchRun>(`${endpoint}/batches/${run.id}`)
        if (disposed) return
        setRun(data); setError('')
        if (activeBatch(data.status)) timer = setTimeout(() => void poll(), 1500)
      } catch (reason) { if (!disposed) setError(errorText(reason, '结果读取失败，请刷新结果')) }
    }
    timer = setTimeout(() => void poll(), 1500)
    return () => { disposed = true; clearTimeout(timer) }
  }, [run?.id, running, pollVersion])
  const start = async () => {
    const lines = commands.split(/\r?\n/).filter(line => line.trim())
    if (!selected.length || !lines.length || starting || running) return
    if (selected.length > 20 || lines.length > 50) { setError('每批最多选择 20 个会话、输入 50 条命令。'); return }
    const body = { session_ids: selected.map(row => row.id), commands: lines }
    const signature = JSON.stringify(body)
    if (request.current?.signature !== signature) request.current = { signature, id: crypto.randomUUID() }
    setStarting(true); setError('')
    try {
      const { data } = await api.post<BatchRun>(`${endpoint}/batches`, { request_id: request.current.id, ...body })
      request.current = undefined; setRun(data)
    } catch (reason) { setError(errorText(reason, '批量执行未启动')) }
    finally { setStarting(false) }
  }
  const refresh = async () => {
    if (!run) return
    try { const { data } = await api.get<BatchRun>(`${endpoint}/batches/${run.id}`); setRun(data); setError(''); setPollVersion(value => value + 1) }
    catch (reason) { setError(errorText(reason, '结果读取失败')) }
  }
  const stop = async () => {
    if (!run) return
    setStopping(true); setError('')
    try { const { data } = await api.post<BatchRun>(`${endpoint}/batches/${run.id}/cancel`); setRun(data); setPollVersion(value => value + 1) }
    catch (reason) { setError(errorText(reason, '停止失败')) }
    finally { setStopping(false) }
  }
  return <section className="ssh-panel" aria-label="批量执行">
    <div className="ssh-panel-heading"><div><h2>批量执行</h2><p>在左侧勾选会话，按下列顺序逐台执行。每行一条完整命令，每批最多 20 个会话、50 条命令。</p></div></div>
    <div className="ssh-batch-targets">{targets.length ? targets.map(row => `${row.name} (${row.host})`).join(' → ') : '尚未选择会话'}</div>
    <Input.TextArea aria-label="批量命令" value={commands} onChange={event => setCommands(event.target.value)} rows={6} spellCheck={false} disabled={starting || running} placeholder={'display version\ndisplay interface brief'} />
    <div className="ssh-panel-actions">
      <Button type="primary" loading={starting} disabled={!selected.length || !commands.trim() || running} onClick={() => void start()}>开始执行</Button>
      {running && <Button danger loading={stopping} disabled={run?.cancel_requested || run?.status === 'cancel_requested'} onClick={() => void stop()}>停止执行</Button>}
      {run && <><Button onClick={() => void refresh()}>刷新结果</Button><span role="status">{statusLabel(run.cancel_requested && running ? 'cancel_requested' : run.status)}</span></>}
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    {run?.notice && <Alert type="warning" showIcon message={run.notice} />}
    {run && <div className="ssh-results" aria-label="批量执行结果">
      {!run.results.length && <p className="ssh-modal-note">等待设备执行结果…</p>}
      {run.results.map(result => <details className="ssh-result" key={result.session_id}>
        <summary><strong>{result.name || result.host}</strong><span>{result.host}</span><span className={`ssh-result-status ssh-status-${result.status}`}>{statusLabel(result.status)}</span></summary>
        {result.error && <p className="ssh-result-error">{result.error}</p>}
        {result.commands.map((item, index) => <div className="ssh-command-result" key={`${index}:${item.command}`}>
          <div><code>{item.command}</code><span className={`ssh-result-status ssh-status-${item.status}`}>{statusLabel(item.status)}</span></div>
          {item.output && <pre className="ssh-output">{item.output}</pre>}{item.truncated && <p className="ssh-modal-note">输出过长，此处仅显示前半部分；可在操作记录中查看。</p>}{item.error && <p className="ssh-result-error">{item.error}</p>}
        </div>)}
      </details>)}
    </div>}
  </section>
}

function AuditDetails({ detail }: { detail: Record<string, unknown> }) {
  const { command, output, ...metadata } = detail
  return <div className="ssh-audit-detail">
    {typeof command === 'string' && <><span className="ssh-detail-label">命令</span><code className="ssh-audit-command">{command}</code></>}
    {typeof output === 'string' && <><span className="ssh-detail-label">输出</span><pre className="ssh-output">{output || '（无输出）'}</pre></>}
    {detail.output_truncated === true && <p className="ssh-modal-note">输出超过保留上限，以上仅为部分结果。</p>}
    <details className="ssh-audit-metadata"><summary>其他详情</summary><pre>{JSON.stringify(metadata, null, 2)}</pre></details>
  </div>
}

function AuditPanel({ admin }: { admin: boolean }) {
  const [filters, setFilters] = useState({ host: '', kind: '', status: '' })
  const [query, setQuery] = useState({ host: '', kind: '', status: '' })
  const [page, setPage] = useState<AuditPage>({ items: [], next_cursor: null })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef({ value: 0 })
  const read = useCallback(async (before?: string) => {
    const current = ++generation.current.value
    setLoading(true); setError('')
    if (!before) setPage({ items: [], next_cursor: null })
    const params = Object.fromEntries(Object.entries(query).filter(([, value]) => value.trim()))
    try {
      const { data } = await api.get<AuditPage>(`${endpoint}/audit`, { params: { ...params, management_only: true, limit: 50, ...(before ? { before } : {}) } })
      if (generation.current.value === current) setPage(previous => ({ items: before ? [...previous.items, ...data.items] : data.items, next_cursor: data.next_cursor }))
    } catch (reason) { if (generation.current.value === current) setError(errorText(reason, '操作记录读取失败')) }
    finally { if (generation.current.value === current) setLoading(false) }
  }, [query])
  useEffect(() => { const tracker = generation.current; void read(); return () => { tracker.value++ } }, [read])
  return <section className="ssh-panel" aria-label="管理记录">
    <div className="ssh-panel-heading"><div><p>{admin ? '查看所有用户的 SSH 管理操作。' : '查看自己的 SSH 管理操作。'}</p></div></div>
    <form className="ssh-audit-filters" onSubmit={event => { event.preventDefault(); setQuery({ ...filters }) }}>
      <Input aria-label="筛选设备地址" placeholder="设备地址" value={filters.host} onChange={event => setFilters(current => ({ ...current, host: event.target.value }))} />
      <AutoComplete aria-label="筛选操作类型" placeholder="操作类型" allowClear value={filters.kind} options={Object.entries(actions).filter(([value]) => /^(session_|default_|host_key_|policy_|batch_)/.test(value)).map(([value, label]) => ({ value, label }))} filterOption={(input, option) => `${option?.label} ${option?.value}`.toLowerCase().includes(input.toLowerCase())} onChange={value => setFilters(current => ({ ...current, kind: value }))} />
      <AutoComplete aria-label="筛选操作状态" placeholder="状态" allowClear value={filters.status} options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} filterOption={(input, option) => `${option?.label} ${option?.value}`.toLowerCase().includes(input.toLowerCase())} onChange={value => setFilters(current => ({ ...current, status: value }))} />
      <Button htmlType="submit" loading={loading}>查询</Button><Button disabled={loading} onClick={() => void read()}>刷新</Button>
    </form>
    {error && <Alert type="error" showIcon message={error} />}
    {!loading && !page.items.length && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作记录" />}
    <div className="ssh-audit-list" aria-label="操作记录列表" aria-live="polite">{page.items.map(item => <details className="ssh-result" key={item.id}>
      <summary><time>{dateLabel(item.created_at)}</time><strong>{actionLabel(item.action)}</strong><span>{String(item.detail.host || '—')}</span><span className={`ssh-result-status ssh-status-${item.status}`}>{statusLabel(item.status)}</span>{admin && <span>用户 {item.user_id}</span>}</summary>
      <AuditDetails detail={item.detail} />
    </details>)}</div>
    {page.next_cursor && <Button className="ssh-load-more" loading={loading} onClick={() => void read(page.next_cursor!)}>加载更多</Button>}
  </section>
}

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
  const admin = user?.role === 'admin'
  const [sessions, setSessions] = useState<SavedSession[]>([])
  const [defaults, setDefaults] = useState<DefaultLogin>({ username: null, configured: false })
  const [checked, setChecked] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [tabs, setTabs] = useState<OpenTab[]>([])
  const [active, setActive] = useState<string>()
  const [editing, setEditing] = useState<SavedSession | 'new'>()
  const [dialog, setDialog] = useState<'default' | 'batch'>()
  const [policyOpen, setPolicyOpen] = useState(false)
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
        <div className="ssh-toolbar-actions">{admin && <Button onClick={() => setPolicyOpen(true)}>命令白名单</Button>}<span className="ssh-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span></div>
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
          <Tabs className="ssh-workspace" defaultActiveKey="terminal" items={[
            { key: 'terminal', label: '终端', children: tabs.length ? <Tabs className="ssh-tabs" type="editable-card" hideAdd activeKey={active} onChange={setActive}
            onEdit={(key, action) => { if (action === 'remove' && typeof key === 'string') close(key) }}
            items={tabs.map(tab => ({ key: tab.key, label: tab.session.name, children: <LiveTerminal target={{ session_id: tab.session.id }} height="calc(100dvh - 242px)" /> }))} />
            : <div className="ssh-empty">
              <div className="ssh-eyebrow">SSH 终端</div><h1>打开一个会话</h1>
              <p>在左侧双击会话打开终端，多个终端以标签页并排。没有填写用户名密码的会话使用默认用户名密码；登录失败时会提示重新输入。</p>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessions.length ? '双击会话开始' : '先新建一个会话'}>{!sessions.length && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setEditing('new')}>新建会话</Button>}</Empty>
            </div> },
            { key: 'batch', label: '批量执行', children: <BatchPanel selected={checked.map(id => sessions.find(row => row.id === id)).filter((row): row is SavedSession => !!row)} /> },
            { key: 'audit', label: '操作记录', children: <SessionLogPanel key={`${user?.username}:${user?.role}`} admin={admin} /> },
          ]} />
        </main>
      </div>
      <SessionModal open={!!editing} session={editing === 'new' ? undefined : editing} onClose={() => setEditing(undefined)} onSaved={() => void refresh()} />
      <LoginForm open={dialog === 'default'} title="默认用户名密码" extra="没有单独填写用户名密码的会话和拓扑节点的终端使用这一组。密码加密保存，不会显示。" onClose={() => setDialog(undefined)}
        onSubmit={async values => { await api.put(`${endpoint}/default-login`, values); await refresh() }} />
      <LoginForm open={dialog === 'batch'} title={`修改 ${checked.length} 个会话的用户名密码`} onClose={() => setDialog(undefined)}
        onSubmit={async values => { await api.post(`${endpoint}/sessions/login`, { ...values, session_ids: checked }); await refresh() }} />
      {admin && <CommandPolicyModal open={policyOpen} onClose={() => setPolicyOpen(false)} />}
    </div>
  </ConfigProvider>
}
