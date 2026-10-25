// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useState } from 'react'
import { Input, message } from 'antd'
import { ArrowUpOutlined, CloseOutlined, LoadingOutlined, PlusOutlined } from '@ant-design/icons'
import { useChatStore } from '../store/useChatStore'
import { api } from '../config/api'
import { useIsMobile } from '../hooks/useBreakpoint'
import ModelSettings from './ModelSettings'
import AttachmentIcon from './AttachmentIcon'

const { TextArea } = Input

/** A file uploaded for the next question; stored in the user's netdisk (问答附件). */
export interface ChatAttachment { file_id: string; name: string; size: number; kind: string }
interface Pending { key: string; name: string; size: number; status: 'uploading' | 'done' | 'failed'; file?: ChatAttachment; error?: string }

const MAX_FILES = 5
const MAX_BYTES = 10 * 1024 * 1024
const ACCEPT = '.txt,.log,.cfg,.conf,.cli,.csv,.md,.json,.yaml,.yml,.xml,.ini,.xlsx,.docx,.pdf,.zip,.png,.jpg,.jpeg,.webp,.gif'

export const formatSize = (bytes: number) =>
  bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`

interface InputAreaProps {
  onSend: (text: string, attachments: ChatAttachment[]) => void
  loading: boolean
  compact?: boolean
  allowUpload?: boolean
}

const InputArea: React.FC<InputAreaProps> = ({ onSend, loading, compact, allowUpload = true }) => {
  const isMobile = useIsMobile()
  const isCompact = compact ?? isMobile
  const [text, setText] = useState('')
  const [files, setFiles] = useState<Pending[]>([])
  const picker = useRef<HTMLInputElement>(null)
  const { currentConversationId } = useChatStore()
  const uploading = files.some(file => file.status === 'uploading')
  const canSend = Boolean(text.trim()) && !uploading

  const handleSend = () => {
    if (loading || !canSend) return
    onSend(text, files.flatMap(file => file.status === 'done' && file.file ? [file.file] : []))
    setText('')
    setFiles([])
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const update = (key: string, change: Partial<Pending>) =>
    setFiles(list => list.map(file => file.key === key ? { ...file, ...change } : file))

  const upload = async (file: File) => {
    const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`
    setFiles(list => [...list, { key, name: file.name, size: file.size, status: 'uploading' }])
    if (!file.size || file.size > MAX_BYTES) {
      update(key, { status: 'failed', error: '文件应大于 0 字节且不超过 10 MB' })
      return
    }
    try {
      const { data } = await api.post<ChatAttachment>('/apps/assistant/attachments', file, {
        params: { filename: file.name }, headers: { 'Content-Type': 'application/octet-stream' },
      })
      update(key, { status: 'done', file: data })
    } catch (error) {
      update(key, { status: 'failed', error: error instanceof Error ? error.message : '上传失败' })
    }
  }

  const choose = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files || [])
    event.target.value = ''
    const room = MAX_FILES - files.length
    if (chosen.length > room) message.warning(`一条消息最多附带 ${MAX_FILES} 个文件`)
    chosen.slice(0, Math.max(room, 0)).forEach(file => void upload(file))
  }

  return (
    <div className={`grok-input-shell${isCompact ? ' is-compact' : ''}`}>
      {allowUpload && files.length > 0 && (
        <ul className="grok-input-files" aria-label="本条消息的附件">
          {files.map(file => (
            <li key={file.key} className={`grok-file-chip is-${file.status}`}>
              {file.status === 'uploading' ? <LoadingOutlined aria-hidden="true" /> : <AttachmentIcon name={file.name} />}
              <span className="grok-file-chip-name" title={file.name}>{file.name}</span>
              <span className="grok-file-chip-meta">
                {file.status === 'uploading' ? '上传中' : file.status === 'failed' ? file.error : formatSize(file.size)}
              </span>
              <button type="button" className="grok-file-chip-remove" aria-label={`移除 ${file.name}`}
                onClick={() => setFiles(list => list.filter(item => item.key !== file.key))}>
                <CloseOutlined />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="grok-input-pill">
        {allowUpload && <>
          <input ref={picker} type="file" multiple accept={ACCEPT} hidden onChange={choose} />
          <button type="button" className="grok-input-plus" title="上传附件（保存到网盘的“问答附件”）"
            aria-label="上传附件" disabled={loading || files.length >= MAX_FILES || !currentConversationId}
            onClick={() => picker.current?.click()}>
            <PlusOutlined />
          </button>
        </>}

        <TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="有什么想问的？"
          autoSize={{ minRows: 1, maxRows: 8 }}
          disabled={loading}
          variant="borderless"
          className="grok-input-field"
        />

        <ModelSettings conversationId={currentConversationId} disabled={loading} />

        <button
          type="button"
          className={`grok-input-send${canSend ? ' is-ready' : ''}`}
          onClick={handleSend}
          disabled={!canSend || loading}
          aria-label="发送"
        >
          <ArrowUpOutlined />
        </button>
      </div>
    </div>
  )
}

export default InputArea
