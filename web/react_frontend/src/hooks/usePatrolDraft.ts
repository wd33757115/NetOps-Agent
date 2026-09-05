import { useState } from 'react'

export interface PatrolDraft {
  deviceIds: string[]
  values: {
    group?: string
    dynamic_group_id?: string
    ticket_id?: string
    save_baseline?: boolean
  }
}
const emptyDraft = (): PatrolDraft => ({ deviceIds: [], values: {} })

/** Session-only scope draft; never stores credentials or automatically launches work. */
export function usePatrolDraft(userId: number) {
  const key = `netops-patrol-draft:${userId}:v1`
  const [draft, setDraft] = useState<PatrolDraft>(() => {
    try {
      const cached = JSON.parse(sessionStorage.getItem(key) || 'null')
      return cached &&
        Array.isArray(cached.deviceIds) &&
        cached.values &&
        typeof cached.values === 'object'
        ? cached
        : emptyDraft()
    } catch {
      return emptyDraft()
    }
  })
  const saveDraft = (patch: Partial<PatrolDraft>) =>
    setDraft((current) => {
      const next = { ...current, ...patch }
      try {
        sessionStorage.setItem(key, JSON.stringify(next))
      } catch {
        /* Optional session recovery. */
      }
      return next
    })
  const clearDraft = () => {
    try {
      sessionStorage.removeItem(key)
    } catch {
      /* Optional session recovery. */
    }
    setDraft(emptyDraft())
  }
  return { draft, saveDraft, clearDraft }
}
