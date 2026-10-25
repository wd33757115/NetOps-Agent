// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Avatar, Popconfirm, Spin } from 'antd'
import {
  SearchOutlined,
  EditOutlined,
  AppstoreOutlined,
  CloudOutlined,
  TeamOutlined,
  LogoutOutlined,
  SettingOutlined,
  QuestionCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { workbenchEnabled } from '../../apps/appRoutes'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { useChatStore, Conversation } from '../../store/useChatStore'

interface ChatShellSidebarProps {
  workbench?: boolean
  /** Drawer 内嵌模式：隐藏折叠按钮、始终展开 */
  embedded?: boolean
  onNavigate?: () => void
}

type HistoryGroup = 'today' | 'yesterday' | 'earlier'

function groupLabel(group: HistoryGroup): string {
  if (group === 'today') return '今天'
  if (group === 'yesterday') return '昨天'
  return '更早'
}

function historyGroup(conv: Conversation): HistoryGroup {
  const raw = conv.updatedAt || conv.createdAt
  if (!raw) return 'earlier'
  const date = new Date(raw)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)

  if (date >= startOfToday) return 'today'
  if (date >= startOfYesterday) return 'yesterday'
  return 'earlier'
}

function userInitials(username: string): string {
  const parts = username.trim().split(/\s+/)
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase()
  }
  return username.slice(0, 2).toUpperCase()
}

const ChatShellSidebar: React.FC<ChatShellSidebarProps> = ({ embedded = false, onNavigate, workbench = false }) => {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const footerRef = useRef<HTMLDivElement>(null)
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(`netops-sidebar:${user?.id}`) === 'true' } catch { return false } })
  const [desktopExpanded, setDesktopExpanded] = useState(false)
  const isCollapsed = embedded ? false : workbench ? !desktopExpanded : collapsed
  const toggleCollapsed = () => {
    if (workbench) setDesktopExpanded(value => !value)
    else { setCollapsed(value => !value); try { localStorage.setItem(`netops-sidebar:${user?.id}`, String(!collapsed)) } catch { /* optional preference */ } }
  }
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(true)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const {
    conversations,
    currentConversationId,
    setCurrentConversation,
    loadConversationDetail,
    deleteConversation,
    loading,
    error,
    loadConversations,
  } = useChatStore()

  useEffect(() => {
    if (user?.id && workbench) void loadConversations()
  }, [user?.id, workbench, loadConversations])

  const navItems = useMemo(
    () => [
      { key: 'search', icon: <SearchOutlined />, label: '搜索', action: 'search' as const },
      { key: 'new-chat', icon: <EditOutlined />, label: '新建聊天', action: 'new-chat' as const },
      ...(workbenchEnabled ? [{ key: '/workbench', icon: <AppstoreOutlined />, label: '工作台', action: 'route' as const }] : []),
      { key: '/skills', icon: <AppstoreOutlined />, label: 'Skills', action: 'route' as const },
      { key: '/storage', icon: <CloudOutlined />, label: '网盘', action: 'route' as const },
    ],
    []
  )

  const filteredConversations = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return conversations
    return conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [conversations, searchQuery])

  const groupedHistory = useMemo(() => {
    const groups: Record<HistoryGroup, Conversation[]> = {
      today: [],
      yesterday: [],
      earlier: [],
    }
    for (const conv of filteredConversations) {
      groups[historyGroup(conv)].push(conv)
    }
    return groups
  }, [filteredConversations])

  useEffect(() => {
    if (!userMenuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (footerRef.current && !footerRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [userMenuOpen])

  const go = (path: string) => {
    navigate(path)
    setUserMenuOpen(false)
    onNavigate?.()
  }

  const handleLogout = async () => {
    setUserMenuOpen(false)
    await logout()
    navigate('/login')
    onNavigate?.()
  }

  const handleNewConversation = async () => {
    await useChatStore.getState().createNewConversation()
    setSearchOpen(false)
    if (!location.pathname.startsWith('/chat')) {
      go('/chat')
    }
    onNavigate?.()
  }

  const handleGoWelcome = () => {
    void handleNewConversation()
  }

  const handleNavClick = (item: (typeof navItems)[number]) => {
    if (item.action === 'search') {
      setSearchOpen((v) => !v)
      if (!searchOpen) setHistoryOpen(true)
      return
    }
    if (item.action === 'new-chat') {
      void handleNewConversation()
      return
    }
    go(item.key)
  }

  const handleSelectConversation = async (conv: Conversation) => {
    setCurrentConversation(conv.id)
    if (!conv.detailLoaded && conv.messages.length === 0 && (conv.messageCount ?? 0) > 0) {
      await loadConversationDetail(conv.id)
    }
    if (!location.pathname.startsWith('/chat')) {
      go('/chat')
    }
    onNavigate?.()
  }

  const handleDeleteConversation = async (id: string) => {
    setDeletingId(id)
    try {
      const { conversationApi } = await import('../../services/api')
      await conversationApi.deleteConversation(id)
      deleteConversation(id)
    } finally {
      setDeletingId(null)
    }
  }

  const displayName = user?.username || '用户'
  const displayEmail = user?.email || ''

  return (
    <aside className={`grok-sidebar${isCollapsed ? ' is-collapsed' : ''}${embedded ? ' is-embedded' : ''}`}>
      <div className="grok-sidebar-header">
        <button
          type="button"
          className="grok-brand-mark"
          onClick={handleGoWelcome}
          aria-label="欢迎页"
          title="欢迎页"
        />
        {!embedded && (
          <button
            type="button"
            className="grok-collapse-btn"
            onClick={toggleCollapsed}
            aria-label={isCollapsed ? '展开侧栏' : '收起侧栏'}
          >
            {isCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </button>
        )}
      </div>

      <nav className="grok-nav-list">
        {navItems.map((item) => {
          const isActive =
            item.action === 'route' && location.pathname.startsWith(item.key)
          const isSearchActive = item.action === 'search' && searchOpen
          return (
            <button
              key={item.key}
              type="button"
              className={`grok-nav-item${isActive || isSearchActive ? ' is-active' : ''}`}
              onClick={() => handleNavClick(item)}
              title={isCollapsed ? item.label : undefined}
            >
              <span className="grok-nav-icon">{item.icon}</span>
              {!isCollapsed && <span className="grok-nav-label">{item.label}</span>}
            </button>
          )
        })}
      </nav>

      {!isCollapsed && (
        <div className="grok-history-section">
          <button
            type="button"
            className="grok-history-header"
            onClick={() => setHistoryOpen((v) => !v)}
          >
            {historyOpen ? <DownOutlined className="grok-history-chevron" /> : <RightOutlined className="grok-history-chevron" />}
            <span>历史记录</span>
          </button>

          {historyOpen && (
            <>
              {searchOpen && (
                <div className="grok-history-search">
                  <SearchOutlined />
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="搜索对话…"
                    autoFocus
                  />
                </div>
              )}

              <div className="grok-history">
                {error && <div className="grok-history-error" role="alert">{error}<button type="button" onClick={() => void loadConversations()}>重试</button></div>}
                {loading && conversations.length === 0 ? (
                  <div className="grok-history-loading">
                    <Spin size="small" />
                  </div>
                ) : filteredConversations.length === 0 ? (
                  <p className="grok-history-empty">暂无对话</p>
                ) : (
                  (['today', 'yesterday', 'earlier'] as HistoryGroup[]).map((group) => {
                    const items = groupedHistory[group]
                    if (items.length === 0) return null
                    return (
                      <div key={group} className="grok-history-group">
                        <div className="grok-history-group-label">{groupLabel(group)}</div>
                        {items.map((conv) => (
                          <div
                            key={conv.id}
                            className={`grok-history-item${conv.id === currentConversationId ? ' is-active' : ''}`}
                          >
                            <button
                              type="button"
                              className="grok-history-link"
                              onClick={() => handleSelectConversation(conv)}
                            >
                              {conv.title}
                            </button>
                            <Popconfirm
                              title="删除此对话？"
                              onConfirm={() => handleDeleteConversation(conv.id)}
                              okText="删除"
                              cancelText="取消"
                            >
                              <button
                                type="button"
                                className="grok-history-delete"
                                aria-label="删除对话"
                                onClick={(e) => e.stopPropagation()}
                                disabled={deletingId === conv.id}
                              >
                                ×
                              </button>
                            </Popconfirm>
                          </div>
                        ))}
                      </div>
                    )
                  })
                )}
              </div>
            </>
          )}
        </div>
      )}

      <div className="grok-sidebar-footer" ref={footerRef}>
        {userMenuOpen && !isCollapsed && (
          <div className="grok-user-menu" role="menu">
            <button type="button" className="grok-user-menu-item" onClick={() => go('/status')}>
              <SettingOutlined />
              <span>设置</span>
            </button>
            <button type="button" className="grok-user-menu-item" onClick={() => go('/skills')}>
              <AppstoreOutlined />
              <span>Skills 和连接器</span>
            </button>
            {(user?.role === 'admin' || user?.permissions?.includes('apps.manage')) && (
              <button type="button" className="grok-user-menu-item" onClick={() => go(user?.role === 'admin' ? '/system/users' : '/system/apps')}>
                <TeamOutlined />
                <span>系统管理</span>
              </button>
            )}
            <button type="button" className="grok-user-menu-item" onClick={() => go('/status')}>
              <QuestionCircleOutlined />
              <span>帮助</span>
              <RightOutlined className="grok-user-menu-arrow" />
            </button>
            <div className="grok-user-menu-divider" />
            <button type="button" className="grok-user-menu-item is-danger" onClick={handleLogout}>
              <LogoutOutlined />
              <span>退出登录</span>
            </button>
          </div>
        )}

        <button
          type="button"
          className={`grok-user-profile${userMenuOpen ? ' is-open' : ''}`}
          onClick={() => setUserMenuOpen((v) => !v)}
          aria-expanded={userMenuOpen}
        >
          <Avatar size={36} className="grok-user-avatar">
            {userInitials(displayName)}
          </Avatar>
          {!isCollapsed && (
            <div className="grok-user-info">
              <span className="grok-user-name">{displayName}</span>
              <span className="grok-user-email">{displayEmail}</span>
            </div>
          )}
        </button>
      </div>
    </aside>
  )
}

export default ChatShellSidebar

