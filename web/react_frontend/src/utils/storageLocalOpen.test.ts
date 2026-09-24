import { afterEach, expect, it, vi } from 'vitest'
import { downloadBlob } from './storageLocalOpen'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('keeps the download URL alive until the browser can consume the report', () => {
  vi.useFakeTimers()
  const create = vi.fn(() => 'blob:report')
  const revoke = vi.fn()
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    expect(this.isConnected).toBe(true)
    expect(this.download).toBe('巡检报告.docx')
    expect(this.href).toBe('blob:report')
  })
  downloadBlob(new Blob(['report']), '巡检报告.docx')
  expect(click).toHaveBeenCalledOnce()
  expect(revoke).not.toHaveBeenCalled()
  expect(document.querySelector('a[download]')).toBeNull()
  vi.advanceTimersByTime(60_000)
  expect(revoke).toHaveBeenCalledWith('blob:report')
})
