import React from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './MarkdownAnswer.css'

/** Answers are untrusted: no HTML execution or automatic remote image requests. */
export default function MarkdownAnswer({ content }: { content: string }) {
  return <div className="markdown-answer"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    urlTransform={url => /^(https?:\/\/|\/api\/|#)/i.test(url) ? defaultUrlTransform(url) : ''}
    components={{
      img: ({ alt }) => <span>{alt ? `[图片：${alt}]` : '[图片]'}</span>,
      a: ({ href, children }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span>,
      table: ({ children }) => <div className="markdown-answer-table"><table>{children}</table></div>,
    }}>{content}</ReactMarkdown></div>
}
