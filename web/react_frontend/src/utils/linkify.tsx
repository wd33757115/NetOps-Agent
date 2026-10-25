// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'

const URL_PATTERN = /(https?:\/\/[^\s<>"')\]]+|\/api\/[^\s<>"')\]]+)/g

/** 将文本中的 http(s) URL 渲染为可点击链接 */
export function linkifyText(text: string): React.ReactNode[] {
  const parts = text.split(URL_PATTERN)
  return parts.map((part, index) => {
    if (/^https?:\/\//.test(part) || part.startsWith('/api/')) {
      const label = part.length > 72 ? `${part.slice(0, 69)}...` : part
      return (
        <a
          key={`url-${index}`}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          download
          style={{
            color: '#2563eb',
            textDecoration: 'underline',
            wordBreak: 'break-all',
          }}
        >
          {label}
        </a>
      )
    }
    return <React.Fragment key={`text-${index}`}>{part}</React.Fragment>
  })
}

/** 从助手回复正文中解析「下载:」后的 URL（兼容历史消息） */
export function extractDownloadUrlFromContent(content: string): string | undefined {
  const md = content.match(/\[([^\]]+)\]\(((?:https?:\/\/|\/api\/)[^)]+)\)/i)
  if (md?.[2]) return md[2]
  const plain = content.match(/(?:下载|download)[：:\s]+(\S+)/i)
  const candidate = plain?.[1]?.replace(/[)\],.]+$/, '')
  if (candidate && (/^https?:\/\//i.test(candidate) || candidate.startsWith('/api/'))) {
    return candidate
  }
  return undefined
}

const MD_LINK_PATTERN = /\[([^\]]+)\]\((https?:\/\/[^)]+|\/api\/[^)]+)\)/g

/** 渲染助手消息：支持 Markdown 链接 + 纯 URL 自动链接 */
export function renderAssistantContent(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  MD_LINK_PATTERN.lastIndex = 0
  while ((match = MD_LINK_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(...linkifyText(text.slice(lastIndex, match.index)))
    }
    nodes.push(
      <a
        key={`md-${match.index}`}
        href={match[2]}
        target="_blank"
        rel="noopener noreferrer"
        style={{ color: '#2563eb', fontWeight: 500, textDecoration: 'underline' }}
      >
        {match[1]}
      </a>
    )
    lastIndex = MD_LINK_PATTERN.lastIndex
  }
  if (lastIndex < text.length) {
    nodes.push(...linkifyText(text.slice(lastIndex)))
  }
  return nodes.length > 0 ? nodes : linkifyText(text)
}
