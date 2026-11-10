import React, { useEffect, useState } from 'react'
import { api } from '@netops/config/api'

interface Suggestion { name: string; source: string }

const split = (value: string) => value.split(/[,，、;；/]/).map(name => name.trim()).filter(Boolean)
const key = (name: string) => name.replace(/\s+/g, '').toLowerCase()

/** Model names the doc set's 版本配套说明 page and the device inventory offer; a click adds one. */
export default function ModelSuggestions({ url, params, value, disabled, onChange }: {
  url: string; params?: Record<string, string>; value: string; disabled?: boolean; onChange: (next: string) => void
}) {
  const [items, setItems] = useState<Suggestion[]>([])
  const query = JSON.stringify(params || {})
  useEffect(() => {
    let live = true
    api.get<{ items: Suggestion[] }>(url, { params: JSON.parse(query) })
      .then(result => { if (live) setItems(result.data.items) })
      .catch(() => { if (live) setItems([]) })
    return () => { live = false }
  }, [url, query])
  const present = new Set(split(value).map(key))
  const open = items.filter(item => !present.has(key(item.name)))
  if (!open.length) return null
  const add = (names: string[]) => onChange([...split(value), ...names].join(', '))
  const groups = [...new Set(open.map(item => item.source))]
  return <div className="model-suggestions" aria-label="建议的型号与别名">
    {groups.map(source => {
      const names = open.filter(item => item.source === source).map(item => item.name)
      return <div key={source} className="model-suggestions-group">
        <span className="model-suggestions-source">来自{source}</span>
        {names.map(name => <button type="button" key={name} disabled={disabled} onClick={() => add([name])}>{name}</button>)}
        {names.length > 1 && <button type="button" className="model-suggestions-all" disabled={disabled} onClick={() => add(names)}>全部加入（{names.length}）</button>}
      </div>
    })}
  </div>
}
