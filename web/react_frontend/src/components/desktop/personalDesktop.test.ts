import { describe, expect, it, vi } from 'vitest'
import { defaultLayout, desktopColumns, moveNode, reconcileLayout } from './desktopLayout'
import { normalizeWallpaper, prepareWallpaper } from './wallpaper'

describe('personal desktop', () => {
  it('uses the right half of a wide desktop and preserves saved coordinates on resize', () => {
    expect(desktopColumns(2400)).toBe(21)
    expect(desktopColumns(344)).toBe(3)
    const original = defaultLayout()
    const moved = moveNode(original, original.nodes[0].id, 'desktop:0', {col:19,row:2})
    expect(reconcileLayout(moved).nodes[0]).toMatchObject({col:19,row:2})
  })
  it('preserves wallpaper during reconciliation and icon moves', () => {
    const original = {...defaultLayout(), wallpaper:{type:'preset' as const,id:'forest'}}
    const moved = moveNode(original, original.nodes[0].id, 'desktop:0', {col:18,row:0})
    expect(reconcileLayout(moved).wallpaper).toEqual(original.wallpaper)
    expect(normalizeWallpaper({type:'image',dataUrl:'https://example.com/image.jpg'})).toEqual({type:'preset',id:'mountains'})
  })
  it('rejects unsupported or oversized source images before decoding', async () => {
    const decode = vi.fn()
    vi.stubGlobal('createImageBitmap',decode)
    try {
      await expect(prepareWallpaper(new File(['<svg/>'],'a.svg',{type:'image/svg+xml'}))).rejects.toThrow('JPG')
      await expect(prepareWallpaper(new File([new Uint8Array(10*1024*1024+1)],'a.jpg',{type:'image/jpeg'}))).rejects.toThrow('10 MB')
      expect(decode).not.toHaveBeenCalled()
    } finally { vi.unstubAllGlobals() }
  })
})
