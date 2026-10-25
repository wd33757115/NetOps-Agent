import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button, ConfigProvider, Input, InputNumber, Select, Segmented, Switch, Table, Upload, message } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, CheckCircleOutlined, ClockCircleOutlined, CloseOutlined, CopyOutlined,
  DeploymentUnitOutlined, DownloadOutlined, FileDoneOutlined, FileExcelOutlined, FileTextOutlined, LoadingOutlined,
  LockOutlined, PlusOutlined, SafetyOutlined, StopOutlined, UploadOutlined } from '@ant-design/icons'
import { activeRun, artifactHref, blockDevices, blockState, BLOCK_VENDORS, cancelRun, downloadArtifact, downloadBlockTemplate,
  generateBlock, listBlockRuns, readBlockRun, retryRun, validateBlockFile,
  type BlockDevice, type BlockResult, type BlockRun, type BlockVendor } from '../services/firewallPolicy'
import './FirewallPolicyPage.css'
import FirewallChangeTicket from '../components/FirewallChangeTicket'
import FirewallModes from '../components/FirewallModes'
import PolicyFileArtwork from '../components/PolicyFileArtwork'
import { useAuth } from '@netops/context/AuthContext'

type Stage = 'idle' | 'uploading' | 'generating'
type View = '地址本' | '封禁命令' | '清理命令'
type Known = { name: string; vendor: string; supported: boolean }

const vendorLabel = (vendor: string) => BLOCK_VENDORS.find(item => item.value === vendor)?.label || vendor
const numberText = (value: number) => value.toLocaleString('zh-CN')

/** Rows that were not blocked, whitelist removals and notes, each opened on demand. */
function BlockChecks({ result }: { result: BlockResult }) {
  const errors = result.issues.filter(item => item.level === 'error')
  const warnings = result.issues.filter(item => item.level === 'warning')
  const removals = result.whitelist.filter(item => item.removed > 0)
  const { summary } = result
  const removedRows = summary.partial_rows + summary.whitelisted_rows
  return <div className="fw-block-checks">
    <p className="fw-block-tally"><span><b>{numberText(summary.rows)}</b> 行名单</span><span><b>{numberText(summary.blocked_rows + summary.partial_rows)}</b> 行已封禁</span>
      {removedRows > 0 && <span><b>{numberText(removedRows)}</b> 行被白名单剔除</span>}
      <span className={summary.error_rows ? 'is-error' : ''}><b>{numberText(summary.error_rows)}</b> 行未封禁</span></p>
    {errors.length > 0 && <details className="fw-block-check is-error" open>
      <summary>{summary.error_rows} 行没有封禁：地址无效、已过期或范围过大。修正名单后重新导入即可。</summary>
      <ul>{errors.map(item => <li key={`${item.sheet}-${item.row}`}><span>第 {item.row} 行</span><code>{item.value || '（空）'}</code><em>{item.message}</em></li>)}</ul>
    </details>}
    {removals.length > 0 && <details className="fw-block-check">
      <summary>白名单剔除 {numberText(summary.removed_addresses)} 个地址，涉及 {removedRows} 行名单</summary>
      <ul>{removals.map(item => <li key={`${item.sheet}-${item.row}`}><span>{item.sheet}第 {item.row} 行</span><code>{item.value}</code>
        <em>剔除 {numberText(item.removed)} 个地址 · 名单第 {item.rows.join('、')} 行{item.row_total > item.rows.length ? ` 等 ${item.row_total} 行` : ''}</em></li>)}</ul>
    </details>}
    {warnings.length > 0 && <details className="fw-block-check is-warning">
      <summary>{summary.warning_rows} 行有提示，已照常封禁</summary>
      <ul>{warnings.map((item, index) => <li key={index}><span>第 {item.row} 行</span><code>{item.value}</code><em>{item.message}</em></li>)}</ul>
    </details>}
    {result.issue_total > result.issues.length && <p className="fw-muted">页面只列出前 {result.issues.length} 条，完整的逐行说明在封禁核对表里。</p>}
  </div>
}

export default function IpBlockPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const runId = params.get('run') || ''
  const [run, setRun] = useState<BlockRun>()
  const [history, setHistory] = useState<BlockRun[]>([])
  const [refresh, setRefresh] = useState(0)
  const retryRequest = useRef(crypto.randomUUID())
  const [file, setFile] = useState<File>()
  const [whitelist, setWhitelist] = useState<File>()
  const [known, setKnown] = useState<Known[]>([])
  const [manual, setManual] = useState<BlockDevice[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [otherName, setOtherName] = useState('')
  const [otherVendor, setOtherVendor] = useState<BlockVendor>('华为')
  const [ticket, setTicket] = useState('')
  const [limit, setLimit] = useState(2000)
  const [allowBroad, setAllowBroad] = useState(false)
  const [stage, setStage] = useState<Stage>('idle')
  const [error, setError] = useState('')
  const [result, setResult] = useState<BlockResult>()
  const [editing, setEditing] = useState(true)
  const [ticketOpen, setTicketOpen] = useState(false)
  const [device, setDevice] = useState('')
  const [view, setView] = useState<View>('地址本')
  const scrollRef = useRef<HTMLDivElement>(null)
  const running = useRef(false)
  const busy = stage !== 'idle' || activeRun(run)
  const resultVisible = !editing && result
  const choices = useMemo<BlockDevice[]>(() => [
    ...known.filter(item => item.supported).map(item => ({ name: item.name, vendor: item.vendor as BlockVendor })), ...manual,
  ], [known, manual])
  const current = result?.devices.find(item => item.name === device)

  const pick = (next: File, isWhitelist = false) => {
    try {
      validateBlockFile(next, isWhitelist)
      if (isWhitelist) setWhitelist(next)
      else setFile(next)
      setError('')
    } catch (err) { setError((err as Error).message) }
    return Upload.LIST_IGNORE
  }
  const addOther = () => {
    const name = otherName.trim()
    if (!/^[\p{L}\p{N}_.-]{1,80}$/u.test(name)) { setError('设备名只能包含文字、数字、短横线、下划线或点，最多 80 字。'); return }
    if (choices.some(item => item.name === name)) { setSelected(items => items.includes(name) ? items : [...items, name]); setOtherName(''); return }
    setManual(items => [...items, { name, vendor: otherVendor }])
    setSelected(items => [...items, name])
    setOtherName(''); setError('')
  }
  const generate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (running.current) return
    if (!file) { setError('请先选择封禁名单。'); return }
    const devices = selected.map(name => choices.find(item => item.name === name)).filter(Boolean) as BlockDevice[]
    if (!devices.length) { setError('请选择要下发封禁的防火墙。'); return }
    const id = ticket.trim()
    if (!/^[\p{L}\p{N}_.-]{1,80}$/u.test(id)) { setError('请填写工单号，使用文字、数字、短横线、下划线或点，最多 80 字。'); return }
    running.current = true
    setError(''); setResult(undefined); setTicketOpen(false)
    try {
      const next = await generateBlock({ file, whitelist, ticket: id, devices, limit, allowBroad }, setStage)
      setRun(next); setParams({ run: next.id }); setRefresh(value => value + 1)
    } catch (err) { setError(err instanceof Error ? err.message : '生成失败，请重试。') }
    finally { setStage('idle'); running.current = false }
  }
  useEffect(() => { void blockDevices().then(setKnown).catch(() => setKnown([])) }, [])
  useEffect(() => {
    let stopped = false
    void listBlockRuns().then(data => { if (!stopped) setHistory(data.items) }).catch(() => {})
    return () => { stopped = true }
  }, [runId, refresh])
  useEffect(() => {
    if (!runId) { setRun(undefined); setResult(undefined); setEditing(true); return }
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    setResult(undefined); setError(''); setEditing(true)
    const poll = async () => {
      try {
        const next = await readBlockRun(runId)
        if (stopped) return
        if (next.kind !== 'block') { setParams({}); return }
        setRun(next)
        setHistory(items => items.map(item => (item.id === next.id ? { ...item, status: next.status, result: next.result } : item)))
        if (next.status === 'completed' && next.result) {
          setResult(next.result); setEditing(false)
          setDevice(next.result.devices[0]?.name || ''); setView('地址本')
          scrollRef.current?.scrollTo({ top: 0 })
        } else if (next.status === 'failed' || next.status === 'cancelled') {
          setError(next.error || '任务未完成')
        }
        if (activeRun(next)) timer = setTimeout(poll, 1500)
      } catch (err) { if (!stopped) { setRun(undefined); setError((err as Error).message) } }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [runId, refresh, setParams])
  const download = (key: string) => {
    if (run) void downloadArtifact(run, key).catch(err => message.error(err.message))
  }
  const retry = async () => {
    if (!run || running.current) return
    running.current = true
    try {
      const next = await retryRun(run.id, retryRequest.current)
      retryRequest.current = crypto.randomUUID()
      setRun(undefined); setParams({ run: next.id }); setRefresh(value => value + 1)
    } catch (err) { setError((err as Error).message) }
    finally { running.current = false }
  }
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); message.success('命令已复制') }
    catch { message.error('无法访问剪贴板，请选择命令文本复制。') }
  }
  const historyLabel = (item: BlockRun) => item.status === 'completed' && item.result ? `${item.input.ticket_id} · ${blockState(item.result)}`
    : `${item.input.ticket_id} · ${item.status === 'failed' ? '失败' : item.status === 'cancelled' ? '已取消' : item.status === 'completed' ? '已完成' : '处理中'}`

  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 9, colorText: '#242b38', colorBorder: '#dce1e9', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 40 } }}>
    <div className="fw-app" ref={scrollRef}>
      <header className="fw-toolbar">
        <div className="fw-breadcrumb"><Link className="fw-back" to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span className="fw-breadcrumb-divider">/</span><span className="fw-toolbar-title">防火墙策略</span><FirewallModes current="block" /></div>
        <div className="fw-toolbar-right"><Select aria-label="历史封禁批次" placeholder="历史封禁批次" style={{ width: 250 }} value={runId || undefined} onChange={id => { setRun(undefined); setParams({ run: id }) }} options={history.map(item => ({ value: item.id, label: historyLabel(item) }))} />
          <Button onClick={() => { setParams({}); setRun(undefined); setResult(undefined); setEditing(true) }}>新建封禁</Button><span className="fw-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span></div>
      </header>
      <div className="fw-window">
        <aside className="fw-sidebar">
          <div className="fw-product"><span className="fw-product-icon is-block"><StopOutlined aria-hidden="true" /></span><div><strong>IP 封禁</strong><span>NETOPS WORKSPACE</span></div></div>
          <div className="fw-nav-label">工作流程</div>
          <nav className="fw-workflow" aria-label="封禁步骤">
            <button type="button" className={editing ? 'active' : ''} aria-current={editing ? 'step' : undefined} onClick={() => setEditing(true)} disabled={busy}><UploadOutlined aria-hidden="true" /><span><strong>导入名单</strong><small>名单、白名单与设备</small></span><em>01</em></button>
            <button type="button" className={!editing ? 'active' : ''} aria-current={!editing ? 'step' : undefined} disabled={!result || busy} onClick={() => setEditing(false)}><DeploymentUnitOutlined aria-hidden="true" /><span><strong>核对结果</strong><small>地址本与设备命令</small></span><em>02</em></button>
            <button type="button" disabled={!result || busy} onClick={() => { setEditing(false); setTicketOpen(true) }}><FileDoneOutlined aria-hidden="true" /><span><strong>变更工单</strong><small>补充信息与导出</small></span><em>03</em></button>
          </nav>
          {resultVisible && <nav className="fw-devices" aria-label="设备列表"><h2>本次设备 <span>{result.devices.length}</span></h2>{result.devices.map(item => <button type="button" key={item.name} className={device === item.name ? 'active' : ''} aria-current={device === item.name ? 'true' : undefined} onClick={() => setDevice(item.name)}><SafetyOutlined aria-hidden="true" /><span>{item.name}</span><small>{vendorLabel(item.vendor)}</small></button>)}</nav>}
          <div className="fw-sidebar-bottom"><div className="fw-current-file"><FileExcelOutlined aria-hidden="true" /><span>{file?.name || (resultVisible ? result.inputs.block : '尚未选择名单')}</span></div><div className="fw-local"><LockOutlined aria-hidden="true" /><div><strong>本地生成</strong><span>名单数据不发送给 AI</span></div></div></div>
        </aside>
        <main className={`fw-main ${resultVisible ? 'has-result' : ''}`}>
          <div className="fw-heading">
            <div><div className="fw-eyebrow">{resultVisible ? 'IP 封禁 / 02' : 'IP 封禁 / 01'}</div><h1>{resultVisible ? '核对封禁结果' : '新建封禁'}</h1>
              <p>{resultVisible ? `${result.ticket_id} · ${result.inputs.block}${result.inputs.whitelist ? ` + ${result.inputs.whitelist}` : ''} · 批次 ${result.batch}` : '导入一份地址名单，在所选防火墙上拒绝这些来源的访问。'}</p></div>
            {resultVisible && run && <div className="fw-heading-actions"><Button onClick={() => setEditing(true)}>修改名单</Button>
              <Button type="primary" icon={<DownloadOutlined aria-hidden="true" />} onClick={() => download('block_zip')} disabled={!artifactHref(run, 'block_zip')}>下载封禁命令包</Button></div>}
          </div>

          {runId && <div className="fw-run-actions">
            <Button onClick={() => setRefresh(value => value + 1)}>刷新任务状态</Button>
            {activeRun(run) && <Button onClick={() => void cancelRun(runId).then(() => setRefresh(value => value + 1)).catch(err => setError(err.message))}>取消任务</Button>}
            {(run?.status === 'failed' || run?.status === 'cancelled') && <Button onClick={() => void retry()}>重试原任务</Button>}
          </div>}
          {!resultVisible ? <div className="fw-compose">
            <form className="fw-form" onSubmit={generate} aria-busy={busy}>
              <fieldset disabled={busy}>
                <div className="fw-section-label"><h2>封禁名单</h2>
                  <Button type="link" onClick={() => void downloadBlockTemplate().catch(err => message.error(err.message))}><DownloadOutlined aria-hidden="true" /> 下载导入模板</Button></div>
                <Upload.Dragger accept=".xlsx,.csv" multiple={false} maxCount={1} showUploadList={false} beforeUpload={next => pick(next)} disabled={busy} className={file ? 'fw-upload has-file' : 'fw-upload'}>
                  <PolicyFileArtwork selected={Boolean(file)} />
                  <strong>{file?.name || '将名单拖到这里'}</strong>
                  <p>{file ? `${(file.size / 1024).toFixed(1)} KB · 点击替换文件` : '或点击选择文件 · XLSX 或 CSV，最大 10 MB'}</p>
                  <span className="fw-select-file">{file ? '更换文件' : '选择文件'}<ArrowRightOutlined aria-hidden="true" /></span>
                </Upload.Dragger>
                <div className="fw-whitelist">
                  <div><strong>白名单</strong><p>{whitelist ? whitelist.name : file?.name.toLowerCase().endsWith('.csv') ? 'CSV 名单的白名单另选一个文件（可选）。' : 'XLSX 名单的“白名单”表会自动读取；也可以另选一个文件。'}</p></div>
                  <div className="fw-topology-actions"><Upload accept=".csv,.xlsx" showUploadList={false} beforeUpload={next => pick(next, true)} disabled={busy}><Button size="small" disabled={busy}>{whitelist ? '更换' : '选择文件'}</Button></Upload>
                    {whitelist && <Button type="text" size="small" disabled={busy} icon={<CloseOutlined aria-hidden="true" />} aria-label="移除白名单文件" onClick={() => setWhitelist(undefined)} />}</div>
                </div>
                <div className="fw-field"><label htmlFor="fw-block-devices">防火墙</label>
                  <Select id="fw-block-devices" mode="multiple" value={selected} onChange={setSelected} placeholder="从拓扑中选择，或在下方添加" optionFilterProp="value" disabled={busy}
                    options={[
                      ...known.map(item => ({ value: item.name, disabled: !item.supported, label: <span className="fw-device-option">{item.name}<small>{item.supported ? vendorLabel(item.vendor) : `${item.vendor || '未知类型'}，暂不支持`}</small></span> })),
                      ...manual.map(item => ({ value: item.name, label: <span className="fw-device-option">{item.name}<small>{vendorLabel(item.vendor)} · 手动添加</small></span> })),
                    ]} />
                  <div className="fw-device-add">
                    <Input value={otherName} onChange={e => setOtherName(e.target.value)} onPressEnter={e => { e.preventDefault(); addOther() }} placeholder="拓扑里没有的设备名" maxLength={80} disabled={busy} aria-label="手动添加的设备名" />
                    <Select value={otherVendor} onChange={setOtherVendor} options={BLOCK_VENDORS} disabled={busy} aria-label="设备类型" />
                    <Button icon={<PlusOutlined aria-hidden="true" />} onClick={addOther} disabled={busy || !otherName.trim()}>添加</Button>
                  </div>
                </div>
                <label className="fw-field" htmlFor="fw-block-ticket"><span>工单号</span><Input id="fw-block-ticket" value={ticket} onChange={e => setTicket(e.target.value)} maxLength={80} placeholder="例如 CHG-20261025-001" disabled={busy} autoComplete="off" /></label>
                <details className="fw-options"><summary>更多选项<span>每个地址本 {limit} 条 · {allowBroad ? '允许大范围' : '单行不超过 /16'}</span></summary>
                  <div className="fw-option-row"><div><label htmlFor="fw-book-limit">地址本条目上限</label><p>超出时拆成多个地址本；设备上限较低时调小。</p></div><InputNumber id="fw-book-limit" min={1} max={2000} precision={0} value={limit} onChange={value => setLimit(value || 2000)} disabled={busy} /></div>
                  <div className="fw-option-row"><div><label htmlFor="fw-allow-broad">允许大范围封禁</label><p>单行超过一个 /16（65536 个地址）时仍然封禁。0.0.0.0/0 始终不封禁。</p></div><Switch id="fw-allow-broad" checked={allowBroad} onChange={setAllowBroad} disabled={busy} aria-label="允许大范围封禁" /></div>
                </details>
              </fieldset>
              {error && <div className="fw-error" role="alert"><strong>暂时无法生成</strong><p>{error}</p></div>}
              <div className="fw-form-footer"><span className="fw-muted"><SafetyOutlined aria-hidden="true" /> 生成后逐行核对</span><Button type="primary" htmlType="submit" size="large" loading={busy} icon={!busy && <ArrowRightOutlined aria-hidden="true" />} disabled={!file || !selected.length || !ticket.trim()}>{busy ? '正在处理' : '生成封禁命令'}</Button></div>
              {busy && <div className="fw-progress" role="status"><LoadingOutlined aria-hidden="true" spin /><div><strong>{stage === 'uploading' ? '正在上传文件…' : '正在合并地址并校验生成命令…'}</strong><p>任务已保存，可关闭页面，稍后从历史批次继续查看。</p></div></div>}
              {result && <Button type="link" className="fw-return" onClick={() => setEditing(false)}>返回上次生成结果</Button>}
            </form>
            <aside className="fw-guide"><span className="fw-guide-kicker">封禁名单指南</span><h2>每行一个来源地址，写上到期时间。</h2>
              <p>名单用 XLSX（“封禁地址”和“白名单”两张表）或 CSV。生成的拒绝策略不写安全域，对所有安全域生效，并移到策略最前面。</p>
              <figure className="fw-sample-sheet"><figcaption><FileExcelOutlined aria-hidden="true" /><span>ip-block.xlsx</span><small>示例</small></figcaption><table><thead><tr><th>IP地址</th><th>到期时间</th><th>备注</th></tr></thead><tbody><tr><td>203.0.113.10</td><td></td><td>永久</td></tr><tr><td>198.51.100.0/24</td><td>2026-12-31</td><td>当天结束</td></tr></tbody></table></figure>
              <dl><div><dt><DeploymentUnitOutlined aria-hidden="true" /> 地址与合并</dt><dd>支持 IP、网段和地址范围。相邻、重叠的地址自动合并；白名单里的地址从网段中扣除。</dd></div>
                <div><dt><ClockCircleOutlined aria-hidden="true" /> 到期时间</dt><dd>留空为永久，只写日期表示当天结束。同一到期时间生成一条策略和一个时间段，到期后策略自动失效。</dd></div>
                <div><dt><FileTextOutlined aria-hidden="true" /> 地址本</dt><dd>每个地址本最多 {limit} 条，超出自动拆分。每次导入新建一批对象，可用清理命令整批删除。</dd></div></dl>
              <div className="fw-guide-bottom"><FileDoneOutlined aria-hidden="true" /><div><strong>生成之后</strong><span>核对地址本、封禁命令和清理命令，再生成变更工单。</span></div></div>
            </aside>
          </div> : <div className="fw-result">
            <div className="fw-result-summary"><span className="fw-complete"><CheckCircleOutlined aria-hidden="true" /> 已生成</span><span><b>{numberText(result.summary.addresses)}</b> 个地址</span><span><b>{numberText(result.summary.members)}</b> 条成员</span><span><b>{result.summary.books}</b> 个地址本</span><span><b>{result.summary.rules}</b> 条拒绝策略</span>
              <span className="fw-muted">{blockState(result)} · 每个地址本最多 {result.book_limit} 条</span></div>
            <BlockChecks result={result} />
            <div className="fw-review">
              {current && <section className="fw-device-detail" aria-label={`${current.name} 生成结果`}>
                <div className="fw-device-heading"><h2>{current.name}<small>{vendorLabel(current.vendor)}</small></h2><Segmented options={['地址本', '封禁命令', '清理命令']} value={view} onChange={value => setView(value as View)} /></div>
                {view === '地址本' ? <Table size="middle" rowKey="name" dataSource={result.books} scroll={{ x: 720 }} pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
                  columns={[
                    { title: '地址本 / 规则', key: 'name', render: (_, book) => <div className="fw-address"><span className="fw-mono">{book.name}</span><small>{book.rule}</small></div> },
                    { title: '到期', key: 'expiry', width: 170, render: (_, book) => <div className="fw-address"><span>{result.groups.find(group => group.expiry === book.expiry)?.label}</span><small>{book.expiry ? '到期后策略失效' : '不设时间段'}</small></div> },
                    { title: '成员', key: 'members', width: 150, render: (_, book) => <div className="fw-book-usage"><span>{numberText(book.members)} / {result.book_limit}</span><i><b style={{ width: `${Math.min(100, (book.members / result.book_limit) * 100)}%` }} /></i></div> },
                    { title: '地址范围', key: 'range', render: (_, book) => <div className="fw-address"><span className="fw-mono">{book.members > 1 ? `${book.first} … ${book.last}` : book.first}</span><small>{numberText(book.addresses)} 个地址</small></div> },
                  ]} />
                  : <div className="fw-commands"><div><span>{view === '封禁命令' ? `${current.lines.toLocaleString('zh-CN')} 行${current.truncated ? '，页面省略了地址本成员' : ''}` : '整批删除本次的策略、地址本和时间段'}</span>
                    {view === '封禁命令' && current.truncated ? <Button size="small" icon={<DownloadOutlined aria-hidden="true" />} onClick={() => download('block_zip')}>下载完整命令</Button>
                      : <Button size="small" icon={<CopyOutlined aria-hidden="true" />} onClick={() => void copy(view === '封禁命令' ? current.preview : current.cleanup)}>复制命令</Button>}</div>
                    <pre tabIndex={0} aria-label={view}><code>{view === '封禁命令' ? current.preview : current.cleanup}</code></pre></div>}
              </section>}
            </div>
            {run && <FirewallChangeTicket key={run.id} open={ticketOpen} onOpenChange={setTicketOpen}
              source={{ run_id: run.id, ticket_id: result.ticket_id, devices: result.devices.map(item => item.name), summary: `${result.devices.length} 台设备 · ${result.summary.books} 个地址本`, manifest: result.ticket }} />}
            <footer className="fw-result-footer"><span>名单有误？修改后重新导入，会生成新的一批对象。</span><div>
              <Button type="text" icon={<FileExcelOutlined aria-hidden="true" />} onClick={() => download('block_report')} disabled={!run || !artifactHref(run, 'block_report')}>下载封禁核对表</Button>
            </div></footer>
          </div>}
          <div className="fw-bottom-note">{resultVisible ? '命令仅生成，尚未下发到设备。到期的策略不再生效，需要时执行清理命令删除。' : '历史批次保存名单、地址本与命令，可随时返回核对。'}</div>
        </main>
      </div>
    </div>
  </ConfigProvider>
}
