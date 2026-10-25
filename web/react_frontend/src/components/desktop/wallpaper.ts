export const wallpaperPresets = [
  { id: 'mountains', name: '远山', background: '#173a64 url("/desktop-wallpaper.svg") center / cover' },
  { id: 'midnight', name: '深海', background: 'linear-gradient(135deg, #101d35, #24566c)' },
  { id: 'forest', name: '松林', background: 'linear-gradient(145deg, #122e2a, #456a57)' },
  { id: 'dusk', name: '暮色', background: 'linear-gradient(135deg, #25223e, #77556d)' },
] as const
export type DesktopWallpaper = { type: 'preset'; id: string } | { type: 'image'; dataUrl: string }
export const defaultWallpaper: DesktopWallpaper = { type: 'preset', id: 'mountains' }
export const MAX_WALLPAPER_LENGTH = 524288
export function normalizeWallpaper(value?: DesktopWallpaper): DesktopWallpaper {
  if (value?.type === 'preset' && wallpaperPresets.some(p => p.id === value.id)) return { type: 'preset', id: value.id }
  if (value?.type === 'image' && typeof value.dataUrl === 'string' && value.dataUrl.length <= MAX_WALLPAPER_LENGTH && /^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(value.dataUrl)) return { type: 'image', dataUrl: value.dataUrl }
  return defaultWallpaper
}
export function wallpaperBackground(value?: DesktopWallpaper) {
  const wallpaper = normalizeWallpaper(value)
  return wallpaper.type === 'image' ? `linear-gradient(#07162833, #07162833), url("${wallpaper.dataUrl}") center / cover` : wallpaperPresets.find(p => p.id === wallpaper.id)!.background
}
export async function prepareWallpaper(file: File): Promise<DesktopWallpaper> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('请选择 JPG、PNG 或 WebP 图片。')
  if (file.size > 10 * 1024 * 1024) throw new Error('图片不能超过 10 MB。')
  const bitmap = await createImageBitmap(file)
  try {
    if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 50_000_000) throw new Error('图片尺寸过大，请缩小后重试。')
    const canvas = document.createElement('canvas')
    const ratio = Math.min(1, 2560 / Math.max(bitmap.width, bitmap.height))
    canvas.width = Math.max(1, Math.round(bitmap.width * ratio))
    canvas.height = Math.max(1, Math.round(bitmap.height * ratio))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器暂时无法处理图片。')
    context.fillStyle = '#173a64'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    for (const quality of [.86, .72, .56, .4]) {
      const dataUrl = canvas.toDataURL('image/jpeg', quality)
      if (dataUrl.length <= MAX_WALLPAPER_LENGTH) return { type: 'image', dataUrl }
    }
    throw new Error('图片细节过多，请缩小尺寸后重试。')
  } finally { bitmap.close() }
}
