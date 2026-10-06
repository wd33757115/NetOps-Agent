import React, { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Input, Modal, Segmented, Select, Table, Tag } from 'antd'
import { CloudUploadOutlined, DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons'
import { discardDraft, publishDraft, readContract, readVersion, saveDraft, type Body, type ContractValue, type DefinitionDetail, type Refusal } from './api'
import { statusTags } from './Definitions'

type Rule = string | string[] | Record<string, unknown>
type Part = { path?: unknown; keys?: unknown; where?: unknown; expect?: unknown; rest?: unknown; order?: unknown; values?: Record<string, Rule> }

const text = (value: unknown) => value === undefined || value === null || value === '' ? '—'
  : typeof value === 'string' ? value : Array.isArray(value) ? value.join(', ') : JSON.stringify(value)

function conditions(value: unknown) {
  if (!value || typeof value !== 'object') return '—'
  return Object.entries(value as Record<string, unknown>).map(([leaf, wanted]) => `${leaf} = ${text(wanted)}`).join('；')
}

/** How one Value is read: the leaf, a constant, a lookup, a code table, a format. */
function source(rule: Rule) {
  if (typeof rule === 'string' || Array.isArray(rule)) return <span className="nc-mono">{text(rule)}</span>
  const parts: React.ReactNode[] = []
  if ('const' in rule) parts.push(<span key="const">常量 <span className="nc-mono">{text(rule.const)}</span></span>)
  if ('leaf' in rule) parts.push(<span key="leaf">叶子 <span className="nc-mono">{text(rule.leaf)}</span></span>)
  const lookup = rule.lookup as { definition?: string; match?: unknown; take?: string; optional?: boolean } | undefined
  if (lookup) parts.push(<span key="lookup">关联 <span className="nc-mono">{lookup.definition}</span>（{lookup.match ? `按 ${text(lookup.match)} ` : '唯一一行 '}取 <span className="nc-mono">{lookup.take}</span>{lookup.optional ? '，查不到为空' : ''}）</span>)
  if (rule.format) parts.push(<span key="format">格式 <span className="nc-mono">{text(rule.format)}</span></span>)
  if (rule.when) parts.push(<span key="when">条件 {conditions(rule.when)}</span>)
  if ('default' in rule) parts.push(<span key="default">缺省 <span className="nc-mono">{text(rule.default)}</span></span>)
  if (rule.none) parts.push(<span key="none">视为无值 <span className="nc-mono">{text(rule.none)}</span></span>)
  return <div className="nc-rule">{parts.map((part, index) => <div key={index}>{part}</div>)}</div>
}

function codes(rule: Rule) {
  if (typeof rule !== 'object' || Array.isArray(rule) || !rule.codes) return <span className="nc-muted">—</span>
  return <div className="nc-codes">{Object.entries(rule.codes as Record<string, unknown>).map(([code, written]) =>
    <span key={code}><span className="nc-mono">{code}</span> → {text(written)}</span>)}</div>
}

/** The definition body as tables: where its rows are and how each Value is read. */
export function BodyView({ body }: { body: Body }) {
  const parts: Part[] = Array.isArray(body.parts) ? body.parts as Part[] : [body as Part]
  return <div className="nc-body">
    <Descriptions size="small" column={2} bordered items={[
      { key: 'vendor', label: '厂商与平台', children: `${text(body.vendor)} ${text(body.platforms)}` },
      { key: 'check', label: '检查项', children: <span className="nc-mono">{text(body.check_item)}</span> },
      { key: 'module', label: '设备模块', children: <span className="nc-mono">{text(body.module)}</span> },
      { key: 'store', label: '数据源', children: body.datastore === 'running' ? 'running 配置（get-config）' : '运行状态（get）' },
      { key: 'ns', label: '命名空间', span: 2, children: <span className="nc-mono">{text(body.namespace)}</span> },
    ]} />
    {parts.map((part, index) => <section key={index} className="nc-part">
      <h3>{parts.length > 1 ? `表 ${index + 1}：` : '表：'}<span className="nc-mono">{text(part.path)}</span></h3>
      <div className="nc-part-meta">
        <span>行标识 <span className="nc-mono">{text(part.keys)}</span></span>
        {part.where ? <span>只取 {conditions(part.where)}</span> : null}
        {part.expect ? <span>要求 {conditions(part.expect)}</span> : null}
        {part.rest ? <span>其余叶子写入 <span className="nc-mono">{text((part.rest as { value?: string }).value)}</span></span> : null}
        {part.order ? <span>排序 {conditions(part.order)}</span> : null}
      </div>
      <Table
        size="small"
        rowKey="name"
        pagination={false}
        dataSource={Object.entries(part.values || {}).map(([name, rule]) => ({ name, rule }))}
        locale={{ emptyText: '这张表只供关联，不直接产出值' }}
        columns={[
          { title: '值', dataIndex: 'name', width: 180, render: (name: string) => <span className="nc-mono">{name}</span> },
          { title: '取法', dataIndex: 'rule', render: (rule: Rule) => source(rule) },
          { title: '代码对照', dataIndex: 'rule', key: 'codes', render: (rule: Rule) => codes(rule) },
        ]}
      />
    </section>)}
  </div>
}

/** Line diff (longest common subsequence) of two JSON texts. */
export function diffLines(before: string, after: string) {
  const a = before.split('\n'), b = after.split('\n')
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--)
    table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
  const lines: { kind: ' ' | '-' | '+'; text: string }[] = []
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { lines.push({ kind: ' ', text: a[i] }); i++; j++ }
    else if (j < b.length && (i >= a.length || table[i][j + 1] >= table[i + 1][j])) { lines.push({ kind: '+', text: b[j] }); j++ }
    else { lines.push({ kind: '-', text: a[i] }); i++ }
  }
  return lines
}

const json = (body: unknown) => JSON.stringify(body, null, 2)

/** The Values a body writes: each part's values and its rest Value. */
export function mappedValues(body: Body) {
  const parts: Part[] = Array.isArray(body.parts) ? body.parts as Part[] : [body as Part]
  const names = new Set<string>()
  for (const part of parts) {
    Object.keys(part.values || {}).forEach(name => names.add(name))
    const rest = part.rest as { value?: string } | undefined
    if (rest?.value) names.add(rest.value)
  }
  return names
}

/** The check item's Values on each platform of the body, and which the body leaves unmapped. */
export function ContractPanel({ body, compact = false }: { body: Body; compact?: boolean }) {
  const checkItem = typeof body.check_item === 'string' ? body.check_item : ''
  const vendor = typeof body.vendor === 'string' ? body.vendor : ''
  const platforms = Array.isArray(body.platforms) ? (body.platforms as unknown[]).map(String) : []
  const key = `${checkItem}|${vendor}|${platforms.join(',')}`
  const [contracts, setContracts] = useState<Record<string, ContractValue[] | string>>({})
  useEffect(() => {
    if (!checkItem || !vendor || !platforms.length) { setContracts({}); return }
    let live = true
    Promise.all(platforms.map(platform => readContract(checkItem, vendor, platform)
      .then(found => [platform, found.values] as const)
      .catch(err => [platform, (err as Refusal).message] as const)))
      .then(found => { if (live) setContracts(Object.fromEntries(found)) })
    return () => { live = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!checkItem) return compact ? null : <p className="nc-muted">这条定义只供关联，不对应检查项。</p>
  const mapped = mappedValues(body)
  return <div className="nc-contract" aria-label="检查项的值">
    {Object.entries(contracts).map(([platform, values]) => {
      if (typeof values === 'string') return <Alert key={platform} type="warning" showIcon message={`${platform}：${values}`} />
      const missing = values.filter(value => value.required && !mapped.has(value.name)).map(value => value.name)
      const outside = [...mapped].filter(name => !values.some(value => value.name === name))
      if (compact) return (missing.length || outside.length)
        ? <Alert key={platform} type="error" showIcon message={`${platform}：${missing.length ? `未映射的必填值 ${missing.join('、')}` : ''}${missing.length && outside.length ? '；' : ''}${outside.length ? `检查项之外的值 ${outside.join('、')}` : ''}`} />
        : <Alert key={platform} type="success" showIcon message={`${platform}：覆盖了检查项的全部必填值`} />
      return <section key={platform} className="nc-part">
        <h3>检查项 <span className="nc-mono">{checkItem}</span> 在 {vendor} {platform} 上的值</h3>
        {(missing.length > 0 || outside.length > 0) && <Alert type="error" showIcon style={{ marginBottom: 10 }}
          message={[missing.length ? `未映射的必填值：${missing.join('、')}` : '', outside.length ? `检查项之外的值：${outside.join('、')}` : ''].filter(Boolean).join('；')} />}
        <Table size="small" rowKey="name" pagination={false} dataSource={values} columns={[
          { title: '值', dataIndex: 'name', render: (name: string) => <span className="nc-mono">{name}</span> },
          { title: '类型', dataIndex: 'value_type', render: (type: string | null) => type || <span className="nc-muted">—</span> },
          { title: '必填', dataIndex: 'required', render: (required: boolean, row) => <>{required ? '是' : '否'}{row.identity && <Tag style={{ marginLeft: 8 }}>标识</Tag>}</> },
          { title: '定义', render: (_, row) => mapped.has(row.name) ? <Tag color="green">已映射</Tag> : row.required ? <Tag color="red">未映射</Tag> : <span className="nc-muted">未映射</span> },
        ]} />
      </section>
    })}
  </div>
}

/** Step 2: one definition's versions, its draft, and saving, discarding, publishing. */
export default function Editor({ detail, onChanged }: { detail: DefinitionDetail; onChanged: () => Promise<void> }) {
  const inUse = detail.published_version ? `v${detail.published_version}` : ''
  const [bodies, setBodies] = useState<Record<string, Body>>(() => ({
    ...(detail.draft ? { draft: detail.draft } : {}),
    ...(detail.published ? { [`v${detail.published.version}`]: detail.published.body } : {}),
  }))
  const choices = [
    ...(detail.draft ? [{ value: 'draft', label: '草稿' }] : []),
    ...detail.versions.map(v => ({ value: `v${v.version}`, label: `v${v.version}${v.version === detail.published_version ? '（使用中）' : ''}` })),
  ]
  const [shown, setShown] = useState(detail.draft ? 'draft' : inUse)
  const [compareWith, setCompareWith] = useState<string>('')
  const [view, setView] = useState<string>('结构')
  const [editing, setEditing] = useState(false)
  const [source, setSource] = useState('')
  const [editError, setEditError] = useState<{ message: string; conflict: boolean }>()
  const [loadError, setLoadError] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [note, setNote] = useState('')
  const [publishError, setPublishError] = useState<Refusal>()
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  // The draft and the version in use come with the detail; older versions on demand.
  useEffect(() => {
    setBodies(current => ({
      ...current,
      ...(detail.draft ? { draft: detail.draft } : {}),
      ...(detail.published ? { [`v${detail.published.version}`]: detail.published.body } : {}),
    }))
    if (!detail.draft) setBodies(({ draft: _gone, ...rest }) => rest)
  }, [detail])
  useEffect(() => {
    for (const key of [shown, compareWith]) {
      if (!key || key === 'draft' || bodies[key]) continue
      readVersion(detail.id, Number(key.slice(1)))
        .then(found => setBodies(current => ({ ...current, [key]: found.body })))
        .catch(err => setLoadError((err as Refusal).message))
    }
  }, [shown, compareWith, bodies, detail.id])
  const body = bodies[shown] || null

  function edit(from?: Body) {
    setSource(json(from || detail.draft || detail.published?.body || {}))
    setEditError(undefined)
    setEditing(true)
  }
  async function save() {
    let parsed: Body
    try {
      parsed = JSON.parse(source)
    } catch (err) {
      setEditError({ message: `JSON 格式错误：${(err as Error).message}`, conflict: false })
      return
    }
    setBusy(true)
    try {
      await saveDraft(detail.id, parsed, detail.revision)
      setEditing(false)
      setShown('draft')
      setNotice('草稿已保存')
      await onChanged()
    } catch (err) {
      const refusal = err as Refusal
      setEditError({ message: refusal.message, conflict: refusal.status === 409 })
    } finally {
      setBusy(false)
    }
  }
  async function discard() {
    setBusy(true)
    try {
      await discardDraft(detail.id, detail.revision)
      setShown(inUse)
      setNotice('草稿已丢弃')
      await onChanged()
    } catch (err) {
      setNotice('')
      setEditError({ message: (err as Refusal).message, conflict: (err as Refusal).status === 409 })
    } finally {
      setBusy(false)
    }
  }
  async function publish() {
    setBusy(true)
    setPublishError(undefined)
    try {
      const version = await publishDraft(detail.id, detail.revision, note)
      setPublishing(false)
      setNote('')
      setShown(`v${version.version}`)
      setNotice(`已发布 v${version.version}`)
      await onChanged()
    } catch (err) {
      setPublishError(err as Refusal)
    } finally {
      setBusy(false)
    }
  }
  async function refresh() {
    setEditError(undefined)
    setEditing(false)
    await onChanged()
  }

  let parsedSource: Body | null = null
  try { parsedSource = editing ? JSON.parse(source) : null } catch { parsedSource = null }
  // Compared with the chosen version; by default the draft with the version in use.
  const against = compareWith && compareWith !== shown ? compareWith
    : shown === 'draft' ? inUse : detail.draft ? 'draft' : ''
  const other = against && bodies[against]
  const diff = body && other ? diffLines(json(other), json(body)) : null
  const label = (key: string) => choices.find(choice => choice.value === key)?.label || key
  return <div className="nc-editor">
    <div className="nc-detail-head">
      <div>
        <div className="nc-mono nc-detail-id">{detail.id}</div>
        <div className="nc-tags">{statusTags(detail)}{detail.check_item && <Tag>{detail.check_item}</Tag>}</div>
        {detail.description && <p className="nc-muted">{detail.description}</p>}
      </div>
      <div className="nc-actions">
        <Button icon={<EditOutlined aria-hidden="true" />} onClick={() => edit()}>{detail.draft ? '编辑草稿' : '新建草稿'}</Button>
        {shown !== 'draft' && body && <Button onClick={() => edit(body)}>以此版本为草稿</Button>}
        {detail.draft && detail.published_version && <Button icon={<DeleteOutlined aria-hidden="true" />} loading={busy} onClick={discard}>丢弃草稿</Button>}
        {detail.draft && <Button type="primary" icon={<CloudUploadOutlined aria-hidden="true" />} onClick={() => { setPublishError(undefined); setPublishing(true) }}>发布</Button>}
      </div>
    </div>
    {notice && <Alert type="success" showIcon closable message={notice} onClose={() => setNotice('')} style={{ marginBottom: 16 }} />}
    {loadError && <Alert type="error" showIcon closable message={loadError} onClose={() => setLoadError('')} style={{ marginBottom: 16 }} />}
    {editError && !editing && <Alert type="error" showIcon message={editError.message} style={{ marginBottom: 16 }}
      action={editError.conflict ? <Button size="small" icon={<ReloadOutlined aria-hidden="true" />} onClick={refresh}>刷新</Button> : undefined} />}
    <div className="nc-filters">
      {choices.length > 0 && <Select aria-label="查看的版本" value={shown} onChange={setShown} options={choices} style={{ width: 160 }} />}
      <Segmented value={view} onChange={value => setView(String(value))} options={['结构', '源码', '版本历史', '差异']} />
      {view === '差异' && <Select aria-label="比较的版本" placeholder="选择比较的版本" value={against || undefined} onChange={setCompareWith}
        options={choices.filter(choice => choice.value !== shown)} style={{ width: 180 }} />}
    </div>
    {view === '结构' && (body ? <div className="nc-body"><ContractPanel body={body} /><BodyView body={body} /></div> : <Empty description="没有可查看的定义体" />)}
    {view === '源码' && (body ? <pre className="nc-source" aria-label="定义源码">{json(body)}</pre> : <Empty description="没有可查看的定义体" />)}
    {view === '版本历史' && <Table size="small" rowKey="version" pagination={false} dataSource={detail.versions}
      locale={{ emptyText: '尚未发布过' }}
      onRow={row => ({ onClick: () => { setShown(`v${row.version}`); setView('结构') }, className: 'nc-clickable' })}
      columns={[
        { title: '版本', dataIndex: 'version', render: (v: number) => <>v{v}{v === detail.published_version && <Tag color="blue" style={{ marginLeft: 8 }}>使用中</Tag>}</> },
        { title: '发布人', dataIndex: 'published_by' },
        { title: '时间', dataIndex: 'published_at', render: (at: string | null) => at ? new Date(at).toLocaleString('zh-CN') : '—' },
        { title: '说明', dataIndex: 'note', render: (n: string) => n || <span className="nc-muted">—</span> },
        { title: 'SHA-256', dataIndex: 'sha256', render: (sha: string) => <span className="nc-mono" title={sha}>{sha.slice(0, 12)}</span> },
      ]} />}
    {view === '差异' && (diff
      ? <>
          <p className="nc-muted">{label(against)} → {label(shown)}（- 只在前者，+ 只在后者）</p>
          <pre className="nc-source nc-diff" aria-label="版本差异">{diff.map((line, index) =>
            <div key={index} className={line.kind === '+' ? 'nc-added' : line.kind === '-' ? 'nc-removed' : undefined}>{line.kind} {line.text}</div>)}</pre>
        </>
      : <Empty description={choices.length < 2 ? '只有一个版本，没有可比较的' : '选择要比较的版本'} />)}

    <Modal open={editing} width={880} title={`编辑草稿：${detail.id}`} okText="保存草稿" cancelText="取消" confirmLoading={busy} onOk={save} onCancel={() => setEditing(false)} destroyOnClose>
      <p className="nc-muted">定义体为 JSON，字段同种子文件（id、vendor、platforms、check_item、namespace、datastore、module，以及 path/keys/values 或 parts）。保存时由后端校验，错误原样显示。</p>
      <Input.TextArea aria-label="定义 JSON" value={source} onChange={event => setSource(event.target.value)} autoSize={{ minRows: 18, maxRows: 30 }} className="nc-mono" spellCheck={false} />
      {parsedSource && <div style={{ marginTop: 12 }}><ContractPanel body={parsedSource} compact /></div>}
      {editError && <Alert type="error" showIcon message={editError.conflict ? '版本冲突' : '保存失败'} description={editError.message} style={{ marginTop: 12 }}
        action={editError.conflict ? <Button size="small" icon={<ReloadOutlined aria-hidden="true" />} onClick={refresh}>刷新</Button> : undefined} />}
    </Modal>

    <Modal open={publishing} title={`发布 ${detail.id}`} okText="发布" cancelText="取消" confirmLoading={busy} onOk={publish} onCancel={() => setPublishing(false)} destroyOnClose>
      <p className="nc-muted">发布后成为不可变版本，拓扑采集立即使用。发布时检查定义是否覆盖了检查项的全部必填值。</p>
      <label className="nc-field">发布说明<Input.TextArea value={note} maxLength={500} onChange={event => setNote(event.target.value)} /></label>
      {publishError && <Alert type="error" showIcon message={publishError.message} style={{ marginTop: 12 }}
        description={publishError.gaps.length > 0 && <ul className="nc-gaps">{publishError.gaps.map(gap => <li key={gap} className="nc-mono">{gap}</li>)}</ul>} />}
    </Modal>
  </div>
}
