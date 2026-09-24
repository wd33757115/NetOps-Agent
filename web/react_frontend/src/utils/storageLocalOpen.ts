// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import { API_BASE_URL, getAccessToken } from '../config/api'
import { storageApi, type StorageFile } from '../services/api'

const OFFICE_PROTOCOL: Record<string, string> = {
  doc: 'ms-word',
  docx: 'ms-word',
  xls: 'ms-excel',
  xlsx: 'ms-excel',
  csv: 'ms-excel',
  ppt: 'ms-powerpoint',
  pptx: 'ms-powerpoint',
}

function extOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

export function buildAuthenticatedContentUrl(
  fileId: string,
  disposition: 'inline' | 'attachment' = 'attachment'
): string {
  const token = getAccessToken()
  const url = new URL(`${API_BASE_URL}/storage/files/${fileId}/content/`, window.location.origin)
  url.searchParams.set('disposition', disposition)
  if (token) url.searchParams.set('token', token)
  return url.toString()
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Let the browser start consuming the Blob before releasing its download URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

type OpenResult = 'office' | 'saved' | 'downloaded'

/** 尽量用本地关联程序打开；纯 Web 环境会回退到保存/下载。 */
export async function openWithLocalApp(file: StorageFile): Promise<OpenResult> {
  const ext = extOf(file.name)
  const officeProto = OFFICE_PROTOCOL[ext]
  if (officeProto) {
    const fileUrl = buildAuthenticatedContentUrl(file.id, 'attachment')
    window.location.href = `${officeProto}:ofe|u|${encodeURIComponent(fileUrl)}`
    return 'office'
  }

  const blob = await storageApi.fetchContent(file.id, 'attachment')

  const picker = (window as Window & { showSaveFilePicker?: Function }).showSaveFilePicker
  if (picker) {
    try {
      const handle = await picker({
        suggestedName: file.name,
        types: [
          {
            description: file.name,
            accept: { [file.content_type || 'application/octet-stream']: [`.${ext}`] },
          },
        ],
      })
      const writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
      return 'saved'
    } catch {
      /* 用户取消保存 */
    }
  }

  downloadBlob(blob, file.name)
  return 'downloaded'
}
