import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Typography } from 'antd'
import { api } from '@netops/config/api'

interface Run { id: string; text: string; status: string; result: string | null }

export default function DeliveryProbe() {
  const [text, setText] = useState('NetOps')
  const [run, setRun] = useState<Run | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pendingRequest = useRef<{ text: string; id: string } | null>(null)
  useEffect(() => {
    const id = run?.id || sessionStorage.getItem('delivery-probe-run')
    if (!id || (run && ['completed', 'cancelled'].includes(run.status))) return
    const timer = window.setInterval(() => {
      void api.get<Run>(`/apps/delivery-probe/runs/${id}`).then(r => { if (!run) setText(r.data.text); setRun(r.data) }).catch(() => { setError('无法读取记录，请检查应用权限。'); window.clearInterval(timer) })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [run])
  return <section style={{ padding: 32, maxWidth: 720, fontFamily: 'system-ui, sans-serif' }}>
    <Typography.Title level={2}>能力包接入验证</Typography.Title>
    <Typography.Paragraph type="secondary">提交一段文本，查看异步处理结果。此应用用于验证业务包的完整接入流程。</Typography.Paragraph>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('')
      if (pendingRequest.current?.text !== text) pendingRequest.current = { text, id: crypto.randomUUID() }
      try {
        const result = await api.post<Run>('/apps/delivery-probe/runs', { request_id: pendingRequest.current.id, text })
        setRun(result.data); sessionStorage.setItem('delivery-probe-run', result.data.id)
        pendingRequest.current = null
      } catch { setError('提交失败，请稍后重试。') }
      finally { setBusy(false) }
    }}>
      <label style={{ display: 'block', marginBottom: 16 }}>验证文本
        <Input.TextArea style={{ marginTop: 8 }} rows={3} aria-label="验证文本" maxLength={500} required value={text} onChange={e => setText(e.target.value)} />
      </label>
      <Button loading={busy} type="primary" htmlType="submit">提交验证</Button>
    </form>
    {error && <Alert style={{ marginTop: 24 }} type="error" message={error} />}
    {run && <div aria-live="polite" style={{ marginTop: 28, paddingTop: 16, borderTop: '1px solid #e5e7eb' }}><Typography.Paragraph>状态：{({ queued: '等待处理', running: '处理中', completed: '已完成', cancelled: '已取消' } as Record<string, string>)[run.status]}</Typography.Paragraph><Typography.Paragraph>结果：{run.result || '等待处理结果'}</Typography.Paragraph></div>}
  </section>
}
