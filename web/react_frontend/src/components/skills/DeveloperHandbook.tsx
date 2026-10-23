import React, { useEffect, useMemo, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
// The handbooks live next to this page as Markdown and are read here as written.
import platformHandbook from './handbook/platform.md?raw'
import skillHandbook from './handbook/skills.md?raw'
import workflowHandbook from './handbook/workflows.md?raw'
import { ArchitectureDiagram, LifecycleDiagram } from './HandbookDiagrams'
import './DeveloperHandbook.css'

const images = import.meta.glob('./handbook/images/*', {
  eager: true, query: '?url', import: 'default',
}) as Record<string, string>
const imageUrl = (src = '') => {
  const name = src.split('/').pop()
  const key = Object.keys(images).find(path => path.endsWith('/' + name))
  return key ? images[key] : ''
}

const plain = (children: React.ReactNode): string => React.Children.toArray(children)
  .map(child => typeof child === 'string' || typeof child === 'number'
    ? String(child)
    : React.isValidElement<{ children?: React.ReactNode }>(child) ? plain(child.props.children) : '')
  .join('')
const anchor = (text: string) => 'handbook-' + text.trim().replace(/\s+/g, '-')

interface Heading { id: string; text: string; level: 2 | 3 }

/** Chapter and section headings, skipping what sits inside code blocks. */
function outline(markdown: string): Heading[] {
  let fenced = false
  const result: Heading[] = []
  // A Windows checkout (core.autocrlf) gives the Markdown CRLF line ends.
  for (const line of markdown.split(/\r?\n/)) {
    if (line.startsWith('```')) fenced = !fenced
    const match = !fenced && /^(##|###) (.+)$/.exec(line)
    if (match) result.push({ id: anchor(match[2]), text: match[2].trim(), level: match[1].length as 2 | 3 })
  }
  return result
}

// Module scope: components made anew on each render would remount every heading,
// and the outline would lose track of the headings it watches.
const MARKDOWN_COMPONENTS: Components = {
  h2: ({ children }) => <h2 id={anchor(plain(children))}>{children}</h2>,
  h3: ({ children }) => <h3 id={anchor(plain(children))}>{children}</h3>,
  pre: ({ node, children }) => {
    const code = node?.children?.[0]
    const classes = code && 'properties' in code ? String(code.properties?.className ?? '') : ''
    if (classes.includes('language-architecture')) return <ArchitectureDiagram />
    if (classes.includes('language-lifecycle')) return <LifecycleDiagram />
    return <pre className="handbook-code">{children}</pre>
  },
  table: ({ children }) => <div className="handbook-table"><table>{children}</table></div>,
  img: ({ src, alt }) => (
    <span className="handbook-shot">
      <img src={imageUrl(typeof src === 'string' ? src : '')} alt={alt || ''} loading="lazy" />
      {alt && <span className="handbook-shot-caption">{alt}</span>}
    </span>
  ),
}

export const HANDBOOKS = { platform: platformHandbook, skill: skillHandbook, workflow: workflowHandbook }

const HandbookText = React.memo(function HandbookText({ source }: { source: string }) {
  return (
    <article className="handbook-article">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={MARKDOWN_COMPONENTS}>{source}</ReactMarkdown>
    </article>
  )
})

/** One handbook: its outline beside the text, and anything that belongs under the text (tools, records). */
export default function DeveloperHandbook({ source, children }: { source: string; children?: React.ReactNode }) {
  const headings = useMemo(() => outline(source), [source])
  const [active, setActive] = useState(headings[0]?.id)

  // The section being read is the heading that last entered the top third of the window.
  useEffect(() => {
    const nodes = headings.map(h => document.getElementById(h.id)).filter(Boolean) as HTMLElement[]
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible[0]) setActive(visible[0].target.id)
    }, { rootMargin: '0px 0px -66% 0px' })
    nodes.forEach(node => observer.observe(node))
    return () => observer.disconnect()
  }, [headings])

  const go = (id: string) => {
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    document.getElementById(id)?.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' })
    setActive(id)
  }

  return (
    <div className="dev-handbook">
      <div className="dev-handbook-layout">
        <nav className="handbook-toc" aria-label="手册目录">
          <span className="handbook-toc-title">目录</span>
          <ul>
            {headings.map(h => (
              <li key={h.id} className={`handbook-toc-${h.level}`}>
                <a href={`#${h.id}`} aria-current={active === h.id ? 'location' : undefined}
                  onClick={event => { event.preventDefault(); go(h.id) }}>{h.text}</a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="handbook-main">
          <HandbookText source={source} />
          {children}
        </div>
      </div>
    </div>
  )
}
