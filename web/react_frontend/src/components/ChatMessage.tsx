// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { extractDownloadUrlFromContent } from '../utils/linkify'
import MarkdownAnswer from './MarkdownAnswer'

interface ChatMessageProps {
  role: 'user' | 'assistant'
  content: string
  agentType?: string
  downloadUrl?: string
}

const ChatMessage: React.FC<ChatMessageProps> = ({ role, content, agentType, downloadUrl }) => {
  const isUser = role === 'user'
  const resolvedDownload =
    downloadUrl || (role === 'assistant' ? extractDownloadUrlFromContent(content) : undefined)

  if (isUser) {
    return (
      <div className="grok-message grok-message-user">
        <div className="grok-message-user-bubble">{content}</div>
      </div>
    )
  }

  return (
    <article className="grok-message grok-message-assistant">
      <div className="grok-message-body"><MarkdownAnswer content={content} /></div>
      {resolvedDownload && (
        <p className="grok-message-download" style={{ marginTop: 8 }}>
          <a
            href={resolvedDownload}
            target="_blank"
            rel="noopener noreferrer"
            download
            style={{ color: '#2563eb', fontWeight: 500, textDecoration: 'underline' }}
          >
            下载文件
          </a>
        </p>
      )}
      {agentType && <footer className="grok-message-meta">via {agentType}</footer>}
    </article>
  )
}

export default ChatMessage
