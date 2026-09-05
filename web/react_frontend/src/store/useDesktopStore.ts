import { create } from 'zustand'
import {
  defaultLayout,
  reconcileLayout,
  type DesktopLayout,
} from '../components/desktop/desktopLayout'
import { desktopApi } from '../services/desktopApi'

type Status = 'loading' | 'saved' | 'pending' | 'saving' | 'error' | 'conflict'
interface DesktopState {
  userId: number | null
  layout: DesktopLayout
  status: Status
  ready: boolean
  error: string
  generation: number
  load: (userId: number, discardDraft?: boolean) => Promise<void>
  edit: (update: (layout: DesktopLayout) => DesktopLayout) => void
  flush: () => Promise<void>
  reset: () => void
}
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let epoch = 0
let saving = false
const key = (id: number) => `netops-desktop:${id}:v1`
function cache(userId: number | null, layout: DesktopLayout, dirty: boolean) {
  if (userId === null) return
  try {
    localStorage.setItem(key(userId), JSON.stringify({ layout, dirty }))
  } catch {
    /* Server remains authoritative when storage is unavailable. */
  }
}
export const useDesktopStore = create<DesktopState>((set, get) => ({
  userId: null,
  layout: defaultLayout(),
  status: 'loading',
  ready: false,
  error: '',
  generation: 0,
  load: async (userId, discardDraft = false) => {
    clearTimeout(timer)
    controller?.abort()
    controller = new AbortController()
    const currentEpoch = ++epoch
    saving = false
    set({
      userId,
      status: 'loading',
      ready: false,
      error: '',
      layout: defaultLayout(),
      generation: 0,
    })
    try {
      const server = reconcileLayout(await desktopApi.get(controller.signal))
      if (epoch !== currentEpoch) return
      let draft: { layout: DesktopLayout; dirty: boolean } | null = null
      try {
        draft = JSON.parse(localStorage.getItem(key(userId)) || 'null')
      } catch {
        /* Ignore corrupt cache. */
      }
      if (!discardDraft && draft?.dirty) {
        const layout = reconcileLayout(draft.layout)
        const conflict = layout.revision !== server.revision
        set({
          layout,
          ready: true,
          status: conflict ? 'conflict' : 'pending',
          error: conflict ? '布局已在其他页面修改，本地草稿已保留。' : '',
        })
        if (!conflict) timer = setTimeout(() => void get().flush(), 600)
      } else {
        set({ layout: server, ready: true, status: 'saved' })
        cache(userId, server, false)
      }
    } catch {
      if (epoch === currentEpoch)
        set({
          status: 'error',
          ready: false,
          error: '无法读取桌面布局，请重试。',
        })
    }
  },
  edit: (update) => {
    const state = get()
    if (!state.ready || state.status === 'conflict') return
    const layout = update(state.layout)
    if (layout === state.layout) return
    set({
      layout,
      status: 'pending',
      generation: state.generation + 1,
      error: '',
    })
    cache(state.userId, layout, true)
    clearTimeout(timer)
    timer = setTimeout(() => void get().flush(), 600)
  },
  flush: async () => {
    const snapshot = get()
    if (
      saving ||
      !snapshot.ready ||
      snapshot.status === 'conflict' ||
      snapshot.status === 'saved'
    )
      return
    saving = true
    const currentEpoch = epoch
    set({ status: 'saving' })
    try {
      const result = await desktopApi.save(snapshot.layout, controller?.signal)
      if (epoch !== currentEpoch) return
      const state = get(),
        changed = state.generation !== snapshot.generation
      const layout = { ...state.layout, revision: result.revision }
      set({ layout, status: changed ? 'pending' : 'saved', error: '' })
      cache(state.userId, layout, changed)
    } catch (error: any) {
      if (epoch !== currentEpoch) return
      const conflict = error?.response?.status === 409 || error?.status === 409
      set({
        status: conflict ? 'conflict' : 'error',
        error: conflict
          ? '布局已在其他页面修改，本地草稿已保留。'
          : '布局尚未保存，修改已保留在本机。',
      })
    } finally {
      if (epoch === currentEpoch) {
        saving = false
        if (get().status === 'pending') void get().flush()
      }
    }
  },
  reset: () => {
    ++epoch
    clearTimeout(timer)
    controller?.abort()
    saving = false
    set({
      userId: null,
      layout: defaultLayout(),
      ready: false,
      status: 'loading',
      error: '',
      generation: 0,
    })
  },
}))
