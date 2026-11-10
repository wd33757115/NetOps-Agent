import React, { useEffect, useRef, useState } from 'react'
import { CheckOutlined, ReadOutlined } from '@ant-design/icons'
import { api } from '../config/api'
import './DocsetPicker.css'

export interface Docset { id: string; title: string; metadata: Record<string, string>; pages?: number | null }

const LIMIT = 3
let catalog: Promise<Docset[]> | null = null
const loadCatalog = () => (catalog ??= api.get<Docset[]>('/apps/assistant/docsets')
  .then(({ data }) => data)
  .catch(error => { catalog = null; throw error }))

/** Limit the next questions to chosen product doc sets; none chosen lets the question's models decide. */
export default function DocsetPicker({ value, onChange, disabled }: { value: string[]; onChange: (ids: string[]) => void; disabled?: boolean }) {
  const [docsets, setDocsets] = useState<Docset[]>([])
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => { void loadCatalog().then(setDocsets).catch(() => undefined) }, [])
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !box.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])

  if (!docsets.length) return null
  const chosen = docsets.filter(d => value.includes(d.id))
  const label = chosen.length === 0 ? '产品文档' : chosen.length === 1 ? chosen[0].title : `${chosen.length} 个文档集`
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter(item => item !== id) : value.length < LIMIT ? [...value, id] : value)

  return <div className="docset-picker" ref={box}>
    <button type="button" className={`docset-picker-toggle${chosen.length ? ' is-chosen' : ''}`} aria-haspopup="dialog" aria-expanded={open}
      disabled={disabled} title={chosen.map(d => d.title).join('\n') || '选择产品文档集'}
      aria-label={chosen.length ? `已限定产品文档集：${chosen.map(d => d.title).join('、')}` : '选择产品文档集'} onClick={() => setOpen(!open)}>
      <ReadOutlined aria-hidden="true" /><span className="docset-picker-label">{label}</span>
    </button>
    {open && <div className="docset-picker-panel" role="dialog" aria-label="产品文档集">
      <p className="docset-picker-heading">产品文档集</p>
      <ul>{docsets.map(docset => {
        const selected = value.includes(docset.id)
        const full = !selected && value.length >= LIMIT
        const meta = [docset.metadata.vendor, docset.metadata.software_version, docset.pages ? `${docset.pages.toLocaleString('zh-CN')} 页` : ''].filter(Boolean).join('，')
        return <li key={docset.id}><button type="button" role="checkbox" aria-checked={selected} disabled={full} onClick={() => toggle(docset.id)}>
          <span className="docset-picker-check" aria-hidden="true">{selected && <CheckOutlined />}</span>
          <span className="docset-picker-text"><strong>{docset.title}</strong>{meta && <small>{meta}</small>}</span>
        </button></li>
      })}</ul>
      <p className="docset-picker-note">{value.length
        ? `本会话的提问只从选中的文档集查起，最多 ${LIMIT} 个。`
        : '不选时，按问题里的型号、版本和路径上的设备自动匹配。'}</p>
      {value.length > 0 && <button type="button" className="docset-picker-clear" onClick={() => onChange([])}>清除选择</button>}
    </div>}
  </div>
}
