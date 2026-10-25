import React, { useState } from 'react'
import { Alert, Button, Checkbox, Input, Space, Table } from 'antd'
import { api } from '@netops/config/api'
import { publicationError, type Preview } from './WordImport'

interface Item { id: string; file: File; title: string; preview?: Preview; status: string; error?: string }

export default function BatchImport({ onPublished }: { onPublished: () => Promise<void> }) {
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  const [ack, setAck] = useState(false)
  const [error, setError] = useState('')
  const [metadata, setMetadata] = useState({ vendor: '', product: '', software_version: '' })
  const ready = items.filter(item => item.preview && item.status !== '已发布')
  function update(index: number, changes: Partial<Item>) {
    setItems(previous => previous.map((item, i) => i === index ? { ...item, ...changes } : item))
  }
  async function parse() {
    setBusy(true); setAck(false); setError('')
    for (let index = 0; index < items.length; index++) {
      if (items[index].preview || items[index].status === '已发布') continue
      update(index, { status: '解析中', error: undefined })
      try {
        const body = new FormData(); body.append('file', items[index].file)
        const result = await api.post<Preview>('/apps/materials/documents/preview', body, { headers: { 'Content-Type': undefined } })
        update(index, { preview: result.data, status: '待发布' })
      } catch (e) { update(index, { status: '解析失败', error: publicationError(e) }) }
    }
    setBusy(false)
  }
  async function publish() {
    if (!ack || !ready.length) return
    setBusy(true); setError('')
    for (let index = 0; index < items.length; index++) {
      const item = items[index]
      if (!item.preview || item.status === '已发布') continue
      update(index, { status: '发布中', error: undefined })
      try {
        const body = new FormData(); body.append('file', item.file)
        body.append('options', JSON.stringify({ title: item.title, metadata,
          source_sha256: item.preview.source_sha256, normalized_sha256: item.preview.normalized_sha256,
          acknowledged_limitations: true }))
        await api.post('/apps/materials/documents/import', body, { headers: { 'Content-Type': undefined } })
        update(index, { status: '已发布' })
      } catch (e) { update(index, { status: '未发布', error: publicationError(e) }) }
    }
    try { await onPublished() } catch { setError('资料列表刷新失败，请手动刷新；各文件的发布结果已保留。') }
    setBusy(false)
  }
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <p>一次导入最多 10 个 Word 文件，总计不超过 64 MB。逐个解析和发布，相同文件会提示已存在；重试时只处理未成功文件。</p>
    <label className="materials-word-upload">选择 Word 文件
      <input type="file" aria-label="批量 Word 文件" accept=".docx" multiple disabled={busy} onChange={event => {
        const files = Array.from(event.target.files || [])
        setItems([]); setAck(false); setError('')
        if (files.length > 10 || files.reduce((n, file) => n + file.size, 0) > 64 * 1024 * 1024
          || files.some(file => !file.name.toLowerCase().endsWith('.docx') || !file.size || file.size > 16 * 1024 * 1024)) {
          setError('最多 10 个 .docx 文件，单文件 16 MB、合计 64 MB，且文件不能为空。'); return
        }
        setItems(files.map((file, index) => ({ id: String(index), file, title: file.name.replace(/\.docx$/i, '').slice(0, 256), status: '待解析' })))
      }} />
    </label>
    {error && <Alert type="error" message={error} />}
    <Space wrap>{Object.entries({ vendor: '厂商', product: '产品', software_version: '软件版本' }).map(([key, label]) =>
      <Input key={key} aria-label={'批量' + label} placeholder={label + '（应用于本批文件）'} disabled={busy} maxLength={256}
        value={metadata[key as keyof typeof metadata]} onChange={event => setMetadata({ ...metadata, [key]: event.target.value })} />)}</Space>
    <Table<Item> rowKey="id" dataSource={items} pagination={false} size="small" columns={[
      { title: '文件 / 资料标题', render: (_, item, index) => <><small>{item.file.name}</small><Input aria-label={'资料标题 ' + (index + 1)} value={item.title} maxLength={256} disabled={busy || item.status === '已发布'} onChange={event => update(index, { title: event.target.value })} /></> },
      { title: '发布范围', render: (_, item) => item.preview ? `整份文件 · ${item.preview.sections.length} 章` : '待解析' },
      { title: '状态', render: (_, item) => <><span role="status">{item.status}</span>{item.error && <p className="materials-import-error">{item.error}</p>}</> },
    ]} />
    {items.some(item => item.preview) && <details><summary>提取提示（按文件核对）</summary>{items.filter(item => item.preview).map((item, index) => <section key={index}><strong>{item.file.name}</strong><ul>{item.preview?.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>)}</details>}
    <Button disabled={busy || !items.some(item => !item.preview && item.status !== '已发布')} onClick={() => void parse()}>解析待处理文件</Button>
    <Checkbox disabled={busy || !ready.length} checked={ack} onChange={event => setAck(event.target.checked)}>已核对本批文件的提取提示，确认逐个发布整个文件</Checkbox>
    <Button type="primary" loading={busy} disabled={busy || !ack || !ready.length || ready.some(item => !item.title.trim())} onClick={() => void publish()}>发布待处理文件（{ready.length}）</Button>
  </Space>
}
