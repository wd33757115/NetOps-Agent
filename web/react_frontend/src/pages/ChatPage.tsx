// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useRef, useEffect, useMemo } from 'react'
import { message } from 'antd'
import { useMutation } from 'react-query'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import ChatMessage from '../components/ChatMessage'
import InputArea from '../components/InputArea'
import TraceProgress, { type TraceStep } from '../components/TraceProgress'
import { useChatStore } from '../store/useChatStore'
import { chatApi, ChatResponse, conversationApi } from '../services/api'
import { streamChatMessage } from '../services/chatStream'
import { useAuth } from '../context/AuthContext'

const SUGGESTIONS = [
  '备份生产环境设备配置',
  '对 prod 分组执行设备巡检',
  '交换机端口 Down 了如何排查？',
]

const ChatPage: React.FC = () => {
  const { user } = useAuth()
  const [threadId, setThreadId] = useState<string>('')
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([])
  const [traceStatus, setTraceStatus] = useState<string>('')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [langfuseUrl, setLangfuseUrl] = useState<string | null>(null)
  const streamAbortRef = useRef<AbortController | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const prevMessageCountRef = useRef(0)
  const initialized = useRef(false)

  const {
    currentConversationId,
    conversations,
    addMessage,
    uploadedFile,
    loadConversationDetail,
    loadConversations,
    refreshConversationMeta,
  } = useChatStore()

  const currentMessages = useMemo(
    () => conversations.find((c) => c.id === currentConversationId)?.messages || [],
    [conversations, currentConversationId],
  )

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      loadConversations().then(() => {
        const state = useChatStore.getState()
        if (!state.currentConversationId) {
          useChatStore.getState().createNewConversation()
        }
      })
    }
  }, [loadConversations])

  useEffect(() => {
    if (!currentConversationId) return
    const conv = conversations.find((c) => c.id === currentConversationId)
    if (!conv || conv.detailLoaded || conv.messages.length > 0) return
    loadConversationDetail(currentConversationId)
  }, [currentConversationId, conversations, loadConversationDetail])

  useEffect(() => {
    if (currentConversationId) {
      setThreadId(currentConversationId)
    }
  }, [currentConversationId])

  const sendMessageMutation = useMutation<ChatResponse, Error, string>({
    mutationFn: async (query) => {
      let convId = currentConversationId
      if (!convId) {
        const newConv = await conversationApi.createConversation({ title: '新对话' })
        convId = newConv.id
        setThreadId(convId)
        useChatStore.setState((state) => ({
          conversations: [
            { id: newConv.id, title: newConv.title, messages: [], detailLoaded: true },
            ...state.conversations,
          ],
          currentConversationId: newConv.id,
        }))
      }

      streamAbortRef.current?.abort()
      const controller = new AbortController()
      streamAbortRef.current = controller
      setTraceSteps([])
      setTraceStatus('Agent 开始执行...')
      setTraceId(null)
      setLangfuseUrl(null)

      try {
        return await streamChatMessage(
          {
            query,
            thread_id: convId || undefined,
            uploaded_file_path: uploadedFile?.path,
          },
          {
            signal: controller.signal,
            onStatus: setTraceStatus,
            onStep: (step) => setTraceSteps((prev) => [...prev, step]),
            onEvent: (event, data) => {
              if (event === 'trace_start' && data.trace_id) {
                setTraceId(String(data.trace_id))
              }
              if (event === 'final_answer' && data.langfuse_url) {
                setLangfuseUrl(String(data.langfuse_url))
              }
            },
          }
        )
      } catch (err) {
        if (controller.signal.aborted) {
          throw err
        }
        return chatApi.sendMessage({
          query,
          thread_id: convId || undefined,
          uploaded_file_path: uploadedFile?.path,
        })
      }
    },
    onMutate: async (query) => {
      addMessage({
        id: `user-${Date.now()}`,
        role: 'user',
        content: query,
      })
    },
    onSuccess: async (data) => {
      setTraceSteps([])
      setTraceStatus('')
      addMessage({
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: data.response,
        agentType: data.agent_type,
        celeryTaskId: data.celery_task_id,
        downloadUrl: data.download_url,
        references: data.references,
      })

      if (data.thread_id !== threadId) {
        setThreadId(data.thread_id)
      }

      void refreshConversationMeta()
    },
    onError: (error) => {
      setTraceSteps([])
      setTraceStatus('')
      message.error(`发送失败: ${error.message}`)
    },
  })

  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort()
    }
  }, [])

  const handleSend = (text: string) => {
    if (!currentConversationId) {
      message.error('请先选择或创建一个对话')
      return
    }
    sendMessageMutation.mutate(text)
  }

  useEffect(() => {
    const count = currentMessages.length
    const prev = prevMessageCountRef.current
    prevMessageCountRef.current = count

    if (count === 0 && !sendMessageMutation.isLoading) return

    const container = messagesScrollRef.current
    const nearBottom =
      !container ||
      container.scrollHeight - container.scrollTop - container.clientHeight < 120

    if (!nearBottom && count <= prev) return

    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({
        behavior: count - prev > 1 ? 'auto' : 'smooth',
        block: 'end',
      })
    })
  }, [currentMessages, sendMessageMutation.isLoading])

  return (
    <GrokShellLayout
      mode="chat"
      scrollRef={messagesScrollRef}
      footer={
        <InputArea onSend={handleSend} loading={sendMessageMutation.isLoading} />
      }
    >
      {currentMessages.length === 0 && !sendMessageMutation.isLoading && (
        <div className="grok-welcome">
          <h1 className="grok-welcome-title">今天有什么可以帮你？</h1>
          <div className="grok-suggestions">
            {SUGGESTIONS.map((text) => (
              <button
                key={text}
                type="button"
                className="grok-suggestion"
                onClick={() => handleSend(text)}
              >
                {text}
              </button>
            ))}
          </div>
        </div>
      )}

      {currentMessages.map((msg) => (
        <ChatMessage
          key={msg.id}
          role={msg.role}
          content={msg.content}
          agentType={msg.agentType}
          downloadUrl={msg.downloadUrl}
        />
      ))}

      {sendMessageMutation.isLoading && (
        <TraceProgress
          steps={traceSteps}
          statusMessage={traceStatus}
          traceId={traceId}
          langfuseUrl={langfuseUrl}
          isAdmin={user?.role === 'admin'}
        />
      )}

      <div ref={messagesEndRef} />
    </GrokShellLayout>
  )
}

export default ChatPage
