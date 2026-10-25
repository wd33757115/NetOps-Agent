import React, { useState } from 'react'
import { Alert, Button, Input, Modal, Popconfirm, Space } from 'antd'
import { api } from '@netops/config/api'
import { publicationError, type Target } from './WordImport'

export interface ManagedDocument extends Target { status: 'active' | 'inactive' }

export default function DocumentActions({ document, onChanged }: { document: ManagedDocument; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(document.title)
  const [metadata, setMetadata] = useState(document.metadata)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function change(action: 'metadata' | 'status' | 'delete') {
    setBusy(true); setError('')
    const path = '/apps/materials/documents/' + document.id
    const expected = { expected_revision: document.revision, expected_status: document.status }
    try {
      if (action === 'delete') await api.delete(path, { data: expected })
      else if (action === 'status') await api.patch(path + '/status', { ...expected, status: document.status === 'active' ? 'inactive' : 'active' })
      else await api.patch(path + '/metadata', { title, metadata, expected_revision: document.revision })
      setEditing(false); await onChanged()
    } catch (e) { setError(publicationError(e)) }
    finally { setBusy(false) }
  }
  return <Space direction="vertical" className="materials-document-actions">
    {error && !editing && <Alert type="error" message={error} />}
    <Button disabled={busy} onClick={() => { setTitle(document.title); setMetadata(document.metadata); setError(''); setEditing(true) }}>编辑资料信息</Button>
    <Popconfirm title={document.status === 'active' ? '停用此资料？' : '恢复此资料？'}
      description={document.status === 'active' ? '停用后不再进入新问答，历史引用仍可查阅。' : '恢复后可再次用于新问答。'}
      okText="确认" cancelText="取消" onConfirm={() => change('status')}>
      <Button disabled={busy}>{document.status === 'active' ? '停用资料' : '恢复资料'}</Button>
    </Popconfirm>
    <Popconfirm title={'删除“' + document.title + '”？'} description="移出资料库和新问答；历史修订及原文件仍保留供已有引用回查。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => change('delete')}>
      <Button danger disabled={busy}>删除资料</Button>
    </Popconfirm>
    <Modal title="编辑资料信息" open={editing} onCancel={() => !busy && setEditing(false)} onOk={() => void change('metadata')}
      okText="保存为新修订" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !title.trim() }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <p>保留正文与原文件，仅更新标题和元数据；已有引用保持不变。</p>
        {error && <Alert type="error" message={error} />}
        <label>资料标题<Input aria-label="编辑资料标题" value={title} maxLength={256} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        {Object.entries({ vendor: '厂商', product: '产品', software_version: '软件版本', doc_type: '资料类型', category: '分类' }).map(([key, label]) =>
          <label key={key}>{label}<Input aria-label={'编辑' + label} value={metadata[key] || ''} maxLength={256} disabled={busy} onChange={event => setMetadata({ ...metadata, [key]: event.target.value })} /></label>)}
      </Space>
    </Modal>
  </Space>
}
