import React, { useState } from 'react'
import { Alert, Button, Drawer, Space, Typography } from 'antd'
import { api } from '../config/api'

export interface Citation {
  id: string; title_path: string[]; revision: string; excerpt: string
  kind?: string; app_id?: string; retrieved_at?: string; observed_at?: string
  start_line?: number; end_line?: number
  /** An attachment citation: the cited line range of the user's file. */
  lines?: [number, number]
  document_id?: string
  source?: { filename: string; warnings: string[]; locations: { kind: string; paragraph?: number; table?: number; row?: number }[]; locations_truncated: boolean }
}

const LABEL: Record<string, string> = { business: '业务证据', attachment: '附件' }
const TITLE: Record<string, string> = { business: '已读取的业务证据', attachment: '附件引用' }

export default function EvidenceLinks({ conversationId, references = [] }: { conversationId: string; references?: Citation[] | null }) {
  const [citation, setCitation] = useState<Citation | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState('')
  if (!references?.length) return null
  return <div style={{ margin: '8px 0 20px' }}>
    <Space wrap>{references.map((ref, index) => <Button size="small" key={ref.id} loading={loading === ref.id}
      onClick={async () => {
        setError(''); setLoading(ref.id)
        try {
          const result = await api.get<Citation>(`/apps/assistant/conversations/${conversationId}/citations/${ref.id}`)
          setCitation(result.data)
        } catch { setError('无法打开证据，请检查应用权限或刷新重试。') }
        finally { setLoading('') }
      }}>{LABEL[ref.kind || ''] || '原文'} {index + 1} · {ref.title_path?.at(-1) || '查看来源'}</Button>)}</Space>
    {error && <Alert type="error" message={error} style={{ marginTop: 8 }} />}
    <Drawer title={TITLE[citation?.kind || ''] || '原文引用'} width="min(640px, 100vw)" open={!!citation} onClose={() => setCitation(null)}>
      {citation && <>
        <Typography.Paragraph>{citation.title_path.join(' / ')}</Typography.Paragraph>
        <Typography.Paragraph type="secondary">{citation.kind === 'business'
          ? `读取于 ${citation.retrieved_at}；采集时间见证据 observed_at。此处保存当次快照，不代表实时状态。`
          : citation.kind === 'attachment'
            ? `第 ${citation.lines?.[0]}–${citation.lines?.[1]} 行，回答时读取的内容；文件在网盘“问答附件”中`
            : `原修订 ${citation.revision.slice(0, 12)} · 行 ${citation.start_line}–${citation.end_line}`}</Typography.Paragraph>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{citation.excerpt}</pre>
        {citation.source && <>
          <Typography.Paragraph>原文件：{citation.source.filename}</Typography.Paragraph>
          <Typography.Paragraph type="secondary">{citation.source.locations.map(location => location.kind === 'table_row'
            ? `表 ${location.table} 第 ${location.row} 行` : `段落 ${location.paragraph}`).join('、')}{citation.source.locations_truncated ? '（位置列表已截断）' : ''}</Typography.Paragraph>
          <Alert type="warning" message="提取说明" description={<ul>{citation.source.warnings.map(w => <li key={w}>{w}</li>)}</ul>} />
          {citation.document_id && <Button style={{ marginTop: 12 }} onClick={async () => {
            try {
              const response = await api.get<Blob>(`/apps/assistant/documents/${citation.document_id}/revisions/${citation.revision}/source`, { responseType: 'blob' })
              const url = URL.createObjectURL(response.data); const link = document.createElement('a')
              link.href = url; link.download = citation.source!.filename; link.click()
              window.setTimeout(() => URL.revokeObjectURL(url), 1000)
            } catch { setError('原文件下载失败，请检查权限或文件状态。') }
          }}>下载完整 Word 原文件核对</Button>}
        </>}
      </>}
    </Drawer>
  </div>
}
