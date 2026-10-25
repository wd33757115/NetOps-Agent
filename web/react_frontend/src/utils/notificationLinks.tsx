// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'

/** 正文 / payload 中出现的 HTTP(S) URL */
export const HTTP_URL_RE = /https?:\/\/[^\s<>"']+/g

export interface NotificationLink {
  href: string
  label: string
}

function isHttpUrl(value: unknown): value is string {
  const text = typeof value === 'string' ? value.trim() : ''
  return /^https?:\/\//i.test(text) || text.startsWith('/api/')
}

/** 从 payload 提取全部可点击链接（优先 downloads 列表，兼容旧字段与任意 URL 字符串） */
export function extractNotificationLinks(payload?: Record<string, unknown>): NotificationLink[] {
  if (!payload) return []

  const links: NotificationLink[] = []
  const seen = new Set<string>()

  const add = (href: string, label: string) => {
    const url = href.trim()
    if (!isHttpUrl(url) || seen.has(url)) return
    seen.add(url)
    links.push({ href: url, label: label.trim() || '打开链接' })
  }

  const downloads = payload.downloads
  if (Array.isArray(downloads)) {
    for (const item of downloads) {
      if (!item || typeof item !== 'object') continue
      const rec = item as Record<string, unknown>
      const url = rec.url ?? rec.href
      if (typeof url === 'string') {
        const label =
          (typeof rec.label === 'string' && rec.label) ||
          (typeof rec.filename === 'string' && rec.filename) ||
          (typeof rec.key === 'string' && rec.key.replace(/_/g, ' ')) ||
          '打开链接'
        add(url, label)
      }
    }
  }

  for (const [key, val] of Object.entries(payload)) {
    if (key === 'downloads') continue
    if (typeof val === 'string' && isHttpUrl(val)) {
      add(val, key.replace(/_/g, ' '))
    }
  }

  return links
}

/** 将正文中的 URL 转为可点击链接（跳过已在 structured links 中出现的 URL） */
export function renderLinkifiedText(
  text: string,
  knownUrls: Set<string>,
  onLinkClick: (event: React.MouseEvent<HTMLAnchorElement>) => void,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  const re = new RegExp(HTTP_URL_RE.source, 'g')
  let match: RegExpExecArray | null

  while ((match = re.exec(text)) !== null) {
    const url = match[0]
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    if (knownUrls.has(url)) {
      nodes.push(url)
    } else {
      nodes.push(
        <a
          key={`${match.index}-${url}`}
          href={url}
          target="_blank"
          rel="noreferrer"
          onClick={onLinkClick}
        >
          {url}
        </a>,
      )
    }
    lastIndex = match.index + url.length
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes.length ? nodes : [text]
}
