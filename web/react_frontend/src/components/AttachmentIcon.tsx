import React from 'react'
import {
  FileExcelOutlined, FileImageOutlined, FileOutlined, FilePdfOutlined, FileTextOutlined,
  FileWordOutlined, FileZipOutlined,
} from '@ant-design/icons'

const BY_ENDING: Record<string, React.ComponentType> = {
  xlsx: FileExcelOutlined, docx: FileWordOutlined, pdf: FilePdfOutlined, zip: FileZipOutlined,
  png: FileImageOutlined, jpg: FileImageOutlined, jpeg: FileImageOutlined, webp: FileImageOutlined, gif: FileImageOutlined,
}

/** The kind of an attached file at a glance; text and configuration files share one icon. */
export default function AttachmentIcon({ name }: { name: string }) {
  const ending = name.split('.').pop()?.toLowerCase() || ''
  const Icon = BY_ENDING[ending] || (ending ? FileTextOutlined : FileOutlined)
  return <span className={`grok-file-icon is-${ending || 'file'}`} aria-hidden="true"><Icon /></span>
}
