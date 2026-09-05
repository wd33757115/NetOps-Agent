import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { defaultLayout } from '../components/desktop/desktopLayout'
import { desktopApi } from '../services/desktopApi'
import { useDesktopStore } from './useDesktopStore'
vi.mock('../services/desktopApi', () => ({
  desktopApi: { get: vi.fn(), save: vi.fn() },
}))
const state = () => useDesktopStore.getState()
const edit = () => state().edit((value) => ({ ...value, dockAppIds: [] }))
beforeEach(() => {
  vi.useFakeTimers()
  state().reset()
  localStorage.clear()
  vi.resetAllMocks()
  vi.mocked(desktopApi.get).mockResolvedValue(defaultLayout())
})
afterEach(() => {
  state().reset()
  vi.useRealTimers()
})
describe('desktop persistence', () => {
  it('does not save defaults on a read failure', async () => {
    vi.mocked(desktopApi.get).mockRejectedValue(new Error('offline'))
    await state().load(1)
    edit()
    await vi.runAllTimersAsync()
    expect(state().ready).toBe(false)
    expect(desktopApi.save).not.toHaveBeenCalled()
  })
  it('serializes saves and preserves edits made while a request is in flight', async () => {
    await state().load(1)
    let finish!: (value: ReturnType<typeof defaultLayout>) => void
    vi.mocked(desktopApi.save)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      .mockImplementation(async (layout) => ({
        ...layout,
        revision: layout.revision + 1,
      }))
    edit()
    const pending = state().flush()
    state().edit((value) => ({
      ...value,
      nodes: value.nodes.map((n) => ({ ...n, row: 2 })),
    }))
    await state().flush()
    expect(desktopApi.save).toHaveBeenCalledTimes(1)
    finish({ ...defaultLayout(), revision: 1 })
    await pending
    await vi.runAllTimersAsync()
    expect(desktopApi.save).toHaveBeenCalledTimes(2)
    expect(state().layout.nodes[0].row).toBe(2)
    expect(state().layout.revision).toBe(2)
  })
  it('retains an offline draft and handles a revision conflict without overwriting', async () => {
    await state().load(1)
    edit()
    vi.mocked(desktopApi.save).mockRejectedValue({ status: 409 })
    await state().flush()
    expect(state().status).toBe('conflict')
    await state().flush()
    expect(desktopApi.save).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem('netops-desktop:1:v1')!).dirty).toBe(
      true,
    )
    await state().load(1, true)
    expect(state().layout.dockAppIds).toHaveLength(2)
  })
  it('ignores late responses from a previous account', async () => {
    let finish!: (value: ReturnType<typeof defaultLayout>) => void
    vi.mocked(desktopApi.get).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const oldLoad = state().load(1)
    await state().load(2)
    finish({ ...defaultLayout(), revision: 9 })
    await oldLoad
    expect(state().userId).toBe(2)
    expect(state().layout.revision).toBe(0)
  })
})
