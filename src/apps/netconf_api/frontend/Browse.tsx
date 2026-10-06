import React, { useMemo, useState } from 'react'
import { Alert, Button, Input, Segmented, Select, Statistic, Tag } from 'antd'
import { EyeOutlined } from '@ant-design/icons'
import { browse, readDefinition, type BrowseResult, type DefinitionSummary, type Refusal, failureText } from './api'

const SHOWN_CHILDREN = 200 // per element; the raw text always has everything

function XmlNode({ node, path, onPath }: { node: Element; path: string[]; onPath: (path: string[]) => void }) {
  const here = [...path, node.localName]
  const children = Array.from(node.children)
  if (!children.length) {
    return <div className="nc-xml-leaf"><span className="nc-mono">{node.localName}</span> = <span className="nc-mono nc-xml-value">{node.textContent}</span></div>
  }
  return <details className="nc-xml-node" open={path.length < 3}>
    <summary>
      <span className="nc-mono">{node.localName}</span>
      <span className="nc-muted"> {children.length} 项</span>
      <Button size="small" type="link" onClick={event => { event.preventDefault(); onPath(here) }}>设为路径</Button>
    </summary>
    {children.slice(0, SHOWN_CHILDREN).map((child, index) => <XmlNode key={index} node={child} path={here} onPath={onPath} />)}
    {children.length > SHOWN_CHILDREN && <div className="nc-muted">还有 {children.length - SHOWN_CHILDREN} 项未展开，见原文</div>}
  </details>
}

/** Read one subtree of a device as it is (read only: a filtered get or get-config). */
export default function Browse({ deviceId, definitions }: { deviceId: string; definitions: DefinitionSummary[] }) {
  const [namespace, setNamespace] = useState('')
  const [path, setPath] = useState('')
  const [datastore, setDatastore] = useState<'state' | 'running'>('state')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<BrowseResult>()
  const [raw, setRaw] = useState(false)

  async function fromDefinition(id: string) {
    try {
      const found = await readDefinition(id)
      const body = (found.draft || found.published?.body || {}) as Record<string, unknown>
      const parts = Array.isArray(body.parts) ? body.parts as { path?: unknown }[] : [body as { path?: unknown }]
      const first = parts[0]?.path
      setNamespace(String(body.namespace || ''))
      setPath(Array.isArray(first) ? first.join('/') : String(first || ''))
      if (body.datastore === 'running') setDatastore('running')
    } catch (err) {
      setError((err as Refusal).message)
    }
  }
  async function run(names = path.split('/').map(name => name.trim()).filter(Boolean)) {
    setRunning(true)
    setError('')
    try {
      setResult(await browse(deviceId, namespace.trim(), names, datastore))
    } catch (err) {
      setError((err as Refusal).message)
    } finally {
      setRunning(false)
    }
  }
  const root = useMemo(() => {
    if (!result?.data) return null
    const doc = new DOMParser().parseFromString(result.data, 'application/xml')
    return doc.querySelector('parsererror') ? null : doc.documentElement
  }, [result])
  const tops = root ? Array.from(root.children) : []

  return <section className="nc-browse" aria-label="数据浏览">
    <h3>数据浏览</h3>
    <p className="nc-muted">按命名空间和路径读取设备的一棵子树，原样显示，用来写定义或核对定义。只读：只发带过滤条件的 get（运行状态）或 get-config（running 配置），不会改设备配置。</p>
    <div className="nc-filters">
      <Select showSearch allowClear aria-label="从定义带入" placeholder="从定义带入命名空间和路径" style={{ width: 300 }}
        options={definitions.map(row => ({ value: row.id, label: row.id }))} onChange={id => id && void fromDefinition(id)} />
      <Segmented value={datastore} onChange={value => setDatastore(value as 'state' | 'running')}
        options={[{ value: 'state', label: '运行状态（get）' }, { value: 'running', label: 'running 配置（get-config）' }]} />
    </div>
    <div className="nc-filters">
      <Input aria-label="命名空间" placeholder="命名空间，例如 http://www.h3c.com/netconf/data:1.0" value={namespace} onChange={event => setNamespace(event.target.value)} style={{ width: 380 }} />
      <Input aria-label="路径" placeholder="从顶层元素起，用 / 分隔，例如 top/ARP/ArpTable" value={path} onChange={event => setPath(event.target.value)} style={{ width: 320 }} onPressEnter={() => void run()} />
      <Button type="primary" icon={<EyeOutlined aria-hidden="true" />} loading={running} disabled={!namespace.trim() || !path.trim()} onClick={() => void run()}>读取</Button>
    </div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
    {result?.status === 'failed' && <Alert type="error" showIcon message={`读取失败：${failureText(result.failure_code || null)}`} description={result.message || undefined} style={{ marginBottom: 12 }} />}
    {result?.status === 'succeeded' && <div aria-label="浏览结果">
      <div className="nc-stats">
        <Statistic title="回包大小" value={result.reply_bytes < 1024 ? `${result.reply_bytes} B` : `${(result.reply_bytes / 1024).toFixed(1)} KB`} />
        <Statistic title="耗时" value={`${result.elapsed_ms} ms`} />
        <Statistic title="数据源" value={result.datastore === 'running' ? 'running 配置' : '运行状态'} />
      </div>
      <p className="nc-muted">过滤条件 <span className="nc-mono">{result.filter}</span></p>
      <div className="nc-filters"><Segmented value={raw ? '原文' : '树'} onChange={value => setRaw(value === '原文')} options={['树', '原文']} /></div>
      {raw || !root
        ? <pre className="nc-source" aria-label="回包原文">{result.data || '（空）'}</pre>
        : tops.length
          ? <div className="nc-xml">{tops.map((node, index) => <XmlNode key={index} node={node} path={[]} onPath={names => { setPath(names.join('/')); void run(names) }} />)}</div>
          : <Tag>设备没有返回这条路径下的数据</Tag>}
    </div>}
  </section>
}
