// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { create } from 'zustand'
import type { AgentProgress } from '../components/TraceProgress'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  agentType?: string
  celeryTaskId?: string
  downloadUrl?: string
  references?: any[]
  createdAt?: string
  progress?: AgentProgress
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
  status?: string
  summary?: string
  createdAt?: string
  updatedAt?: string
  messageCount?: number
  detailLoaded?: boolean
}

interface ChatStore {
  reset: () => void
  currentConversationId: string | null
  conversations: Conversation[]
  loading: boolean
  detailLoadingId: string | null
  error: string | null
  setCurrentConversation: (id: string) => void
  addMessage: (message: Message) => void
  createNewConversation: () => void
  loadConversations: () => Promise<void>
  /** Older conversations arrive a page at a time, as the history list is scrolled. */
  hasMoreConversations: boolean
  loadingMoreConversations: boolean
  loadMoreConversations: () => Promise<void>
  /** Every conversation, so that a search covers the whole history and not only what was scrolled to. */
  loadAllConversations: () => Promise<void>
  refreshConversationMeta: () => Promise<void>
  loadConversationDetail: (id: string) => Promise<void>
  updateConversationTitle: (id: string, title: string) => void
  deleteConversation: (id: string) => void
}

let chatEpoch = 0
const detailInflight = new Map<string, Promise<void>>()

// The server returns 20 conversations unless asked for more.
const CONVERSATION_PAGE = 50
// How many conversations the server has already sent; the next page starts there.
let conversationOffset = 0

type RemoteConversation = {
  id: string
  title: string
  status?: string
  summary?: string
  created_at?: string
  updated_at?: string
  message_count?: number
}

const fromRemote = (c: RemoteConversation, existing?: Conversation): Conversation => {
  const hasLocalMessages = (existing?.messages.length ?? 0) > 0
  return {
    id: c.id,
    title: c.title,
    messages: hasLocalMessages ? existing!.messages : [],
    status: c.status,
    summary: c.summary,
    createdAt: c.created_at,
    updatedAt: c.updated_at,
    messageCount: c.message_count,
    detailLoaded: hasLocalMessages || (c.message_count ?? 0) === 0 || existing?.detailLoaded,
  }
}

export const useChatStore = create<ChatStore>((set, get) => ({
  reset: () => {
    chatEpoch++
    detailInflight.clear()
    conversationOffset = 0
    set({
      currentConversationId: null, conversations: [], loading: false, detailLoadingId: null, error: null,
      hasMoreConversations: false, loadingMoreConversations: false,
    })
  },
  currentConversationId: null,
  conversations: [],
  hasMoreConversations: false,
  loadingMoreConversations: false,
  loading: false,
  detailLoadingId: null,
  error: null,
  setCurrentConversation: (id: string) => set({ currentConversationId: id }),
  addMessage: (message: Message) => set(state => {
    const convId = state.currentConversationId
    if (!convId) return state
    return {
      conversations: state.conversations.map(conv =>
        conv.id === convId
          ? { ...conv, messages: [...conv.messages, message], detailLoaded: true }
          : conv
      )
    }
  }),
  createNewConversation: async () => {
    const epoch = chatEpoch
    set({ loading: true, error: null })
    try {
      const { conversationApi } = await import('../services/api')
      const newConv = await conversationApi.createConversation({ title: '新对话' })
      if (epoch !== chatEpoch) return
      const conversation: Conversation = {
        id: newConv.id,
        title: newConv.title,
        messages: [],
        status: newConv.status,
        summary: newConv.summary,
        createdAt: newConv.created_at,
        updatedAt: newConv.updated_at,
        messageCount: 0,
        detailLoaded: true,
      }
      set(state => ({
        conversations: [conversation, ...state.conversations],
        currentConversationId: conversation.id
      }))
    } catch (error) {
      if (epoch !== chatEpoch) return
      console.error('Failed to create conversation:', error)
      const newId = `conv-${Date.now()}`
      const newConv: Conversation = {
        id: newId,
        title: '新对话',
        messages: [],
        detailLoaded: true,
      }
      set(state => ({
        conversations: [...state.conversations, newConv],
        currentConversationId: newId
      }))
    } finally {
      if (epoch === chatEpoch) set({ loading: false })
    }
  },
  loadConversations: async () => {
    const epoch = chatEpoch
    if (get().loading) return
    set({ loading: true, error: null })
    try {
      const { conversationApi } = await import('../services/api')
      const data = await conversationApi.getConversations({ limit: CONVERSATION_PAGE, offset: 0 })
      if (epoch !== chatEpoch) return
      conversationOffset = data.length
      set((state) => ({
        conversations: data.map((c) => fromRemote(c, state.conversations.find((conv) => conv.id === c.id))),
        hasMoreConversations: data.length === CONVERSATION_PAGE,
      }))
      if (data.length > 0 && !get().currentConversationId) {
        set({ currentConversationId: data[0].id })
      }
    } catch (error) {
      if (epoch !== chatEpoch) return
      console.error('Failed to load conversations:', error)
      set({ error: '加载对话列表失败' })
    } finally {
      if (epoch === chatEpoch) set({ loading: false })
    }
  },
  loadMoreConversations: async () => {
    const epoch = chatEpoch
    const { loading, loadingMoreConversations, hasMoreConversations } = get()
    if (loading || loadingMoreConversations || !hasMoreConversations) return
    set({ loadingMoreConversations: true })
    try {
      const { conversationApi } = await import('../services/api')
      // A conversation made since the first page shifts the server's order by one; ids already listed are skipped below.
      const data = await conversationApi.getConversations({ limit: CONVERSATION_PAGE, offset: conversationOffset })
      if (epoch !== chatEpoch) return
      conversationOffset += data.length
      set((state) => {
        const known = new Set(state.conversations.map((conv) => conv.id))
        return {
          conversations: [...state.conversations, ...data.filter((c) => !known.has(c.id)).map((c) => fromRemote(c))],
          hasMoreConversations: data.length === CONVERSATION_PAGE,
        }
      })
    } catch (error) {
      if (epoch !== chatEpoch) return
      console.error('Failed to load more conversations:', error)
      set({ error: '加载更早的对话失败' })
    } finally {
      if (epoch === chatEpoch) set({ loadingMoreConversations: false })
    }
  },
  loadAllConversations: async () => {
    // Bounded so that a server which keeps answering full pages cannot loop forever.
    for (let page = 0; page < 200 && get().hasMoreConversations; page += 1) {
      const before = get().conversations.length
      await get().loadMoreConversations()
      if (get().conversations.length === before) break
    }
  },
  refreshConversationMeta: async () => {
    const epoch = chatEpoch
    try {
      const { conversationApi } = await import('../services/api')
      const data = await conversationApi.getConversations({ limit: CONVERSATION_PAGE, offset: 0 })
      if (epoch !== chatEpoch) return
      set((state) => ({
        conversations: state.conversations.map((conv) => {
          const remote = data.find((c) => c.id === conv.id)
          if (!remote) return conv
          return {
            ...conv,
            title: remote.title,
            status: remote.status,
            summary: remote.summary,
            updatedAt: remote.updated_at,
            messageCount: remote.message_count,
          }
        }),
      }))
    } catch (error) {
      console.error('Failed to refresh conversation meta:', error)
    }
  },
  loadConversationDetail: async (id: string) => {
    const epoch = chatEpoch
    const existing = get().conversations.find(c => c.id === id)
    if (existing?.detailLoaded) return
    if ((existing?.messageCount ?? 0) === 0) {
      set(state => ({
        conversations: state.conversations.map(conv =>
          conv.id === id ? { ...conv, detailLoaded: true } : conv
        ),
      }))
      return
    }
    const inflight = detailInflight.get(id)
    if (inflight) return inflight

    const task = (async () => {
      set({ detailLoadingId: id, error: null })
      try {
        const { conversationApi } = await import('../services/api')
        const data = await conversationApi.getConversation(id)
        if (epoch !== chatEpoch) return
        const messages: Message[] = data.messages.map(m => ({
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          agentType: m.agent_type,
          celeryTaskId: m.celery_task_id,
          downloadUrl: m.download_url,
          references: m.references,
          createdAt: m.created_at
        }))
        set(state => ({
          conversations: state.conversations.map(conv =>
            conv.id === id
              ? { ...conv, messages, title: data.conversation.title, detailLoaded: true }
              : conv
          ),
        }))
      } catch (error) {
        if (epoch !== chatEpoch) return
        console.error('Failed to load conversation detail:', error)
        set(state => ({
          error: '加载对话详情失败',
          conversations: state.conversations.map(conv =>
            conv.id === id ? { ...conv, detailLoaded: true } : conv
          ),
        }))
      } finally {
        if (epoch === chatEpoch) {
          detailInflight.delete(id)
          set(state => ({ detailLoadingId: state.detailLoadingId === id ? null : state.detailLoadingId }))
        }
      }
    })()

    detailInflight.set(id, task)
    return task
  },
  updateConversationTitle: (id: string, title: string) => set(state => ({
    conversations: state.conversations.map(conv =>
      conv.id === id ? { ...conv, title } : conv
    )
  })),
  deleteConversation: (id: string) => set(state => ({
    conversations: state.conversations.filter(conv => conv.id !== id),
    currentConversationId: state.currentConversationId === id
      ? state.conversations.find(c => c.id !== id)?.id || null
      : state.currentConversationId
  }))
}))
