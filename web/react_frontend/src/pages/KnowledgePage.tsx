// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Switch,
  Table,
  Upload,
  message,
} from 'antd'
import {
  ReloadOutlined,
  DatabaseOutlined,
  UploadOutlined,
  EyeOutlined,
  DeleteOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload/interface'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import { GrokChip, GrokInfoBar, GrokRowAction, GrokToolBtn } from '../components/ui/GrokUi'
import {
  knowledgeApi,
  type KnowledgeDocument,
  type KnowledgeDocumentPreview,
  type KnowledgeStats,
} from '../services/api'
import { useIsMobile } from '../hooks/useIsMobile'

const ACCEPT = '.md,.txt,.pdf,.docx'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch {
    return iso
  }
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      const base64 = result.includes(',') ? result.split(',')[1] : result
      resolve(base64)
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function downloadBase64File(base64: string, filename: string, mime: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  const blob = new Blob([bytes], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const KnowledgePage: React.FC = () => {
  const isMobile = useIsMobile()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [preview, setPreview] = useState<KnowledgeDocumentPreview | null>(null)
  const [uploadFileList, setUploadFileList] = useState<UploadFile[]>([])
  const [autoReindexOnChange, setAutoReindexOnChange] = useState(true)
  const [uploadForm] = Form.useForm()

  const { data: documents = [], isLoading, refetch } = useQuery<KnowledgeDocument[]>(
    'knowledge-documents',
    knowledgeApi.listDocuments,
    { refetchOnWindowFocus: false }
  )

  const { data: stats, isLoading: statsLoading } = useQuery<KnowledgeStats>(
    'knowledge-stats',
    knowledgeApi.getStats,
    { refetchOnWindowFocus: false }
  )

  const invalidateAll = () => {
    queryClient.invalidateQueries('knowledge-documents')
    queryClient.invalidateQueries('knowledge-stats')
  }

  const reindexMutation = useMutation(knowledgeApi.reindex, {
    onSuccess: (res) => {
      message.success(res.message || '索引重建完成')
      invalidateAll()
    },
    onError: () => message.error('重建索引失败'),
  })

  const uploadMutation = useMutation(
    (payload: { filename: string; file_content: string; folder?: string; auto_reindex: boolean }) =>
      knowledgeApi.upload(payload),
    {
      onSuccess: (res) => {
        message.success(res.message || '上传成功')
        setUploadOpen(false)
        setUploadFileList([])
        uploadForm.resetFields()
        invalidateAll()
      },
      onError: (err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        message.error(detail || '上传失败')
      },
    }
  )

  const deleteMutation = useMutation(
    (relativePath: string) => knowledgeApi.delete(relativePath, autoReindexOnChange),
    {
      onSuccess: (res) => {
        message.success(res.message || '已删除')
        invalidateAll()
      },
      onError: () => message.error('删除失败'),
    }
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return documents
    return documents.filter(
      (d) =>
        d.file_name.toLowerCase().includes(q) ||
        d.relative_path.toLowerCase().includes(q) ||
        d.doc_type.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q)
    )
  }, [documents, search])

  const handlePreview = async (relativePath: string) => {
    setPreviewOpen(true)
    setPreviewLoading(true)
    setPreview(null)
    try {
      const data = await knowledgeApi.getPreview(relativePath)
      setPreview(data)
    } catch {
      message.error('加载预览失败')
      setPreviewOpen(false)
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleUpload = async () => {
    const file = uploadFileList[0]?.originFileObj
    if (!file) {
      message.warning('请选择文件')
      return
    }
    const folder = (uploadForm.getFieldValue('folder') as string) || ''
    const base64 = await fileToBase64(file)
    uploadMutation.mutate({
      filename: file.name,
      file_content: base64,
      folder: folder.trim(),
      auto_reindex: autoReindexOnChange,
    })
  }

  const columns = [
    {
      title: '文件名',
      dataIndex: 'file_name',
      key: 'file_name',
      ellipsis: true,
    },
    {
      title: '路径',
      dataIndex: 'relative_path',
      key: 'relative_path',
      ellipsis: true,
      responsive: ['md'] as const,
    },
    {
      title: '类型',
      dataIndex: 'doc_type',
      key: 'doc_type',
      width: 120,
      render: (v: string) => <GrokChip>{v}</GrokChip>,
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 90,
      render: (v: number) => formatSize(v),
    },
    {
      title: '索引',
      key: 'indexed',
      width: 100,
      render: (_: unknown, row: KnowledgeDocument) =>
        row.indexed ? (
          <GrokChip tone="ok">{row.chunk_count} 片段</GrokChip>
        ) : (
          <GrokChip tone="warn">未索引</GrokChip>
        ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      responsive: ['lg'] as const,
      render: (v: string) => formatTime(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      fixed: isMobile ? ('right' as const) : undefined,
      render: (_: unknown, row: KnowledgeDocument) => (
        <span className="grok-row-actions">
          <GrokRowAction icon={<EyeOutlined />} onClick={() => handlePreview(row.relative_path)}>
            预览
          </GrokRowAction>
          <Popconfirm
            title="确认删除该文档？"
            description="删除后无法恢复"
            onConfirm={() => deleteMutation.mutate(row.relative_path)}
          >
            <GrokRowAction danger icon={<DeleteOutlined />} disabled={deleteMutation.isLoading}>
              删除
            </GrokRowAction>
          </Popconfirm>
        </span>
      ),
    },
  ]

  const toolbar = (
    <>
      <Input
        className="grok-search-input"
        placeholder="搜索文档…"
        allowClear
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <label className="grok-inline-switch">
        <span>变更后自动重建索引</span>
        <Switch size="small" checked={autoReindexOnChange} onChange={setAutoReindexOnChange} />
      </label>
      <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetch()}>
        刷新
      </GrokToolBtn>
      <GrokToolBtn icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
        上传
      </GrokToolBtn>
      <GrokToolBtn
        primary
        icon={<DatabaseOutlined />}
        disabled={reindexMutation.isLoading}
        onClick={() => reindexMutation.mutate()}
      >
        {reindexMutation.isLoading ? '重建中…' : '重建索引'}
      </GrokToolBtn>
    </>
  )

  return (
    <GrokShellLayout
      title="知识库"
      subtitle="上传、预览、删除文档并管理 RAG 向量索引"
      toolbar={toolbar}
    >
      <div className="grok-stat-grid">
        <div className="grok-stat-card">
          <div className="grok-stat-label">文档总数</div>
          <div className="grok-stat-value">{statsLoading ? '—' : stats?.document_count ?? 0}</div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">已索引文档</div>
          <div className="grok-stat-value">{statsLoading ? '—' : stats?.indexed_document_count ?? 0}</div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">向量片段</div>
          <div className="grok-stat-value">{statsLoading ? '—' : stats?.indexed_chunks ?? 0}</div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">向量库</div>
          <div className="grok-stat-value grok-stat-value-sm">{stats?.vector_store ?? 'chroma'}</div>
        </div>
      </div>

      <GrokInfoBar>
        <strong>支持格式</strong>
        <span>{stats?.supported_extensions?.join(' ') || '.md .txt .pdf .docx'}</span>
        <span>·</span>
        <span>目录</span>
        <code className="grok-inline-code">{stats?.kb_path || 'knowledge_base/'}</code>
      </GrokInfoBar>

      <section className="grok-panel grok-panel-flush">
        {isLoading ? (
          <div className="grok-page-loading is-compact">
            <Spin size="large" />
          </div>
        ) : (
          <Table
            className="grok-table"
            rowKey="id"
            columns={columns}
            dataSource={filtered}
            pagination={{ pageSize: 10, showSizeChanger: !isMobile }}
            scroll={{ x: isMobile ? 900 : undefined }}
            size={isMobile ? 'small' : 'middle'}
          />
        )}
      </section>

      <Modal
        title="上传知识库文档"
        open={uploadOpen}
        onCancel={() => {
          setUploadOpen(false)
          setUploadFileList([])
          uploadForm.resetFields()
        }}
        onOk={handleUpload}
        confirmLoading={uploadMutation.isLoading}
        okText="上传"
      >
        <Form form={uploadForm} layout="vertical">
          <Form.Item label="子目录（可选）" name="folder" extra="如 sops、manuals，留空则放在根目录">
            <Input placeholder="sops" />
          </Form.Item>
          <Form.Item label="文件" required>
            <Upload
              accept={ACCEPT}
              maxCount={1}
              beforeUpload={() => false}
              fileList={uploadFileList}
              onChange={({ fileList }) => setUploadFileList(fileList.slice(-1))}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={preview ? `预览 · ${preview.file_name}` : '文档预览'}
        open={previewOpen}
        onCancel={() => {
          setPreviewOpen(false)
          setPreview(null)
        }}
        width={800}
        footer={
          preview?.preview_type === 'binary' && preview.download_base64 ? (
            <Button
              icon={<DownloadOutlined />}
              type="primary"
              onClick={() =>
                downloadBase64File(
                  preview.download_base64!,
                  preview.file_name,
                  preview.content_type || 'application/octet-stream'
                )
              }
            >
              下载文件
            </Button>
          ) : null
        }
      >
        {previewLoading ? (
          <div className="grok-page-loading is-compact">
            <Spin />
          </div>
        ) : preview ? (
          <>
            {preview.truncated && (
              <Alert type="warning" message="内容过长，仅显示部分内容" style={{ marginBottom: 12 }} />
            )}
            {preview.preview_type === 'binary' ? (
              <Alert type="info" message={preview.message || '该格式请下载后查看'} />
            ) : (
              <pre className="grok-code-block is-scroll">{preview.content}</pre>
            )}
            <p className="grok-muted" style={{ marginTop: 12, marginBottom: 0 }}>
              大小 {formatSize(preview.size_bytes)} · {preview.content_type}
            </p>
          </>
        ) : null}
      </Modal>
    </GrokShellLayout>
  )
}

export default KnowledgePage
