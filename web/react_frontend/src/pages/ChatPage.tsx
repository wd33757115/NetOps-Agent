import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import { Alert, message } from 'antd'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import ChatMessage from '../components/ChatMessage'
import EvidenceLinks from '../components/EvidenceLinks'
import InputArea, { type ChatAttachment } from '../components/InputArea'
import TraceProgress, { isRunning, pendingProgress, type AgentProgress } from '../components/TraceProgress'
import { useChatStore, type Message } from '../store/useChatStore'
import { streamChatMessage } from '../services/chatStream'
import { api } from '../config/api'

const SUGGESTIONS = ['查找资料中关于 BGP EVPN 的配置要求', '结合已有巡检记录分析异常', '交换机端口 Down 了如何排查？']
interface History {
  messages: (Message & { progress?: AgentProgress })[]
  latest_turn: { id: string; status: string; result_message_id?: string; progress?: AgentProgress } | null
}

export default function ChatPage() {
  const [progressByConversation, setProgressByConversation] = useState<Record<string, AgentProgress | undefined>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [sending, setSending] = useState('')
  const streamRef = useRef<{ conversation: string; controller: AbortController } | null>(null)
  const generations = useRef<Record<string, number>>({})
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)
  const { currentConversationId, conversations, addMessage, loadConversations, refreshConversationMeta } = useChatStore()
  const selected = currentConversationId || ''
  const currentMessages = useMemo(() => conversations.find(c => c.id === selected)?.messages || [], [conversations, selected])
  const progress = progressByConversation[selected]
  const busy = !!sending || isRunning(progress)

  const updateProgress = useCallback((id: string, value?: AgentProgress) => {
    setProgressByConversation(prev => ({ ...prev, [id]: value }))
  }, [])
  const refreshHistory = useCallback(async (id: string) => {
    const generation = generations.current[id] || 0
    const { data } = await api.get<History>(`/apps/assistant/conversations/${id}`)
    if ((generations.current[id] || 0) !== generation || streamRef.current?.conversation === id) return
    useChatStore.setState(state => ({ conversations: state.conversations.map(c => c.id === id
      ? { ...c, messages: data.messages, detailLoaded: true } : c) }))
    updateProgress(id, data.latest_turn?.result_message_id ? undefined : data.latest_turn?.progress)
    return data.latest_turn?.status
  }, [updateProgress])

  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    void loadConversations().then(() => {
      if (!useChatStore.getState().currentConversationId) useChatStore.getState().createNewConversation()
    })
  }, [loadConversations])

  useEffect(() => {
    if (!selected || sending === selected) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const status = await refreshHistory(selected)
        // Poll only an unfinished turn; this also recovers a disconnected stream.
        if (!disposed) {
          if (['running', 'cancel_requested'].includes(status || '')) timer = setTimeout(refresh, 2000)
        }
      } catch {
        if (!disposed) setErrors(prev => ({ ...prev, [selected]: '无法读取会话状态，请刷新重试。' }))
      }
    }
    void refresh()
    return () => { disposed = true; clearTimeout(timer) }
  }, [selected, sending, refreshHistory])

  useEffect(() => () => { streamRef.current?.controller.abort() }, [])
  useEffect(() => {
    const container = messagesScrollRef.current
    if (!container || container.scrollHeight - container.scrollTop - container.clientHeight < 180) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [currentMessages, progress?.steps.length, progress?.message])

  async function handleSend(query: string, attachments: ChatAttachment[] = []) {
    if (busy || !query.trim()) return
    if (!selected) { message.error('请先选择或创建一个对话'); return }
    const id = selected
    generations.current[id] = (generations.current[id] || 0) + 1
    const controller = new AbortController()
    streamRef.current = { conversation: id, controller }
    setSending(id)
    setErrors(prev => ({ ...prev, [id]: '' }))
    updateProgress(id, pendingProgress())
    addMessage({ id: `user-${Date.now()}`, role: 'user', content: query,
      references: attachments.map(file => ({ kind: 'attachment', ...file })) })
    let terminalStatus = 'running'
    try {
      const result = await streamChatMessage({ query, thread_id: id, attachments: attachments.map(file => file.file_id) }, {
        signal: controller.signal, onProgress: value => { terminalStatus = value.status; updateProgress(id, value) },
      })
      // Keep the answer even if a subsequent history refresh fails; bind to its original conversation.
      useChatStore.setState(state => ({ conversations: state.conversations.map(c => c.id === id
        ? { ...c, messages: [...c.messages, { id: `assistant-${Date.now()}`, role: 'assistant', content: result.response, references: result.references }] } : c) }))
    } catch (error) {
      if (!controller.signal.aborted && terminalStatus !== 'cancelled') setErrors(prev => ({ ...prev, [id]: error instanceof Error ? error.message : '问答未完成，请刷新查看本轮状态。' }))
      setProgressByConversation(prev => {
        const value = prev[id]
        return { ...prev, [id]: value && isRunning(value) ? {
          ...value, status: controller.signal.aborted ? 'cancelled' : 'interrupted',
          message: controller.signal.aborted ? '已停止连接，正在核对本轮状态' : '连接已中断，正在核对本轮状态',
          ended_at: new Date().toISOString(),
        } : value }
      })
    } finally {
      streamRef.current = null
      setSending('')
      void refreshConversationMeta()
    }
  }

  async function stop() {
    if (!progress) return
    if (!progress.run_id) { streamRef.current?.controller.abort(); return }
    try {
      const { data } = await api.post<{ progress: AgentProgress }>(`/apps/assistant/turns/${progress.run_id}/cancel`)
      updateProgress(selected, data.progress)
    } catch { setErrors(prev => ({ ...prev, [selected]: '停止请求失败，请重试。' })) }
  }

  return <GrokShellLayout mode="chat" scrollRef={messagesScrollRef}
    footer={<InputArea onSend={(text, attachments) => void handleSend(text, attachments)} loading={busy} />}>
    {currentMessages.length === 0 && !busy && <div className="grok-welcome">
      <h1 className="grok-welcome-title">今天有什么可以帮你？</h1>
      <div className="grok-suggestions">{SUGGESTIONS.map(text => <button key={text} type="button" className="grok-suggestion" onClick={() => void handleSend(text)}>{text}</button>)}</div>
    </div>}
    {currentMessages.map(msg => <React.Fragment key={msg.id}>
      {msg.progress && <TraceProgress progress={msg.progress} />}
      <ChatMessage role={msg.role} content={msg.content} agentType={msg.agentType} downloadUrl={msg.downloadUrl}
        attachments={msg.role === 'user' ? msg.references?.filter(item => item?.kind === 'attachment') : undefined} />
      {msg.role === 'assistant' && <EvidenceLinks conversationId={selected} references={msg.references} />}
    </React.Fragment>)}
    {progress && <TraceProgress progress={progress} onCancel={() => void stop()} />}
    {errors[selected] && <Alert type="error" message={errors[selected]} showIcon style={{ margin: '12px 0' }} />}
    <div ref={messagesEndRef} />
  </GrokShellLayout>
}
