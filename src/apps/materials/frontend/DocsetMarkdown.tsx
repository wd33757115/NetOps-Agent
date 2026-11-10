import React, { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '@netops/config/api'

const TONES: Record<string, string> = { 说明: 'info', 窍门: 'info', 须知: 'caution', 注意: 'caution', 警告: 'danger', 危险: 'danger' }

/** Pages carry command syntax: `_` and `*` stay literal, and HTML or indented code never parse. */
function remarkLiteral(this: { data: () => { micromarkExtensions?: unknown[] } }) {
  const data = this.data()
  data.micromarkExtensions = [...(data.micromarkExtensions || []),
    { disable: { null: ['attention', 'htmlFlow', 'htmlText', 'autolink', 'codeIndented'] } }]
}

/** The first two lines of every page: its place in the TOC, then type, features and source page. */
export function splitHeader(text: string) {
  const lines = text.split('\n')
  if (lines[0]?.startsWith('> 位置：') && lines[1]?.startsWith('> ')) {
    return { crumb: lines[0].slice(5).split(' / '), meta: lines[1].slice(2).split(' | '), body: lines.slice(2).join('\n') }
  }
  return { crumb: [] as string[], meta: [] as string[], body: text }
}

function Figure({ base, src, alt }: { base: string; src: string; alt: string }) {
  const [url, setUrl] = useState<string>()
  const [failed, setFailed] = useState(false)
  const name = src.replace(/^figures\//, '')
  useEffect(() => {
    let alive = true
    let made = ''
    setUrl(undefined); setFailed(false)
    api.get<Blob>(base + name, { responseType: 'blob' })
      .then(result => { if (!alive) return; made = URL.createObjectURL(result.data); setUrl(made) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false; if (made) URL.revokeObjectURL(made) }
  }, [base, name])
  const caption = alt.replace(/^图：?/, '')
  return <span className="docset-figure">
    {url ? <img src={url} alt={caption || '文档插图'} loading="lazy" /> : <span className="docset-figure-pending">{failed ? '图片无法加载' : '正在加载图片…'}</span>}
    {caption && <span className="docset-figure-caption">{caption}</span>}
  </span>
}

function plain(node: unknown): string {
  const item = node as { value?: string; children?: unknown[] }
  return item.value ?? (item.children || []).map(plain).join('')
}

export default function DocsetMarkdown({ text, figureBase, onOpen }: { text: string; figureBase: string; onOpen?: (path: string) => void }) {
  const { crumb, meta, body } = splitHeader(text)
  return <article className="docset-page">
    {crumb.length > 0 && <nav className="docset-crumb" aria-label="在目录中的位置">{crumb.map((part, index) =>
      <span key={index} aria-current={index === crumb.length - 1 ? 'page' : undefined}>{part}</span>)}</nav>}
    {meta.length > 0 && <dl className="docset-meta">{meta.map(item => {
      const [label, ...value] = item.split('：')
      return <div key={item}><dt>{label}</dt><dd>{value.join('：')}</dd></div>
    })}</dl>}
    <div className="docset-body">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkLiteral]} skipHtml components={{
        img: ({ src, alt }) => src?.startsWith('figures/') ? <Figure base={figureBase} src={src} alt={alt || ''} /> : <span>[{alt || '图'}]</span>,
        a: ({ href, children }) => href?.startsWith('pages/') && onOpen
          ? <a href={'?path=' + encodeURIComponent(href)} onClick={event => { event.preventDefault(); onOpen(href) }}>{children}</a>
          : <span>{children}</span>,
        table: ({ children }) => <div className="docset-table"><table>{children}</table></div>,
        blockquote: ({ children, node }) => {
          const tone = TONES[(plain(node).trim().match(/^(说明|注意|警告|危险|须知|窍门)：/) || [])[1] || '']
          return <blockquote className={tone ? 'docset-note docset-note-' + tone : undefined}>{children}</blockquote>
        },
      }}>{body}</ReactMarkdown>
    </div>
  </article>
}
