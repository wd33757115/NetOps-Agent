// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react'
import { Input, Upload, message } from 'antd'
import { ArrowUpOutlined, CloseOutlined, PlusOutlined, FileTextOutlined } from '@ant-design/icons'
import { useChatStore } from '../store/useChatStore'
import { chatApi } from '../services/api'
import { useIsMobile } from '../hooks/useBreakpoint'
import ModelSettings from './ModelSettings'

const { TextArea } = Input

interface InputAreaProps {
  onSend: (text: string) => void
  loading: boolean
  compact?: boolean
  allowUpload?: boolean
}

const InputArea: React.FC<InputAreaProps> = ({ onSend, loading, compact, allowUpload = true }) => {
  const isMobile = useIsMobile()
  const isCompact = compact ?? isMobile
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const { uploadedFile, setUploadedFile, currentConversationId } = useChatStore()

  const handleSend = () => {
    if (loading || uploading) return
    if (text.trim() || (allowUpload && uploadedFile)) {
      onSend(text)
      setText('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileUpload = (file: File) => {
    if (!currentConversationId) {
      message.warning('请先选择或创建一个对话')
      return Upload.LIST_IGNORE
    }

    setUploading(true)
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const result = reader.result as string
        const base64 = result.includes(',') ? result.split(',')[1] : result
        const uploadResult = await chatApi.uploadFile({
          thread_id: currentConversationId,
          filename: file.name,
          file_content: base64,
        })
        setUploadedFile({ name: file.name, path: uploadResult.file_path })
      } catch {
        message.error('文件上传失败')
      } finally {
        setUploading(false)
      }
    }
    reader.onerror = () => {
      message.error('读取文件失败')
      setUploading(false)
    }
    reader.readAsDataURL(file)
    return false
  }

  const canSend = Boolean(text.trim() || (allowUpload && uploadedFile))

  return (
    <div className={`grok-input-shell${isCompact ? ' is-compact' : ''}`}>
      {allowUpload && uploadedFile && (
        <div className="grok-input-attachment">
          <FileTextOutlined />
          <span>{uploadedFile.name}</span>
          <button type="button" className="grok-input-attachment-remove" onClick={() => setUploadedFile(null)}>
            <CloseOutlined />
          </button>
        </div>
      )}

      <div className="grok-input-pill">
        {allowUpload && <Upload
          accept=".xlsx,.xls,.csv"
          showUploadList={false}
          beforeUpload={handleFileUpload}
          disabled={uploading || loading}
        >
          <button type="button" className="grok-input-plus" aria-label="上传附件" disabled={uploading || loading}>
            <PlusOutlined />
          </button>
        </Upload>}

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
