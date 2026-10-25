import React, { useRef, useState } from 'react'
import { Alert, Button, Modal } from 'antd'
import { PictureOutlined } from '@ant-design/icons'
import { defaultWallpaper, prepareWallpaper, wallpaperBackground, wallpaperPresets, type DesktopWallpaper } from './wallpaper'

export default function WallpaperSettings({ value, onSave, onClose, disabled }: {
  value?: DesktopWallpaper; onSave: (value: DesktopWallpaper) => void; onClose: () => void; disabled: boolean
}) {
  const [draft, setDraft] = useState(value || defaultWallpaper)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const generation = useRef(0)
  const choose = (next: DesktopWallpaper) => { generation.current++; setBusy(false); setError(''); setDraft(next) }
  return <Modal title="桌面壁纸" open onCancel={onClose} okText="应用壁纸" cancelText="取消" width={650}
    onOk={() => onSave(draft)} okButtonProps={{ disabled: disabled || busy }}>
    <p className="wallpaper-description">为自己的工作台选择背景，设置随账号保存。</p>
    <div className="wallpaper-preview" style={{ background: wallpaperBackground(draft) }} aria-label="壁纸预览"><span>NetOps 工作台</span></div>
    <div className="wallpaper-presets" aria-label="内置壁纸">{wallpaperPresets.map(preset => <button type="button" key={preset.id}
      aria-pressed={draft.type === 'preset' && draft.id === preset.id} onClick={() => choose({ type: 'preset', id: preset.id })}>
      <span style={{ background: preset.background }} /><strong>{preset.name}</strong>
    </button>)}</div>
    <div className="wallpaper-upload"><Button icon={<PictureOutlined aria-hidden="true" />} loading={busy} onClick={() => input.current?.click()}>上传自己的图片</Button>
      <span>JPG、PNG、WebP，最大 10 MB；自动适配桌面。</span>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" aria-label="选择壁纸图片" hidden onChange={async event => {
        const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
        const current = ++generation.current
        setBusy(true); setError('')
        try { const next = await prepareWallpaper(file); if (current === generation.current) setDraft(next) }
        catch (err) { if (current === generation.current) setError(err instanceof Error ? err.message : '图片无法读取，请重新选择。') }
        finally { if (current === generation.current) setBusy(false) }
      }} />
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    <Button type="link" className="wallpaper-reset" onClick={() => choose(defaultWallpaper)}>恢复默认壁纸</Button>
  </Modal>
}
