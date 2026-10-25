// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import {
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  FileZipOutlined,
  FolderFilled,
  FolderOpenFilled,
  SoundOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'

export type StorageIconKind = 'folder' | 'folder-open' | 'file'

function extOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

export function getStorageFileIcon(name: string, contentType?: string | null): React.ReactNode {
  const ext = extOf(name)
  const ct = (contentType || '').toLowerCase()

  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'ico'].includes(ext) || ct.startsWith('image/')) {
    return <FileImageOutlined className="storage-icon storage-icon-image" />
  }
  if (ext === 'pdf' || ct === 'application/pdf') {
    return <FilePdfOutlined className="storage-icon storage-icon-pdf" />
  }
  if (['doc', 'docx'].includes(ext) || ct.includes('word')) {
    return <FileWordOutlined className="storage-icon storage-icon-word" />
  }
  if (['xls', 'xlsx', 'csv'].includes(ext) || ct.includes('sheet') || ct.includes('excel')) {
    return <FileExcelOutlined className="storage-icon storage-icon-excel" />
  }
  if (['ppt', 'pptx'].includes(ext) || ct.includes('presentation') || ct.includes('powerpoint')) {
    return <FilePptOutlined className="storage-icon storage-icon-ppt" />
  }
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext) || ct.includes('zip') || ct.includes('compressed')) {
    return <FileZipOutlined className="storage-icon storage-icon-zip" />
  }
  if (['mp4', 'avi', 'mov', 'mkv', 'webm'].includes(ext) || ct.startsWith('video/')) {
    return <VideoCameraOutlined className="storage-icon storage-icon-video" />
  }
  if (['mp3', 'wav', 'flac', 'aac', 'ogg'].includes(ext) || ct.startsWith('audio/')) {
    return <SoundOutlined className="storage-icon storage-icon-audio" />
  }
  if (['md', 'markdown'].includes(ext)) {
    return <FileMarkdownOutlined className="storage-icon storage-icon-text" />
  }
  if (['txt', 'log', 'json', 'xml', 'yaml', 'yml', 'ini', 'cfg', 'conf'].includes(ext) || ct.startsWith('text/')) {
    return <FileTextOutlined className="storage-icon storage-icon-text" />
  }
  return <FileOutlined className="storage-icon storage-icon-default" />
}

export function getStorageFolderIcon(open = false): React.ReactNode {
  return open ? (
    <FolderOpenFilled className="storage-icon storage-icon-folder-open" />
  ) : (
    <FolderFilled className="storage-icon storage-icon-folder" />
  )
}

export function isPreviewableInBrowser(name: string, contentType?: string | null): boolean {
  const ext = extOf(name)
  const ct = (contentType || '').toLowerCase()
  if (ct.startsWith('image/') || ct.startsWith('text/') || ct.startsWith('video/') || ct.startsWith('audio/')) {
    return true
  }
  if (ct === 'application/pdf') return true
  return ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'pdf', 'txt', 'log', 'json', 'md', 'mp4', 'webm', 'mp3', 'wav'].includes(ext)
}
