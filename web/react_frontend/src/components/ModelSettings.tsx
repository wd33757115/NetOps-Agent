import React, { useEffect, useRef, useState } from 'react'
import { api } from '../config/api'
import './ModelSettings.css'

interface Option { id: string; label: string }
interface Choice { model: string; reasoning_effort: string }
interface Options { models: Option[]; reasoning_efforts: Option[]; default: Choice }

let options: Promise<Options> | null = null
const loadOptions = () => (options ??= api.get<Options>('/apps/assistant/model-settings')
  .then(({ data }) => data)
  .catch(error => { options = null; throw error }))
const settingsUrl = (id: string) => `/apps/assistant/conversations/${encodeURIComponent(id)}/model-settings`

/** Model and reasoning effort for the current conversation only; saved as soon as chosen. */
export default function ModelSettings({ conversationId, disabled }: { conversationId: string | null; disabled?: boolean }) {
  const [offered, setOffered] = useState<Options>()
  const [choice, setChoice] = useState<Choice>()
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => { void loadOptions().then(setOffered).catch(() => undefined) }, [])
  useEffect(() => {
    setChoice(undefined)
    setError('')
    setOpen(false)
    if (!conversationId) return
    let disposed = false
    void api.get<Choice>(settingsUrl(conversationId))
      .then(({ data }) => { if (!disposed) setChoice({ model: data.model, reasoning_effort: data.reasoning_effort }) })
      .catch(() => undefined) // Unknown: the defaults show until a choice is made.
    return () => { disposed = true }
  }, [conversationId])
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !box.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])

  if (!conversationId || !offered) return null
  const current = choice ?? offered.default
  const named = (list: Option[], id: string) => list.find(option => option.id === id)?.label ?? id
  const model = named(offered.models, current.model)
  const effort = named(offered.reasoning_efforts, current.reasoning_effort)

  async function pick(next: Choice) {
    if (!conversationId || (next.model === current.model && next.reasoning_effort === current.reasoning_effort)) return
    const previous = current
    setChoice(next)
    setSaving(true)
    setError('')
    try {
      const { data } = await api.put<Choice>(settingsUrl(conversationId), next)
      setChoice({ model: data.model, reasoning_effort: data.reasoning_effort })
    } catch {
      setChoice(previous)
      setError('设置未保存，请重试')
    } finally {
      setSaving(false)
    }
  }

  return <div className="model-settings" ref={box}>
    <button type="button" className={`model-settings-toggle${current.reasoning_effort !== 'off' ? ' is-thinking' : ''}`}
      aria-haspopup="dialog" aria-expanded={open} disabled={disabled}
      aria-label={`模型与思考强度：${model}，思考${effort}`} onClick={() => setOpen(!open)}>
      <span className="model-settings-model">{model}</span>
      <span className="model-settings-model" aria-hidden="true">·</span>
      <span>思考 {effort}</span>
    </button>
    {open && <div className="model-settings-panel" role="dialog" aria-label="模型与思考强度">
      <p className="model-settings-heading">模型</p>
      <div className="model-settings-options">{offered.models.map(option =>
        <button key={option.id} type="button" aria-pressed={current.model === option.id} disabled={saving}
          onClick={() => void pick({ ...current, model: option.id })}>{option.label}</button>)}</div>
      <p className="model-settings-heading">思考强度</p>
      <div className="model-settings-options">{offered.reasoning_efforts.map(option =>
        <button key={option.id} type="button" aria-pressed={current.reasoning_effort === option.id} disabled={saving}
          onClick={() => void pick({ ...current, reasoning_effort: option.id })}>{option.label}</button>)}</div>
      <p className={`model-settings-note${error ? ' is-error' : ''}`} role={error ? 'alert' : undefined}>
        {error || '只对当前会话生效；开启思考回答更慢、用量更多。'}</p>
    </div>}
  </div>
}
