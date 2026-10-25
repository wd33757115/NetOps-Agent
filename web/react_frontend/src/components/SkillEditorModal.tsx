// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from 'react'
import { Modal, Tabs, Input, Upload, Select, message, List, Typography } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { skillApi } from '../services/api'

const { TextArea } = Input
const { Dragger } = Upload
const { Text } = Typography

interface SkillEditorModalProps {
  open: boolean
  skillName: string | null
  onClose: () => void
  onSaved?: () => void
}

const FOLDERS = ['scripts', 'references', 'assets'] as const

const SkillEditorModal: React.FC<SkillEditorModalProps> = ({ open, skillName, onClose, onSaved }) => {
  const [content, setContent] = useState('')
  const [files, setFiles] = useState<Record<string, string[]>>({})
  const [folder, setFolder] = useState<(typeof FOLDERS)[number]>('scripts')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open && skillName) {
      loadEditorData(skillName)
    }
  }, [open, skillName])

  const loadEditorData = async (name: string) => {
    setLoading(true)
    try {
      const [contentRes, filesRes] = await Promise.all([
        skillApi.getContent(name),
        skillApi.listFiles(name),
      ])
      setContent(contentRes.content)
      setFiles(filesRes.files || {})
    } catch {
      message.error('加载 Skill 编辑器失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSaveContent = async () => {
    if (!skillName) return
    setSaving(true)
    try {
      await skillApi.saveContent(skillName, content)
      message.success('SKILL.md 已保存并重载')
      onSaved?.()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleUpload = (file: File) => {
    if (!skillName) return false
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const result = reader.result as string
        const base64 = result.includes(',') ? result.split(',')[1] : result
        await skillApi.uploadFile(skillName, { folder, filename: file.name, file_content: base64 })
        message.success(`${folder}/${file.name} 上传成功`)
        const filesRes = await skillApi.listFiles(skillName)
        setFiles(filesRes.files || {})
        onSaved?.()
      } catch {
        message.error('文件上传失败')
      }
    }
    reader.readAsDataURL(file)
    return false
  }

  const filesTab = (
    <>
      <Select
        value={folder}
        onChange={setFolder}
        style={{ width: 200, marginBottom: 12 }}
        options={FOLDERS.map((f) => ({ label: f, value: f }))}
      />
      <Dragger beforeUpload={handleUpload} showUploadList={false} multiple disabled={!skillName}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">拖拽文件到 {folder}/ 目录</p>
      </Dragger>
      {FOLDERS.map((f) => (
        <div key={f} style={{ marginTop: 16 }}>
          <Text strong>{f}/</Text>
          <List
            size="small"
            locale={{ emptyText: '暂无文件' }}
            dataSource={files[f] || []}
            renderItem={(item) => <List.Item>{item}</List.Item>}
          />
        </div>
      ))}
    </>
  )

  return (
    <Modal
      title={skillName ? `编辑 Skill: ${skillName}` : '编辑 Skill'}
      open={open}
      onCancel={onClose}
      width={900}
      okText="保存 SKILL.md"
      confirmLoading={saving}
      onOk={handleSaveContent}
      destroyOnClose
    >
      <Tabs
        items={[
          {
            key: 'markdown',
            label: 'SKILL.md',
            children: (
              <TextArea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={18}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
                placeholder="编辑 SKILL.md 全文..."
                disabled={loading}
              />
            ),
          },
          {
            key: 'files',
            label: '文件管理',
            children: filesTab,
          },
        ]}
      />
    </Modal>
  )
}

export default SkillEditorModal
