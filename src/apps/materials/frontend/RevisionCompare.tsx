import React, { useState } from 'react'
import { Alert, Button, Empty, Select, Space, Table } from 'antd'
import { api } from '@netops/config/api'
import { publicationError } from './WordImport'

interface Change { kind: 'added' | 'removed' | 'changed'; title_path: string[]; before_section: string | null; after_section: string | null }
interface Comparison { before: string; after: string; title: { before: string; after: string }; metadata: { before: Record<string, string>; after: Record<string, string> }; counts: Record<string, number>; items: Change[]; next_offset: number | null }
interface Excerpt { citation: { excerpt: string }; next_offset: number | null }

export default function RevisionCompare({ documentId, revision, revisions }: { documentId: string; revision: string; revisions: { revision: string; created_at: string }[] }) {
  const [before, setBefore] = useState<string>()
  const [comparison, setComparison] = useState<Comparison>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [active, setActive] = useState<Change>()
  const [excerpts, setExcerpts] = useState<{ before?: Excerpt; after?: Excerpt }>({})
  const base = '/apps/materials/documents/' + documentId
  async function compare(offset = 0) {
    if (!before) return
    setBusy(true); setError('')
    try {
      const { data } = await api.get<Comparison>(base + '/compare', { params: { before, after: revision, offset } })
      setComparison(previous => offset && previous ? { ...data, items: [...previous.items, ...data.items] } : data)
    } catch (e) { setError(publicationError(e)) }
    finally { setBusy(false) }
  }
  async function inspect(item: Change) {
    setBusy(true); setError(''); setActive(item); setExcerpts({})
    try {
      const read = async (section: string | null, version: string) => section ? (await api.get<Excerpt>(base + '/sections/' + section, { params: { revision: version, limit: 4000 } })).data : undefined
      const [old, current] = await Promise.all([read(item.before_section, comparison!.before), read(item.after_section, comparison!.after)])
      setExcerpts({ before: old, after: current })
    } catch (e) { setError(publicationError(e)) }
    finally { setBusy(false) }
  }
  async function more(side: 'before' | 'after') {
    const section = active?.[side === 'before' ? 'before_section' : 'after_section']
    const offset = excerpts[side]?.next_offset
    if (!section || offset == null || !comparison) return
    setBusy(true); setError('')
    try {
      const { data } = await api.get<Excerpt>(base + '/sections/' + section, { params: { revision: comparison[side], offset, limit: 4000 } })
      setExcerpts(previous => ({ ...previous, [side]: data }))
    } catch (e) { setError(publicationError(e)) }
    finally { setBusy(false) }
  }
  return <section className="materials-compare" aria-label="修订比较">
    <h2>修订比较</h2>
    <Space wrap><Select aria-label="比较基准修订" placeholder="选择基准修订" style={{ minWidth: 240 }} value={before} disabled={busy}
      options={revisions.filter(r => r.revision !== revision).map(r => ({ value: r.revision, label: r.created_at.slice(0, 10) + ' · ' + r.revision.slice(0, 12) }))}
      onChange={value => { setBefore(value); setComparison(undefined); setActive(undefined); setExcerpts({}) }} />
      <Button disabled={!before || busy} loading={busy} onClick={() => void compare()}>与所选修订比较</Button></Space>
    {error && <Alert type="error" message={error} />}
    {comparison && <>
      <p>新增 {comparison.counts.added} 章 · 移除 {comparison.counts.removed} 章 · 正文变化 {comparison.counts.changed} 章</p>
      <dl className="materials-metadata-diff">{['title', ...new Set([...Object.keys(comparison.metadata.before), ...Object.keys(comparison.metadata.after)])].map(key => {
        const a = key === 'title' ? comparison.title.before : comparison.metadata.before[key]
        const b = key === 'title' ? comparison.title.after : comparison.metadata.after[key]
        const labels: Record<string, string> = { title: '标题', vendor: '厂商', product: '产品', software_version: '软件版本', doc_type: '资料类型', category: '分类' }
        return a === b ? null : <div key={key}><dt>{labels[key] || key}</dt><dd>{a || '未填写'} → {b || '未填写'}</dd></div>
      })}</dl>
      <Table<Change> rowKey={item => (item.before_section || '') + ':' + (item.after_section || '')} dataSource={comparison.items} pagination={false} size="small"
        locale={{ emptyText: <Empty description="两个修订的章节正文一致" /> }} columns={[
          { title: '变化', width: 90, render: (_, item) => ({ added: '新增', removed: '移除', changed: '正文变化' })[item.kind] },
          { title: '章节', render: (_, item) => <Button type="link" disabled={busy} style={{ whiteSpace: 'normal', height: 'auto', textAlign: 'left' }} onClick={() => void inspect(item)}>{item.title_path.join(' / ') || '正文'}</Button> },
        ]} />
      {comparison.next_offset !== null && <Button disabled={busy} onClick={() => void compare(comparison.next_offset!)}>更多变化</Button>}
      {active && <div className="materials-diff-excerpts">{(['before', 'after'] as const).map(side => <section key={side}><h3>{side === 'before' ? '基准修订' : '所选修订'}</h3>
        <pre>{excerpts[side]?.citation.excerpt || (busy ? '读取中…' : '此修订没有该章节')}</pre>
        {excerpts[side]?.next_offset != null && <Button disabled={busy} onClick={() => void more(side)}>继续阅读{side === 'before' ? '基准' : '所选'}修订</Button>}
      </section>)}</div>}
    </>}
  </section>
}
