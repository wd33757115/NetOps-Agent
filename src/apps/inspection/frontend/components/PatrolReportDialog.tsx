import React, { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Input, Modal, Pagination, Select, Space, Spin, Switch, Tabs, Typography, Upload, message } from 'antd'
import { downloadBlob } from '@netops/utils/storageLocalOpen'
import { patrolReports, type DraftPreview, type DraftResult, type EvidenceLine, type ReportEvidence, type ReportPreview, type ReportSection, type ReportTemplate } from '@netops/services/patrolReports'

const names: Record<ReportSection, string> = { 'narrative.summary': '巡检概述', 'changes.summary': '变化总结' }

function EvidenceText({ value }: { value?: EvidenceLine | null }) {
  if (!value) return <span>—</span>
  const spans = [...(value.anomaly_spans || []), ...(value.anomaly_span ? [value.anomaly_span] : [])]
  const bounds = [...new Set([0, value.text.length, ...spans.flat()])].sort((a, b) => a - b)
  return <><span style={{ color: '#888' }}>{value.line} </span>{bounds.slice(0, -1).map((a, i) => <span key={a} style={{ color: spans.some(([s, e]) => a >= s && bounds[i + 1] <= e) ? '#c00000' : undefined }}>{value.text.slice(a, bounds[i + 1])}</span>)}</>
}

export default function PatrolReportDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const admin = true
  const [templates, setTemplates] = useState<ReportTemplate[]>([])
  const [templateId, setTemplateId] = useState('builtin-v1')
  const [metadata, setMetadata] = useState<Record<string, string>>({})
  const [local, setLocal] = useState<ReportPreview>()
  const [ai, setAi] = useState(false)
  const [sections, setSections] = useState<ReportSection[]>(['narrative.summary', 'changes.summary'])
  const [preview, setPreview] = useState<DraftPreview>()
  const [result, setResult] = useState<DraftResult>()
  const [accepted, setAccepted] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [evidence, setEvidence] = useState<{ total: number; source_digest: string; items: ReportEvidence[] }>({ total: 0, source_digest: '', items: [] })
  const [fullEvidence, setFullEvidence] = useState<Record<string, { baseline: string | null; current: string | null }>>({})
  const [page, setPage] = useState(1)
  const invalidate = () => { setPreview(undefined); setResult(undefined); setAccepted(false) }
  const action = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError('')
    try { await fn() } catch (e: any) {
      let detail = e?.response?.data?.detail || e?.message || '操作失败，请重试'
      if (e?.response?.data instanceof Blob) { try { detail = JSON.parse(await e.response.data.text()).detail || detail } catch { /* keep readable fallback */ } }
      setError(typeof detail === 'string' ? detail : '请求无效，请检查报告选项')
    } finally { setBusy('') }
  }
  useEffect(() => {
    let active = true
    Promise.all([patrolReports.templates(), patrolReports.preview(jobId)]).then(([ts, p]) => { if (active) { setTemplates(ts); setLocal(p) } }).catch(e => { if (active) setError(typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : e?.message || '加载报告信息失败，请关闭后重试') })
    return () => { active = false }
  }, [jobId])
  const exportReport = (bundle: boolean) => action('export', async () => {
    const blob = await patrolReports.export(jobId, { template_id: templateId, metadata, draft_id: ai && accepted ? result?.id : undefined, bundle })
    downloadBlob(blob, `巡检报告-${jobId}.${bundle ? 'zip' : 'docx'}`)
    message.success('报告已生成并开始下载')
  })
  const loadEvidence = (next: number) => action('evidence', async () => { setEvidence(await patrolReports.evidence(jobId, (next - 1) * 10)); setFullEvidence({}); setPage(next) })
  return <Modal title="生成巡检报告" open onCancel={busy ? undefined : onClose} maskClosable={false} width={1040} footer={<Space wrap>
    <Typography.Text type="secondary">{ai && accepted ? '使用已采纳的 AI 段落' : '本地生成，不向 AI 发送巡检数据'}</Typography.Text>
    <Button loading={busy === 'details'} disabled={!!busy || !local} onClick={() => action('details', async () => {
      downloadBlob(await patrolReports.details(jobId), '巡检详细数据.xlsx')
      message.success('巡检详细数据已生成并开始下载')
    })}>导出详细数据 Excel</Button>
    <Button disabled={!!busy} onClick={() => exportReport(true)}>导出报告与证据包</Button>
    <Button type="primary" loading={busy === 'export'} disabled={!!busy || !local} onClick={() => exportReport(false)}>导出 Word</Button>
  </Space>}>
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message={error} description={local && ai ? 'AI 起草失败不影响本地报告导出。' : undefined} />}
      <Space wrap>
        <Typography.Text>报告模板</Typography.Text>
        <Select aria-label="报告模板" style={{ minWidth: 260 }} value={templateId} disabled={!!busy} options={templates.map(t => ({ value: t.id, label: t.name }))} onChange={id => { setTemplateId(id); invalidate() }} />
        <Button disabled={!!busy} onClick={() => action('template', async () => downloadBlob(await patrolReports.templateFile(templateId), '巡检报告模板.docx'))}>下载模板</Button>
        {admin && <Upload accept=".docx" showUploadList={false} disabled={!!busy} beforeUpload={file => { void action('import', async () => { const t = await patrolReports.importTemplate(file); setTemplates(await patrolReports.templates()); setTemplateId(t.id); invalidate(); message.success('模板已导入') }); return false }}><Button disabled={!!busy}>导入模板</Button></Upload>}
      </Space>
      <Typography.Text type="secondary">可编辑下载模板的文字、样式和表头。保留数据控件及结论、依据、覆盖限制；动态 CLI 附录由系统生成。</Typography.Text>
      <Tabs onChange={key => { if (key === 'evidence') void loadEvidence(1) }} items={[
        { key: 'report', label: '报告内容', children: <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Space wrap>{[['project', '项目名称'], ['period_label', '巡检周期'], ['issuer', '编制单位'], ['recipient', '报送单位'], ['author', '编制人'], ['reviewer', '审核人']].map(([key, label]) => <label key={key}>{label}<Input maxLength={256} placeholder="待填写" value={metadata[key]} disabled={!!busy} onChange={e => setMetadata({ ...metadata, [key]: e.target.value })} /></label>)}</Space>
          {local ? <Alert type="info" message={local.conclusion || '判定信息'} description={local.limitations} /> : <Spin />}
          {local && <Collapse items={Object.entries(local.text).map(([id, text]) => ({ key: id, label: `${names[id as ReportSection]} · 本地生成`, children: <p style={{ whiteSpace: 'pre-wrap' }}>{text}</p> }))} />}
          <Space><Switch aria-label="使用 AI 起草" checked={ai} disabled={!!busy} onChange={value => { setAi(value); invalidate() }} /><Typography.Text>使用 AI 起草概述和变化总结</Typography.Text></Space>
          {ai && <>
            <Alert type="warning" showIcon message="汇总统计也可能敏感，请检查实际发送内容" description="仅发送数量、受控风险分类和覆盖限制。完整报告、模板、CLI、设备标识、地址、配置和日志均不发送。AI 不修改表格、结论和证据；采纳前请复核文字。" />
            <Checkbox.Group disabled={!!busy} value={sections} options={Object.entries(names).map(([value, label]) => ({ value, label }))} onChange={values => { setSections(values as ReportSection[]); invalidate() }} />
            <Button disabled={!!busy || !sections.length} onClick={() => action('preview', async () => { invalidate(); setPreview(await patrolReports.prepare(jobId, templateId, sections)) })}>预览将发送的内容</Button>
            {preview && <>
              <Typography.Text>发送至 {preview.provider.endpoint} · {preview.provider.model} · {preview.bytes} 字节。预览 15 分钟内有效。</Typography.Text>
              <pre style={{ maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', padding: 12, background: '#f5f5f5' }}>{JSON.stringify(preview.request, null, 2)}</pre>
              <Button disabled={!!busy || !!result} onClick={() => action('generate', async () => { const next = await patrolReports.generate(jobId, preview, templateId); setResult(next); if (next.error) setError(next.error) })}>确认发送并起草</Button>
            </>}
            {result?.output?.sections.map(section => <div key={section.id}><Typography.Text strong>{names[section.id]} · AI 建议</Typography.Text>{section.paragraphs.length ? section.paragraphs.map((p, i) => <p key={i}>{p.text}<Typography.Text type="secondary"> [{p.fact_ids.join(', ')}]</Typography.Text></p>) : <p>未生成建议，继续保留本地段落。</p>}</div>)}
            {result?.state === 'ready' && <Button disabled={!!busy || accepted} onClick={() => action('accept', async () => { await patrolReports.accept(jobId, result.id, templateId); setAccepted(true) })}>{accepted ? '已采纳，导出时使用' : '核对后采纳这些段落'}</Button>}
          </>}
        </Space> },
        { key: 'evidence', label: `CLI 证据核对${local ? `（${local.evidence_count}）` : ''}`, children: <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">按异常对象摘录，红色为可靠定位的异常值。原始行号可能不连续；完整原文随证据包提供。</Typography.Text>
          {busy === 'evidence' ? <Spin /> : <Collapse items={evidence.items.map(entry => ({ key: entry.id, label: `${entry.id} · ${entry.device} · ${entry.entity}`, children: <>
            <p>{entry.command} · {entry.references.join('、')}</p><p>{entry.note}</p>
            <Button disabled={!!busy} size="small" onClick={() => action('full', async () => {
              if (fullEvidence[entry.id]) { const next = { ...fullEvidence }; delete next[entry.id]; setFullEvidence(next) }
              else setFullEvidence({ ...fullEvidence, [entry.id]: await patrolReports.fullEvidence(jobId, entry.id, evidence.source_digest) })
            })}>{fullEvidence[entry.id] ? '收起完整原文' : '查看此命令完整原文'}</Button>
            {fullEvidence[entry.id] && <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>{(['baseline', 'current'] as const).map(side => <div key={side}><Typography.Text strong>{side === 'baseline' ? '基线全文' : '当前全文'}</Typography.Text><pre style={{ maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: '#f5f5f5', padding: 8 }}>{fullEvidence[entry.id][side] ?? '未提供原文'}</pre></div>)}</div>}
            <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'collapse' }}><thead><tr><th>基线 CLI 原文</th><th>当前 CLI 原文</th></tr></thead><tbody>{entry.rows.map((row, i) => <tr key={i}>{(['baseline', 'current'] as const).map(side => <td key={side} style={{ verticalAlign: 'top', border: '1px solid #ddd', padding: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'monospace', color: row.change === 'equal' ? undefined : '#8b6508' }}><EvidenceText value={row[side]} /></td>)}</tr>)}</tbody></table></div>
          </> }))} />}
          {!evidence.total && busy !== 'evidence' && <Typography.Text>本次未形成此类证据；不等同于全部正常。</Typography.Text>}
          <Pagination current={page} pageSize={10} total={evidence.total} showSizeChanger={false} disabled={!!busy} onChange={next => void loadEvidence(next)} />
        </Space> },
      ]} />
    </Space>
  </Modal>
}
