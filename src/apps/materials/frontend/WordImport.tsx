import React, { useState } from 'react'
import { Alert, Button, Checkbox, Input, Pagination, Space, Table, Typography } from 'antd'
import { api } from '@netops/config/api'
import { FileWordOutlined, SearchOutlined } from '@ant-design/icons'

interface Section { id: string; title_path: string[]; start_line: number; text: string }
export interface Preview { source_sha256: string; normalized_sha256: string; warnings: string[]; sections: Section[] }
export interface Target { id: string; title: string; revision: string; metadata: Record<string, string> }

export default function WordImport({ target, onPublished }: { target?: Target; onPublished: () => Promise<void> }) {
  const [file, setFile] = useState<File>()
  const [preview, setPreview] = useState<Preview>()
  const [reading, setReading] = useState<Section>()
  const [page, setPage] = useState(1)
  const [title, setTitle] = useState(target?.title || '')
  const [vendor, setVendor] = useState(target?.metadata.vendor || '')
  const [product, setProduct] = useState(target?.metadata.product || '')
  const [version, setVersion] = useState(target?.metadata.software_version || '')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [sectionQuery, setSectionQuery] = useState('')
  const matchedSections = preview?.sections.filter(s => [s.title_path.join(' / '), s.text].join(' ').toLowerCase().includes(sectionQuery.trim().toLowerCase())) || []
  const totalCharacters = preview?.sections.reduce((sum, s) => sum + s.text.length, 0) || 0

  async function inspect() {
    if (!file) return
    setBusy(true); setError(''); setPreview(undefined); setReading(undefined); setAcknowledged(false); setSuccess(false)
    try {
      const body = new FormData(); body.append('file', file)
      const result = await api.post<Preview>('/apps/materials/documents/preview', body, { headers: { 'Content-Type': undefined } })
      setPreview(result.data)
    } catch { setError('无法解析该文件。请检查文件格式、大小和服务状态。') }
    finally { setBusy(false) }
  }

  async function publish() {
    if (!file || !preview || !acknowledged) return
    setBusy(true); setError('')
    try {
      const body = new FormData(); body.append('file', file)
      body.append('options', JSON.stringify({
        title, source_sha256: preview.source_sha256, normalized_sha256: preview.normalized_sha256,
        acknowledged_limitations: acknowledged,
        metadata: { ...target?.metadata, vendor, product, software_version: version },
        ...(target ? { document_id: target.id, expected_revision: target.revision } : {}),
      }))
      await api.post('/apps/materials/documents/import', body, { headers: { 'Content-Type': undefined } })
      setPreview(undefined); setReading(undefined); setSuccess(true)
      await onPublished()
    } catch (error) { setError(publicationError(error)) }
    finally { setBusy(false) }
  }

  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <p className="materials-word-intro">本地解析 Word，先预览再发布。保存原文件不会把整份资料发送给模型；问答只按需读取已发布章节。</p>
    <div className="materials-word-upload"><FileWordOutlined aria-hidden="true" />
    <label>Word 文件（.docx，最多 16 MB）
      <input aria-label="Word 文件" type="file" accept=".docx" disabled={busy} style={{ display: 'block', marginTop: 8 }}
        onChange={e => {
          const next = e.target.files?.[0]
          setPreview(undefined); setReading(undefined); setAcknowledged(false); setSuccess(false); setError(''); setSectionQuery('')
          if (next && (!next.name.toLowerCase().endsWith('.docx') || next.size > 16 * 1024 * 1024 || !next.size)) {
            setFile(undefined); setError('请选择不超过 16 MB 的非空 .docx 文件。'); return
          }
          setFile(next); if (next && !target) setTitle(next.name.replace(/\.docx$/i, ''))
        }} />
    </label>
    <Button onClick={() => void inspect()} disabled={!file || busy} loading={busy && !preview}>解析并预览</Button>
    </div>
    {error && <Alert type="error" message={error} />}
    {success && <Alert type="success" message="资料已发布，可在问答中按需检索。" />}
    {preview && <>
      <Alert type="warning" message="请核对提取范围" description={<ul>{preview.warnings.map(w => <li key={w}>{w}</li>)}</ul>} />
      <Input aria-label="资料标题" placeholder="资料标题" value={title} maxLength={256} disabled={busy} onChange={e => setTitle(e.target.value)} />
      <Space wrap>
        <Input aria-label="厂商" placeholder="厂商" value={vendor} maxLength={256} disabled={busy} onChange={e => setVendor(e.target.value)} />
        <Input aria-label="产品" placeholder="产品" value={product} maxLength={256} disabled={busy} onChange={e => setProduct(e.target.value)} />
        <Input aria-label="软件版本" placeholder="软件版本" value={version} maxLength={256} disabled={busy} onChange={e => setVersion(e.target.value)} />
      </Space>
      <div className="materials-import-scope"><Typography.Text strong>整份文件 · {preview.sections.length} 个章节</Typography.Text>
        <p>将发布全部 {totalCharacters.toLocaleString('zh-CN')} 字符。下方搜索仅筛选预览，不改变发布范围；问答按需读取片段。</p></div>
      <div className="materials-section-search"><Input aria-label="搜索预览章节" allowClear prefix={<SearchOutlined />} placeholder="搜索章节标题或正文，如 VPC Endpoint" value={sectionQuery} onChange={e => setSectionQuery(e.target.value)} />
        <span>{matchedSections.length} 个匹配章节</span></div>
      <Table<Section> size="small" rowKey="id" dataSource={matchedSections} pagination={{ pageSize: 10, showSizeChanger: false }}
        columns={[
          { title: '章节', render: (_, section) => <Button type="link" style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
            onClick={() => { setReading(section); setPage(1) }}>{section.title_path.join(' / ') || '正文'}</Button> },
          { title: '字符数', width: 90, render: (_, section) => section.text.length },
        ]} />
      {reading && <section aria-label="章节预览">
        <Typography.Title level={3}>{reading.title_path.join(' / ') || '正文'}</Typography.Title>
        <Typography.Text style={{ color: '#475569' }}>提取起始行 {reading.start_line} · 原文件与提取内容可能存在版式差异</Typography.Text>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 360, overflowY: 'auto' }}>{reading.text.slice((page - 1) * 4000, page * 4000)}</pre>
        <Pagination current={page} pageSize={4000} total={reading.text.length} showSizeChanger={false} onChange={setPage} />
      </section>}
      <Checkbox checked={acknowledged} disabled={busy} onChange={e => setAcknowledged(e.target.checked)}>已核对提取预览与缺失提示，确认发布整个文件</Checkbox>
      <Button type="primary" loading={busy} disabled={!title.trim() || !acknowledged || busy} onClick={() => void publish()}>
        {target ? '发布新修订' : '发布整个文件'}
      </Button>
    </>}
  </Space>
}

export function publicationError(error: unknown) {
  if (error instanceof Error && error.name === 'ApiError') return error.message
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : '发布未完成。若资料已被更新，请关闭导入窗口并刷新；其他情况可核对后重试。'
}
