import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Empty, Input, List, Select, Space, Typography } from 'antd'
import { api } from '@netops/config/api'
import TraceProgress, { pendingProgress, type AgentProgress } from '@netops/components/TraceProgress'
import MarkdownAnswer from '@netops/components/MarkdownAnswer'
import EvidenceLinks, { type Citation } from '@netops/components/EvidenceLinks'

interface Conversation { id: string; title: string }
interface Message { id: string; role: string; content: string; references: Citation[]; progress?: AgentProgress }
interface Turn { id: string; status: string; error_code?: string; result_message_id?: string; progress?: AgentProgress }
interface History { messages: Message[]; latest_turn: Turn | null }
const base = '/apps/assistant'
const active = (turn: Turn | null) => !!turn && ['running', 'cancel_requested'].includes(turn.status)

export default function Assistant() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [selected, setSelected] = useState('')
  const selectedRef = useRef('')
  const [history, setHistory] = useState<History>({ messages: [], latest_turn: null })
  const running = active(history.latest_turn)
  const [question, setQuestion] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState<AgentProgress | null>(null)
  const [version, setVersion] = useState('')
  const retry = useRef<{ session: string; text: string; version: string; id: string } | null>(null)
  const refreshLists = useCallback(async () => {
    const chats = await api.get<Conversation[]>(`${base}/conversations`)
    setConversations(chats.data)
  }, [])
  const refreshHistory = useCallback(async (id: string) => {
    const result = await api.get<History>(`${base}/conversations/${id}`)
    if (selectedRef.current === id) setHistory(result.data)
  }, [])
  useEffect(() => { void refreshLists().catch(() => setError('无法加载应用，请检查使用权限和服务状态。')) }, [refreshLists])
  useEffect(() => {
    selectedRef.current = selected
    setHistory({ messages: [], latest_turn: null })
    if (!selected) return
    void refreshHistory(selected).catch(() => setError('无法读取会话。'))
  }, [selected, refreshHistory])
  useEffect(() => {
    if (!selected || (!sending && !running)) return
    const timer = window.setInterval(() => {
      void refreshHistory(selected).catch(() => { setError('会话刷新失败。'); window.clearInterval(timer) })
    }, 2000)
    return () => window.clearInterval(timer)
  }, [selected, sending, running, refreshHistory])

  async function submit() {
    if (!selected || !question.trim()) return
    const id = selected
    if (retry.current?.session !== id || retry.current.text !== question || retry.current.version !== version) {
      retry.current = { session: id, text: question, version, id: crypto.randomUUID() }
    }
    setSending(true); setError(''); setPending(pendingProgress())
    try {
      const result = await api.post<Turn>(`${base}/conversations/${id}/questions`, {
        request_id: retry.current.id, text: question,
        filters: version.trim() ? { software_version: version.trim() } : {},
      })
      if (!active(result.data)) { retry.current = null; setQuestion('') }
      await refreshHistory(id); await refreshLists()
    } catch { setError('问答请求未完成。可刷新查看记录；重试相同问题会复用请求编号。') }
    finally { setSending(false); setPending(null) }
  }

  return <section style={{ padding: 24, maxWidth: 1120, margin: '0 auto' }}>
    <Typography.Title level={2}>知识问答</Typography.Title>
    <Typography.Paragraph type="secondary">按需阅读资料，核对原文引用。诊断仅查询已有记录。</Typography.Paragraph>
    {error && <Alert type="error" message={error} closable onClose={() => setError('')} style={{ marginBottom: 16 }} />}
    <>
        <Space wrap style={{ marginBottom: 20 }}>
          <Select aria-label="选择会话" placeholder="选择历史会话" style={{ width: 280 }} value={selected || undefined}
            options={conversations.map(c => ({ value: c.id, label: c.title }))} onChange={setSelected} disabled={sending} />
          <Button disabled={sending} onClick={async () => {
            try { const r = await api.post<Conversation>(`${base}/conversations`); setSelected(r.data.id); await refreshLists() }
            catch { setError('创建会话失败。') }
          }}>新建会话</Button>
          <Input aria-label="问答软件版本" placeholder="软件版本（可选）" value={version} onChange={e => setVersion(e.target.value)} style={{ width: 200 }} />
        </Space>
        {!history.messages.length && <Empty description="选择或新建会话，提出你的问题" />}
        <List dataSource={history.messages} renderItem={m => <List.Item key={m.id}>
          <div style={{ width: '100%' }}>{m.progress && <TraceProgress progress={m.progress} />}<Typography.Text strong>{m.role === 'user' ? '你' : '助手'}</Typography.Text>
            <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '8px 0' }}>{m.role === 'assistant' ? <MarkdownAnswer content={m.content} /> : m.content}</div>
            <EvidenceLinks conversationId={selected} references={m.references} />
          </div>
        </List.Item>} />
        {pending && !active(history.latest_turn) && <TraceProgress progress={pending} />}
        {!history.latest_turn?.result_message_id && history.latest_turn?.progress && <TraceProgress progress={history.latest_turn.progress} />}
        <Input.TextArea aria-label="问题" rows={3} maxLength={4000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="输入问题，注明产品和软件版本" />
        <Space style={{ marginTop: 12 }}>
          <Button aria-label="发送" type="primary" loading={sending} disabled={!selected || !question.trim() || active(history.latest_turn)} onClick={() => void submit()}>发送</Button>
          {active(history.latest_turn) && <Button onClick={async () => {
            try { await api.post(`${base}/turns/${history.latest_turn!.id}/cancel`); await refreshHistory(selected) }
            catch { setError('取消请求失败，请刷新后重试。') }
          }}>取消本次问答</Button>}
        </Space>
    </>
  </section>
}
