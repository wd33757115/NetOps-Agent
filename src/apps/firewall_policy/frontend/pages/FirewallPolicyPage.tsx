import React, { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button, ConfigProvider, Input, Select, Segmented, Switch, Table, Upload, message } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, CheckCircleOutlined, CloseOutlined, CopyOutlined,
  DownloadOutlined, FileExcelOutlined, FileTextOutlined, LoadingOutlined,
  SafetyOutlined, SearchOutlined, UploadOutlined, LockOutlined, FileDoneOutlined, DeploymentUnitOutlined } from '@ant-design/icons'
import { artifactHref, generateFirewallPolicy, generateFromLedger, ledgerAvailable, validatePolicyFile, readRun, listRuns, cancelRun, retryRun, activeRun, runResult, downloadArtifact, downloadTemplate, hasSettings, type FirewallRun, type LedgerDraftInput, type PolicyPlan, type PolicyResult, type PolicyRule } from '../services/firewallPolicy'
import './FirewallPolicyPage.css'
import FirewallChangeTicket from '../components/FirewallChangeTicket'
import FirewallBusinessPaths from '../components/FirewallBusinessPaths'
import PolicySettings from '../components/PolicySettings'
import PolicyFileArtwork from '../components/PolicyFileArtwork'
import LedgerDraft from '../components/LedgerDraft'
import FirewallModes from '../components/FirewallModes'
import { useAuth } from '@netops/context/AuthContext'

type Stage = 'idle' | 'uploading' | 'generating'

function RuleEvidence({ rule, plan }: { rule: PolicyRule; plan: PolicyPlan }) {
  return <div className="fw-evidence">
    {plan.evidence.filter(item => rule.evidence_ids.includes(item.id)).map(item => {
      const request = plan.requests.find(row => row.row === item.row)
      return <div key={item.id}>
        <strong>申请表第 {item.row} 行</strong>
        {plan.include_business_paths && <p>{request?.source_system || '未填写'} → {request?.destination_system || '未填写'}</p>}
        {request && <p className="fw-mono">{request.src_ip} → {request.dst_ip} · {request.proto.toUpperCase()} {request.port}</p>}
        <p><span className="fw-muted">{item.path_source === 'explicit' ? '指定路径' : '拓扑推导路径'}</span><br />{item.path.map(hop => hop.join(' / ')).join(' → ')}</p>
        <div className="fw-segments">{[['源地址归属', item.source_segments], ['目的地址归属', item.destination_segments]].map(([label, segments]) =>
          <div key={String(label)}><span className="fw-muted">{String(label)}</span>{(segments as PolicyPlan['evidence'][number]['source_segments']).map((part, i) =>
            <p key={i}>{part.addresses.join('、')}<br /><span className="fw-muted">{part.owner.join(' / ')} · {part.prefix}</span></p>)}</div>)}</div>
      </div>
    })}
  </div>
}

export default function FirewallPolicyPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const runId = params.get('run') || ''
  const [run, setRun] = useState<FirewallRun>()
  const [history, setHistory] = useState<FirewallRun[]>([])
  const [refresh, setRefresh] = useState(0)
  const retryRequest = useRef(crypto.randomUUID())
  const [file, setFile] = useState<File>()
  // The request comes from an uploaded sheet, or is drafted from the resource ledger when that App is granted.
  const [source, setSource] = useState<'file' | 'ledger'>('file')
  const [ledgerReady, setLedgerReady] = useState(false)
  const [draft, setDraft] = useState<LedgerDraftInput>({ source: '', destination: '' })
  const [topology, setTopology] = useState<File>()
  const [ticket, setTicket] = useState('')
  const [merge, setMerge] = useState(true)
  const [businessPaths, setBusinessPaths] = useState(false)
  const [stage, setStage] = useState<Stage>('idle')
  const [error, setError] = useState('')
  const [result, setResult] = useState<PolicyResult>()
  const [editing, setEditing] = useState(true)
  const [ticketOpen, setTicketOpen] = useState(false)
  const [device, setDevice] = useState('')
  const [view, setView] = useState('策略明细')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const running = useRef(false)
  const busy = stage !== 'idle' || activeRun(run)
  const plan = result?.policy_plan
  const devices = Object.keys(plan?.commands || {})
  const resultVisible = !editing && plan && result
  const rules = plan?.rules.filter(rule => rule.device === device) || []
  const timed = hasSettings(rules)
  const filtered = rules.filter(rule => [rule.source_zone, rule.destination_zone, rule.protocol, ...rule.sources, ...rule.destinations, ...rule.ports].join(' ').toLowerCase().includes(search.toLowerCase()))
  const pick = (next: File, isTopology = false) => {
    try {
      validatePolicyFile(next, isTopology)
      if (isTopology) setTopology(next)
      else setFile(next)
      setError('')
    } catch (err) { setError((err as Error).message) }
    return Upload.LIST_IGNORE
  }
  const generate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (running.current) return
    const fromLedger = source === 'ledger'
    if (!fromLedger && !file) { setError('请先上传策略申请表。'); return }
    if (fromLedger && !(draft.source.trim() && draft.destination.trim())) { setError('请填写源和目的。'); return }
    const id = ticket.trim()
    if (!/^[\p{L}\p{N}_.-]{1,80}$/u.test(id)) { setError('请填写工单号，使用文字、数字、短横线、下划线或点，最多 80 字。'); return }
    running.current = true
    setError('')
    setResult(undefined)
    setTicketOpen(false)
    try {
      const options = { topology, ticket: id, merge, businessPaths }
      const next = fromLedger ? await generateFromLedger({ ...options, draft }, setStage)
        : await generateFirewallPolicy({ ...options, file: file! }, setStage)
      setRun(next); setParams({ run: next.id }); setRefresh(value => value + 1)
    } catch (err) { setError(err instanceof Error ? err.message : '生成失败，请重试。') }
    finally { setStage('idle'); running.current = false }
  }
  useEffect(() => { void ledgerAvailable().then(setLedgerReady).catch(() => setLedgerReady(false)) }, [])
  useEffect(() => {
    let stopped = false
    void listRuns().then(data => { if (!stopped) setHistory(data.items) }).catch(() => {})
    return () => { stopped = true }
  }, [runId, refresh])
  useEffect(() => {
    if (!runId) { setRun(undefined); setResult(undefined); setEditing(true); return }
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    setResult(undefined); setError(''); setEditing(true)
    const poll = async () => {
      try {
        const next = await readRun(runId)
        if (stopped) return
        setRun(next)
        // The history list was read when the run started; keep its status in step with the run.
        setHistory(items => items.map(item => (item.id === next.id ? { ...item, status: next.status } : item)))
        const completed = runResult(next)
        if (completed) {
          setResult(completed); setEditing(false)
          setDevice(Object.keys(completed.policy_plan.commands)[0] || '')
          setSearch(''); setPage(1); setView('策略明细')
          scrollRef.current?.scrollTo({ top: 0 })
        } else if (next.status === 'failed' || next.status === 'cancelled') {
          setError(next.error || '任务未完成')
        }
        if (activeRun(next)) timer = setTimeout(poll, 1500)
      } catch (err) { if (!stopped) { setRun(undefined); setError((err as Error).message) } }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [runId, refresh])
  const download = (key: string) => {
    if (result) void downloadArtifact(result, key).catch(err => message.error(err.message))
  }
  const retry = async () => {
    if (!run || running.current) return
    running.current = true
    try {
      const next = await retryRun(run.id, retryRequest.current)
      retryRequest.current = crypto.randomUUID()
      setRun(next); setParams({ run: next.id }); setRefresh(value => value + 1)
    } catch (err) { setError((err as Error).message) }
    finally { running.current = false }
  }
  const copyCommands = async () => {
    try { await navigator.clipboard.writeText(plan?.commands[device] || ''); message.success('命令已复制') }
    catch { message.error('无法访问剪贴板，请选择命令文本复制。') }
  }

  return <ConfigProvider theme={{ token: { colorPrimary: '#1671e8', borderRadius: 9, colorText: '#242b38', colorBorder: '#dce1e9', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 40 } }}>
    <div className="fw-app" ref={scrollRef}>
      <header className="fw-toolbar">
        <div className="fw-breadcrumb"><Link className="fw-back" to="/workbench"><ArrowLeftOutlined aria-hidden="true" /> 工作台</Link><span className="fw-breadcrumb-divider">/</span><span className="fw-toolbar-title">防火墙策略</span><FirewallModes current="policy" /></div>
        <div className="fw-toolbar-right"><Select aria-label="历史策略任务" placeholder="历史策略任务" style={{ width: 250 }} value={runId || undefined} onChange={id => { setRun(undefined); setParams({ run: id }) }} options={history.map(item => ({ value: item.id, label: `${item.input.ticket_id} · ${item.status === 'completed' ? '已完成' : item.status === 'failed' ? '失败' : item.status === 'cancelled' ? '已取消' : '处理中'}` }))} /><Button onClick={() => { setParams({}); setRun(undefined); setResult(undefined); setEditing(true) }}>新建策略</Button><span className="fw-account" title={user?.username} aria-label={`当前用户 ${user?.username || ''}`}>{user?.username?.slice(0, 1).toUpperCase() || 'N'}</span></div>
      </header>
      <div className="fw-window">
      <aside className="fw-sidebar">
        <div className="fw-product"><span className="fw-product-icon"><SafetyOutlined aria-hidden="true" /></span><div><strong>防火墙策略</strong><span>NETOPS WORKSPACE</span></div></div>
        <div className="fw-nav-label">工作流程</div>
        <nav className="fw-workflow" aria-label="策略生成步骤">
          <button type="button" className={editing ? 'active' : ''} aria-current={editing ? 'step' : undefined} onClick={() => setEditing(true)} disabled={busy}><UploadOutlined aria-hidden="true" /><span><strong>导入申请</strong><small>文件与生成选项</small></span><em>01</em></button>
          <button type="button" className={!editing ? 'active' : ''} aria-current={!editing ? 'step' : undefined} disabled={!result || busy} onClick={() => setEditing(false)}><DeploymentUnitOutlined aria-hidden="true" /><span><strong>核对策略</strong><small>访问关系与设备配置</small></span><em>02</em></button>
          <button type="button" disabled={!result || busy} onClick={() => { setEditing(false); setTicketOpen(true) }}><FileDoneOutlined aria-hidden="true" /><span><strong>变更工单</strong><small>补充信息与导出</small></span><em>03</em></button>
        </nav>
        {resultVisible && <nav className="fw-devices" aria-label="设备列表"><h2>本次设备 <span>{devices.length}</span></h2>{devices.map(name => <button type="button" key={name} className={device === name ? 'active' : ''} aria-current={device === name ? 'true' : undefined} onClick={() => { setDevice(name); setPage(1); setSearch('') }}><SafetyOutlined aria-hidden="true" /><span>{name}</span><small>{plan.rules.filter(rule => rule.device === name).length}</small></button>)}</nav>}
        <div className="fw-sidebar-bottom"><div className="fw-current-file"><FileExcelOutlined aria-hidden="true" /><span>{source === 'ledger' ? '按资源台账生成申请表' : file?.name || '尚未选择申请表'}</span></div><div className="fw-local"><LockOutlined aria-hidden="true" /><div><strong>本地生成</strong><span>申请数据不发送给 AI</span></div></div></div>
      </aside>
      <main className={`fw-main ${resultVisible ? 'has-result' : ''}`}>
        <div className="fw-heading">
          <div><div className="fw-eyebrow">{resultVisible ? '策略工作区 / 02' : '策略工作区 / 01'}</div><h1>{resultVisible ? '核对生成结果' : '新建策略'}</h1>
            <p>{resultVisible ? `${plan.ticket_id} · ${plan.input_name}` : '从一份访问申请，开始本次变更。'}</p></div>
          {resultVisible && <div className="fw-heading-actions"><Button onClick={() => setEditing(true)}>修改申请</Button>
            <Button type="primary" icon={<DownloadOutlined aria-hidden="true" />} onClick={() => download('config_zip')} disabled={!artifactHref(result, 'config_zip')}>下载策略包</Button></div>}
        </div>

        {runId && <div className="fw-run-actions">
          <Button onClick={() => setRefresh(value => value + 1)}>刷新任务状态</Button>
          {activeRun(run) && <Button onClick={() => void cancelRun(runId).then(next => { setRun(next); setRefresh(value => value + 1) }).catch(err => setError(err.message))}>取消任务</Button>}
          {(run?.status === 'failed' || run?.status === 'cancelled') && <Button onClick={() => void retry()}>重试原任务</Button>}
        </div>}
        {!resultVisible ? <div className="fw-compose">
          <form className="fw-form" onSubmit={generate} aria-busy={busy}>
            <fieldset disabled={busy}>
              <div className="fw-section-label"><h2>策略申请表</h2>
                {source === 'file' && <Button type="link" onClick={() => void downloadTemplate().catch(err => message.error(err.message))}><DownloadOutlined aria-hidden="true" /> 下载导入模板</Button>}</div>
              {ledgerReady && <Segmented className="fw-source" block value={source} onChange={value => { setSource(value as 'file' | 'ledger'); setError('') }}
                options={[{ value: 'file', label: '上传申请表' }, { value: 'ledger', label: '按资源台账生成' }]} />}
              {source === 'ledger' ? <LedgerDraft value={draft} onChange={setDraft} disabled={busy} /> : <Upload.Dragger accept=".xlsx" multiple={false} maxCount={1} showUploadList={false} beforeUpload={next => pick(next)} disabled={busy} className={file ? 'fw-upload has-file' : 'fw-upload'}>
                <PolicyFileArtwork selected={Boolean(file)} />
                <strong>{file?.name || '将申请表拖到这里'}</strong>
                <p>{file ? `${(file.size / 1024).toFixed(1)} KB · 点击替换文件` : '或点击选择文件 · XLSX，最大 10 MB'}</p>
                <span className="fw-select-file">{file ? '更换文件' : '选择文件'}<ArrowRightOutlined aria-hidden="true" /></span>
              </Upload.Dragger>}
              <label className="fw-field" htmlFor="fw-ticket"><span>工单号</span><Input id="fw-ticket" value={ticket} onChange={e => setTicket(e.target.value)} maxLength={80} placeholder="例如 CHG-20260909-001" disabled={busy} autoComplete="off" /></label>
              <details className="fw-options"><summary>更多选项<span>{merge ? '等价合并' : '保留原规则'} · {topology ? '自定义拓扑' : '默认拓扑'}</span></summary>
                <div className="fw-option-row"><div><label htmlFor="fw-merge">合并等价策略</label><p>仅在允许范围完全一致时合并。</p></div><Switch id="fw-merge" checked={merge} onChange={setMerge} disabled={busy} aria-label="合并等价策略" /></div>
                <div className="fw-option-row"><div><label htmlFor="fw-business-paths">业务系统与拓扑路径说明</label><p>读取源、目的业务系统，展示访问关系并加入下载预览。</p></div><Switch id="fw-business-paths" checked={businessPaths} onChange={setBusinessPaths} disabled={busy} aria-label="业务系统与拓扑路径说明" /></div>
                <div className="fw-option-row"><div><strong>网络拓扑</strong><p>{topology?.name || '使用系统配置的拓扑和安全域。'}</p></div>
                  <div className="fw-topology-actions"><Upload accept=".json" showUploadList={false} beforeUpload={next => pick(next, true)} disabled={busy}><Button size="small" disabled={busy}>选择 JSON</Button></Upload>
                    {topology && <Button type="text" size="small" disabled={busy} icon={<CloseOutlined aria-hidden="true" />} aria-label="移除自定义拓扑" onClick={() => setTopology(undefined)} />}</div></div>
              </details>
            </fieldset>
            {error && <div className="fw-error" role="alert"><strong>暂时无法生成</strong><p>{error}</p></div>}
            <div className="fw-form-footer"><span className="fw-muted"><SafetyOutlined aria-hidden="true" /> 生成后可逐条核对</span><Button type="primary" htmlType="submit" size="large" loading={busy} icon={!busy && <ArrowRightOutlined aria-hidden="true" />} disabled={(source === 'ledger' ? !(draft.source.trim() && draft.destination.trim()) : !file) || !ticket.trim()}>{busy ? '正在处理' : '生成策略'}</Button></div>
            {busy && <div className="fw-progress" role="status"><LoadingOutlined aria-hidden="true" spin /><div><strong>{stage === 'uploading' ? '正在上传文件…' : '正在计算策略并校验生成命令…'}</strong><p>任务已保存，可关闭页面，稍后从历史任务继续查看。</p></div></div>}
            {result && <Button type="link" className="fw-return" onClick={() => setEditing(false)}>返回上次生成结果</Button>}
          </form>
          {source === 'ledger' ? <aside className="fw-guide"><span className="fw-guide-kicker">按资源台账生成</span><h2>说清楚谁访问谁，地址和端口从台账里取。</h2>
            <p>源或目的写业务系统名称时，取它在用（未下线）主机的 IP，可以再限定到某个子系统；目的端口取台账里主机登记的服务端口，也可以自己指定。台账里查不到的地址照常放行，预览里会说明。</p>
            <dl><div><dt><DeploymentUnitOutlined aria-hidden="true" /> 防火墙与安全域</dt><dd>下发到哪台防火墙、哪个安全域，生成策略时按拓扑计算，和上传申请表时一样。</dd></div>
              <div><dt><FileTextOutlined aria-hidden="true" /> 有效期与长连接</dt><dd>结束只写日期表示当天结束；长连接只对 TCP 生效，0 为永不老化。目前支持华为 USG6000 与 H3C F5000。</dd></div></dl>
            <div className="fw-guide-bottom"><FileDoneOutlined aria-hidden="true" /><div><strong>生成之后</strong><span>申请表随任务保存，可在结果页下载；核对策略后再生成变更工单。</span></div></div>
          </aside> : <aside className="fw-guide"><span className="fw-guide-kicker">申请表指南</span><h2>填写每一组访问关系。</h2><p>参考模板中的示例填写。限时开通时同时填写 start_time 和 end_time，结束只写日期表示当天结束；长连接在 long_link 填老化小时数（0 为永不老化），或填“是”（168 小时）。有效期和长连接目前支持华为 USG6000 与 H3C F5000。</p>
            <figure className="fw-sample-sheet"><figcaption><FileExcelOutlined aria-hidden="true" /><span>policies.xlsx</span><small>示例</small></figcaption><table><thead><tr><th>src_ip</th><th>dst_ip</th><th>port</th><th>proto</th></tr></thead><tbody><tr><td>10.0.0.1</td><td>10.1.0.8</td><td>443</td><td>tcp</td></tr><tr><td>10.0.0.2</td><td>10.1.0.8</td><td>80,443</td><td>tcp</td></tr></tbody></table></figure>
            <dl><div><dt><DeploymentUnitOutlined aria-hidden="true" /> 地址与网段</dt><dd>支持 IP、CIDR 网段及地址范围。多个地址用英文逗号分隔。</dd></div><div><dt><FileTextOutlined aria-hidden="true" /> 端口与协议</dt><dd>支持单端口、端口范围与 TCP / UDP。ICMP 无需填写端口。</dd></div></dl>
            <div className="fw-guide-bottom"><FileDoneOutlined aria-hidden="true" /><div><strong>准备好之后</strong><span>核对设备策略，下载配置包，再生成变更工单。</span></div></div>
          </aside>}
        </div> : <div className="fw-result">
          <div className="fw-result-summary"><span className="fw-complete"><CheckCircleOutlined aria-hidden="true" /> 已生成</span><span><b>{devices.length}</b> 台设备</span><span><b>{plan.optimization.after}</b> 条策略</span>
            <span className="fw-muted">{plan.optimization.status === 'verified' ? `${plan.optimization.before} → ${plan.optimization.after} 条 · 合并等价校验通过` : plan.optimization.status === 'disabled' ? '未启用合并' : '保留原规则，未进行合并'}</span></div>
          {plan.optimization.reason && <p className="fw-result-note">{plan.optimization.reason}</p>}
          {plan.warnings.length > 0 && <details className="fw-warnings"><summary>有 {plan.warnings.length} 条拓扑提示，建议核对路径</summary><ul>{plan.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
          {plan.include_business_paths && <FirewallBusinessPaths plan={plan} />}
          <div className="fw-review">
            <section className="fw-device-detail" aria-label={`${device} 生成结果`}>
              <div className="fw-device-heading"><h2>{device}</h2><Segmented options={['策略明细', '配置命令']} value={view} onChange={setView} /></div>
              {view === '策略明细' ? <>
                <div className="fw-table-tools"><span className="fw-muted">展开策略，核对来源和路径</span><Input aria-label="搜索策略" placeholder="搜索地址、端口或安全域" prefix={<SearchOutlined aria-hidden="true" />} value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} allowClear /></div>
                <Table<PolicyRule> size="middle" rowKey="id" dataSource={filtered} scroll={{ x: 720 }}
                  pagination={{ current: page, onChange: setPage, pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
                  locale={{ emptyText: '没有匹配的策略' }} expandable={{ expandedRowRender: rule => <RuleEvidence rule={rule} plan={plan} />, rowExpandable: rule => rule.evidence_ids.length > 0 }}
                  columns={[
                    { title: '源地址 / 安全域', key: 'source', render: (_, rule) => <div className="fw-address"><span>{rule.sources.join('\n')}</span><small>{rule.source_zone}</small></div> },
                    { title: '目的地址 / 安全域', key: 'destination', render: (_, rule) => <div className="fw-address"><span>{rule.destinations.join('\n')}</span><small>{rule.destination_zone}</small></div> },
                    { title: '服务', key: 'service', width: 155, render: (_, rule) => <div className="fw-address"><span>{rule.protocol.toUpperCase()}</span><small>{rule.ports.join(', ') || '无需端口'}</small></div> },
                    ...(timed ? [{ title: '有效期 / 长连接', key: 'settings', width: 196, render: (_: unknown, rule: PolicyRule) => <PolicySettings item={rule} /> }] : []),
                    { title: '动作', key: 'action', width: 64, render: () => <span className="fw-permit">放行</span> },
                  ]} />
              </> : <div className="fw-commands"><div><span>生成的设备配置</span><Button size="small" icon={<CopyOutlined aria-hidden="true" />} onClick={copyCommands}>复制命令</Button></div><pre tabIndex={0} aria-label="设备配置命令"><code>{plan.commands[device]}</code></pre></div>}
            </section>
          </div>
          <FirewallChangeTicket key={result.run_id} open={ticketOpen} onOpenChange={setTicketOpen}
            source={{ run_id: result.run_id, ticket_id: plan.ticket_id, devices, summary: `${devices.length} 台设备 · ${plan.rules.length} 条策略`, manifest: result.manifest || {} }} />
          <footer className="fw-result-footer"><span>需要调整？修改申请表后重新生成。</span><div>
            <Button type="text" icon={<FileExcelOutlined aria-hidden="true" />} onClick={() => download('policy_request')} disabled={!artifactHref(result, 'policy_request')}>下载申请表</Button>
            <Button type="text" icon={<DownloadOutlined aria-hidden="true" />} onClick={() => download('policy_preview')} disabled={!artifactHref(result, 'policy_preview')}>下载完整预览</Button>
          </div></footer>
          {!artifactHref(result, 'config_zip') && <p className="fw-error" role="alert">配置已生成，但下载文件未能保存。可以先复制命令，或稍后重新生成以获取策略包。</p>}
        </div>}
        <div className="fw-bottom-note">{resultVisible ? '配置仅生成，尚未下发到设备。' : '历史任务保存申请、策略计划与变更工单，可随时返回核对。'}</div>
      </main>
      </div>
    </div>
  </ConfigProvider>
}
