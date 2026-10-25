// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Breadcrumb,
  Button,
  Drawer,
  Dropdown,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tree,
  message,
} from 'antd'
import type { MenuProps } from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  CloudUploadOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  PlusOutlined,
  ReloadOutlined,
  ScissorOutlined,
  ShareAltOutlined,
  TeamOutlined,
  UserAddOutlined,
} from '@ant-design/icons'
import { useQuery, useQueryClient } from 'react-query'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import { GrokChip, GrokInfoBar, GrokRowAction, GrokToolBtn } from '../components/ui/GrokUi'
import { useAuth } from '../context/AuthContext'
import { useIsMobile } from '../hooks/useIsMobile'
import {
  storageApi,
  userAdminApi,
  type ManagedUser,
  type StorageFile,
  type StorageFolder,
  type StorageListResult,
  type StorageTeam,
  type StorageTeamMember,
} from '../services/api'
import {
  getStorageFileIcon,
  getStorageFolderIcon,
  isPreviewableInBrowser,
} from '../utils/storageFileIcons'
import { downloadBlob, openWithLocalApp } from '../utils/storageLocalOpen'
import {
  flattenFolderOptions,
  isInvalidFolderMoveTarget,
  parseStorageRowKey,
  type FolderTreeNode,
} from '../utils/storageTree'

type Visibility = 'private' | 'shared'
type DragPayload = { kind: 'file' | 'folder'; id: string }
type ShareTarget = { kind: 'file' | 'folder'; id: string; name: string }
type TransferMode = 'move' | 'copy'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isFolder(row: StorageFile | StorageFolder): row is StorageFolder {
  return !('size_bytes' in row)
}

function treeToNodes(
  node: FolderTreeNode,
  dropHandlers?: (folderId: string) => Record<string, unknown>
): DataNode[] {
  const children = (node.children || []).map((c) => treeToNodes(c, dropHandlers)[0]).filter(Boolean)
  return [
    {
      key: node.id,
      title: dropHandlers ? (
        <span className="storage-tree-node-title" {...dropHandlers(node.id)}>
          {node.name}
        </span>
      ) : (
        node.name
      ),
      icon: getStorageFolderIcon(false),
      children: children.length ? children : undefined,
    },
  ]
}

const DRAG_MIME = 'application/x-netops-storage-item'

const StoragePage: React.FC = () => {
  const { user } = useAuth()
  const isMobile = useIsMobile()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [visibility, setVisibility] = useState<Visibility>('private')
  const [teamId, setTeamId] = useState<string | undefined>()
  const [folderId, setFolderId] = useState<string | undefined>()
  const [folderModalOpen, setFolderModalOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [shareModalOpen, setShareModalOpen] = useState(false)
  const [shareTarget, setShareTarget] = useState<ShareTarget | null>(null)
  const [shareTeamId, setShareTeamId] = useState<string | undefined>()
  const [shareTargetFolderId, setShareTargetFolderId] = useState<string | undefined>()
  const [uploading, setUploading] = useState(false)
  const [renameModalOpen, setRenameModalOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState<{ kind: 'file' | 'folder'; id: string; name: string } | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewFile, setPreviewFile] = useState<StorageFile | null>(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewText, setPreviewText] = useState('')
  const [dropTargetId, setDropTargetId] = useState<string | null>(null)
  const [teamDrawerOpen, setTeamDrawerOpen] = useState(false)
  const [adminTeamId, setAdminTeamId] = useState<string | undefined>()
  const [newTeamName, setNewTeamName] = useState('')
  const [newTeamDesc, setNewTeamDesc] = useState('')
  const [addMemberUserId, setAddMemberUserId] = useState<string | undefined>()
  const [addMemberRole, setAddMemberRole] = useState('member')
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [transferModalOpen, setTransferModalOpen] = useState(false)
  const [transferMode, setTransferMode] = useState<TransferMode>('move')
  const [transferTargets, setTransferTargets] = useState<DragPayload[]>([])
  const [transferTargetFolderId, setTransferTargetFolderId] = useState<string | undefined>()
  const [search, setSearch] = useState('')
  const [mobileTreeOpen, setMobileTreeOpen] = useState(false)
  const [createTeamOpen, setCreateTeamOpen] = useState(false)

  const isAdmin = user?.role === 'admin'
  const canWrite = isAdmin || user?.role === 'operator'

  const { data: teams = [], refetch: refetchTeams } = useQuery('storage-teams', storageApi.listTeams, {
    refetchOnWindowFocus: false,
  })

  const { data: allUsers = [] } = useQuery('storage-all-users', userAdminApi.list, {
    enabled: isAdmin && teamDrawerOpen,
    refetchOnWindowFocus: false,
  })

  const listKey = ['storage-list', visibility, teamId, folderId]
  const { data: listing, isLoading, refetch } = useQuery<StorageListResult>(
    listKey,
    () =>
      storageApi.list({
        visibility,
        team_id: visibility === 'shared' ? teamId : undefined,
        folder_id: folderId,
      }),
    {
      enabled: visibility === 'private' || !!teamId,
      refetchOnWindowFocus: false,
    }
  )

  const { data: treeData } = useQuery<FolderTreeNode>(
    ['storage-tree', visibility, teamId],
    () =>
      storageApi.folderTree({
        visibility,
        team_id: visibility === 'shared' ? teamId : undefined,
      }),
    {
      enabled: visibility === 'private' || !!teamId,
      refetchOnWindowFocus: false,
    }
  )

  const { data: shareTeamTree } = useQuery(
    ['storage-share-tree', shareTeamId],
    () => storageApi.folderTree({ visibility: 'shared', team_id: shareTeamId }),
    { enabled: !!shareTeamId && shareModalOpen, refetchOnWindowFocus: false }
  )

  const { data: teamMembers = [], refetch: refetchMembers } = useQuery<StorageTeamMember[]>(
    ['storage-team-members', adminTeamId],
    () => storageApi.listTeamMembers(adminTeamId!),
    { enabled: isAdmin && teamDrawerOpen && !!adminTeamId, refetchOnWindowFocus: false }
  )

  useEffect(() => {
    if (visibility === 'shared' && teams.length > 0 && !teamId) {
      setTeamId(teams[0].id)
    }
  }, [visibility, teams, teamId])

  useEffect(() => {
    if (isAdmin && teams.length > 0 && !adminTeamId) {
      setAdminTeamId(teams[0].id)
    }
  }, [isAdmin, teams, adminTeamId])

  useEffect(() => {
    setSelectedRowKeys([])
  }, [folderId, visibility, teamId])

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries('storage-list')
    queryClient.invalidateQueries('storage-tree')
    queryClient.invalidateQueries('storage-teams')
  }, [queryClient])

  const currentFolderId = folderId || listing?.folder?.id

  const folderOptions = useMemo(
    () => (treeData ? flattenFolderOptions(treeData) : []),
    [treeData]
  )

  const shareFolderOptions = useMemo(
    () => (shareTeamTree ? flattenFolderOptions(shareTeamTree as FolderTreeNode) : []),
    [shareTeamTree]
  )

  const userNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const u of allUsers) {
      map.set(String(u.id), u.username)
    }
    return map
  }, [allUsers])

  const tableData = useMemo(
    () => [
      ...(listing?.folders || []).map((f) => ({ ...f, key: `folder-${f.id}` })),
      ...(listing?.files || []).map((f) => ({ ...f, key: `file-${f.id}` })),
    ],
    [listing]
  )

  const filteredTableData = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return tableData
    return tableData.filter((row) => row.name.toLowerCase().includes(q))
  }, [tableData, search])

  const storageStats = useMemo(() => {
    const folders = listing?.folders.length ?? 0
    const files = listing?.files.length ?? 0
    const bytes = (listing?.files || []).reduce((sum, f) => sum + (f.size_bytes || 0), 0)
    return { folders, files, bytes }
  }, [listing])

  const selectedTeam = useMemo(
    () => teams.find((t) => t.id === adminTeamId),
    [teams, adminTeamId]
  )

  const fileById = useMemo(() => {
    const map = new Map<string, StorageFile>()
    for (const f of listing?.files || []) map.set(f.id, f)
    return map
  }, [listing])

  const folderById = useMemo(() => {
    const map = new Map<string, StorageFolder>()
    for (const f of listing?.folders || []) map.set(f.id, f)
    if (listing?.folder) map.set(listing.folder.id, listing.folder)
    return map
  }, [listing])

  const handleCreateFolder = async () => {
    const name = newFolderName.trim()
    if (!name) {
      message.warning('请输入文件夹名称')
      return
    }
    try {
      await storageApi.createFolder({
        name,
        parent_id: currentFolderId,
        visibility,
        team_id: visibility === 'shared' ? teamId : undefined,
      })
      message.success('文件夹已创建')
      setFolderModalOpen(false)
      setNewFolderName('')
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '创建失败')
    }
  }

  const handleUploadFiles = async (files: FileList | null) => {
    if (!files?.length || !canWrite) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const init = await storageApi.uploadInit({
          filename: file.name,
          folder_id: currentFolderId,
          visibility,
          team_id: visibility === 'shared' ? teamId : undefined,
          content_type: file.type || 'application/octet-stream',
          size_bytes: file.size,
        })
        const putRes = await fetch(init.upload_url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        })
        if (!putRes.ok) {
          throw new Error(`上传 ${file.name} 失败: ${putRes.status}`)
        }
        await storageApi.uploadComplete({ file_id: init.file_id, size_bytes: file.size })
      }
      message.success('上传完成')
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '上传失败，请稍后重试')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDownload = async (file: StorageFile) => {
    try {
      const blob = await storageApi.fetchContent(file.id, 'attachment')
      downloadBlob(blob, file.name)
    } catch (e: unknown) {
      message.error((e as Error).message || '下载失败')
    }
  }

  const handlePreview = async (file: StorageFile) => {
    setPreviewLoading(true)
    setPreviewFile(file)
    setPreviewOpen(true)
    setPreviewUrl('')
    setPreviewText('')
    try {
      const blob = await storageApi.fetchContent(file.id, 'inline')
      const objectUrl = URL.createObjectURL(blob)
      setPreviewUrl(objectUrl)
      const ct = (file.content_type || '').toLowerCase()
      if (ct.startsWith('text/') || file.name.match(/\.(txt|log|json|md|xml|yaml|yml|ini|cfg)$/i)) {
        const text = await blob.text()
        setPreviewText(text.slice(0, 200_000))
      }
    } catch (e: unknown) {
      message.error((e as Error).message || '预览失败')
      setPreviewOpen(false)
    } finally {
      setPreviewLoading(false)
    }
  }

  const openFile = async (file: StorageFile) => {
    if (isPreviewableInBrowser(file.name, file.content_type)) {
      await handlePreview(file)
      return
    }
    try {
      const result = await openWithLocalApp(file)
      if (result === 'office') {
        message.info('正在尝试用本机 Office 打开，请稍候…')
      } else if (result === 'saved') {
        message.success('文件已保存到所选位置，请从资源管理器打开')
      } else {
        message.info('已开始下载，可在浏览器下载栏点击「打开」使用本地程序')
      }
    } catch (e: unknown) {
      message.error((e as Error).message || '打开失败')
    }
  }

  const openShareModal = (target: ShareTarget) => {
    setShareTarget(target)
    setShareTeamId(teams[0]?.id)
    setShareTargetFolderId(undefined)
    setShareModalOpen(true)
  }

  const handleShare = async () => {
    if (!shareTarget || !shareTeamId) return
    try {
      if (shareTarget.kind === 'file') {
        await storageApi.share({
          file_id: shareTarget.id,
          team_id: shareTeamId,
          target_folder_id: shareTargetFolderId,
        })
      } else {
        await storageApi.shareFolder({
          folder_id: shareTarget.id,
          team_id: shareTeamId,
          target_folder_id: shareTargetFolderId,
        })
      }
      message.success('已分享到团队空间')
      setShareModalOpen(false)
      setShareTarget(null)
    } catch (e: unknown) {
      message.error((e as Error).message || '分享失败')
    }
  }

  const openRenameModal = (kind: 'file' | 'folder', id: string, name: string) => {
    setRenameTarget({ kind, id, name })
    setRenameValue(name)
    setRenameModalOpen(true)
  }

  const handleRename = async () => {
    if (!renameTarget) return
    const name = renameValue.trim()
    if (!name) {
      message.warning('名称不能为空')
      return
    }
    try {
      if (renameTarget.kind === 'file') {
        await storageApi.renameFile(renameTarget.id, name)
      } else {
        await storageApi.renameFolder(renameTarget.id, name)
      }
      message.success('重命名成功')
      setRenameModalOpen(false)
      setRenameTarget(null)
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '重命名失败')
    }
  }

  const validateMoveTarget = useCallback(
    (payload: DragPayload, targetFolderId: string): string | null => {
      if (payload.kind === 'folder' && isInvalidFolderMoveTarget(treeData, payload.id, targetFolderId)) {
        return '不能将文件夹移动到自身或其子目录'
      }
      if (payload.kind === 'file') {
        const file = fileById.get(payload.id)
        if (file?.folder_id === targetFolderId) return '文件已在此文件夹中'
      } else {
        const folder = folderById.get(payload.id) || (listing?.folders || []).find((f) => f.id === payload.id)
        if (folder?.parent_id === targetFolderId) return '文件夹已在此目录下'
      }
      return null
    },
    [treeData, fileById, folderById, listing]
  )

  const handleMove = useCallback(
    async (payloads: DragPayload[], targetFolderId: string) => {
      for (const payload of payloads) {
        const err = validateMoveTarget(payload, targetFolderId)
        if (err) {
          message.warning(err)
          return
        }
      }
      try {
        for (const payload of payloads) {
          if (payload.kind === 'file') {
            await storageApi.moveFile(payload.id, targetFolderId)
          } else {
            await storageApi.moveFolder(payload.id, targetFolderId)
          }
        }
        message.success(payloads.length > 1 ? `已移动 ${payloads.length} 项` : '已移动')
        setSelectedRowKeys([])
        invalidate()
      } catch (e: unknown) {
        message.error((e as Error).message || '移动失败')
      }
    },
    [invalidate, validateMoveTarget]
  )

  const handleCopy = useCallback(
    async (fileIds: string[], targetFolderId: string) => {
      try {
        for (const fileId of fileIds) {
          await storageApi.copyFile(fileId, targetFolderId)
        }
        message.success(fileIds.length > 1 ? `已复制 ${fileIds.length} 个文件` : '已复制')
        setSelectedRowKeys([])
        invalidate()
      } catch (e: unknown) {
        message.error((e as Error).message || '复制失败')
      }
    },
    [invalidate]
  )

  const openTransferModal = (mode: TransferMode, targets: DragPayload[]) => {
    setTransferMode(mode)
    setTransferTargets(targets)
    setTransferTargetFolderId(currentFolderId)
    setTransferModalOpen(true)
  }

  const confirmTransfer = async () => {
    if (!transferTargetFolderId || transferTargets.length === 0) return
    setTransferModalOpen(false)
    if (transferMode === 'move') {
      await handleMove(transferTargets, transferTargetFolderId)
    } else {
      const fileIds = transferTargets.filter((t) => t.kind === 'file').map((t) => t.id)
      if (fileIds.length === 0) {
        message.warning('目前仅支持复制文件')
        return
      }
      await handleCopy(fileIds, transferTargetFolderId)
    }
  }

  const payloadsFromSelection = useCallback((): DragPayload[] => {
    return selectedRowKeys
      .map((key) => parseStorageRowKey(String(key)))
      .filter((x): x is DragPayload => x !== null)
  }, [selectedRowKeys])

  const handleDeleteFolder = async (id: string) => {
    try {
      await storageApi.deleteFolder(id)
      message.success('文件夹已删除')
      if (folderId === id) setFolderId(undefined)
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '删除失败')
    }
  }

  const handleBatchDelete = async () => {
    const payloads = payloadsFromSelection()
    if (!payloads.length) return
    try {
      for (const p of payloads) {
        if (p.kind === 'file') {
          await storageApi.deleteFile(p.id)
        } else {
          await storageApi.deleteFolder(p.id)
        }
      }
      message.success('已删除')
      setSelectedRowKeys([])
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '删除失败')
    }
  }

  const handleBatchDownload = async () => {
    const fileIds = payloadsFromSelection().filter((p) => p.kind === 'file').map((p) => p.id)
    if (!fileIds.length) {
      message.warning('请选择要下载的文件')
      return
    }
    for (const id of fileIds) {
      const file = fileById.get(id)
      if (file) await handleDownload(file)
    }
  }

  const setDragData = (e: React.DragEvent, payload: DragPayload) => {
    const keys =
      selectedRowKeys.includes(`${payload.kind}-${payload.id}`) && selectedRowKeys.length > 1
        ? selectedRowKeys
        : [`${payload.kind}-${payload.id}`]
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ items: keys.map((k) => parseStorageRowKey(String(k))).filter(Boolean) }))
    e.dataTransfer.effectAllowed = 'move'
  }

  const getDragData = (e: React.DragEvent): DragPayload[] => {
    const raw = e.dataTransfer.getData(DRAG_MIME)
    if (!raw) return []
    try {
      const parsed = JSON.parse(raw) as { items?: DragPayload[] } | DragPayload
      if ('items' in parsed && Array.isArray(parsed.items)) return parsed.items
      return [parsed as DragPayload]
    } catch {
      return []
    }
  }

  const bindDropZone = useCallback(
    (targetId: string) => ({
      onDragOver: (e: React.DragEvent) => {
        if (!canWrite) return
        e.preventDefault()
        e.stopPropagation()
        e.dataTransfer.dropEffect = 'move'
        setDropTargetId(targetId)
      },
      onDragLeave: (e: React.DragEvent) => {
        e.stopPropagation()
        setDropTargetId((prev) => (prev === targetId ? null : prev))
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setDropTargetId(null)
        if (!canWrite) return
        const payloads = getDragData(e)
        if (payloads.length) void handleMove(payloads, targetId)
      },
    }),
    [canWrite, handleMove]
  )

  const treeNodes = useMemo(
    () => (treeData ? treeToNodes(treeData, canWrite ? bindDropZone : undefined) : []),
    [treeData, canWrite, bindDropZone]
  )

  const buildContextMenu = (row: StorageFile | StorageFolder): MenuProps => {
    const folder = isFolder(row)
    const isRoot = folder && row.parent_id === null
    const file = folder ? null : row

    const items: MenuProps['items'] = []

    if (folder) {
      items.push({
        key: 'open',
        icon: <FolderOpenOutlined />,
        label: '打开',
        onClick: () => setFolderId(row.id),
      })
    } else if (file) {
      items.push({
        key: 'open',
        icon: <FolderOpenOutlined />,
        label: isPreviewableInBrowser(file.name, file.content_type) ? '预览' : '打开',
        onClick: () => void openFile(file),
      })
      items.push({
        key: 'download',
        icon: <DownloadOutlined />,
        label: '下载',
        onClick: () => void handleDownload(file),
      })
    }

    if (canWrite && !isRoot) {
      if (!folder) {
        items.push({
          key: 'copy',
          icon: <CopyOutlined />,
          label: '复制到…',
          onClick: () => openTransferModal('copy', [{ kind: 'file', id: row.id }]),
        })
      }
      items.push({
        key: 'move',
        icon: <ScissorOutlined />,
        label: '移动到…',
        onClick: () => openTransferModal('move', [{ kind: folder ? 'folder' : 'file', id: row.id }]),
      })
      items.push({
        key: 'rename',
        icon: <EditOutlined />,
        label: '重命名',
        onClick: () => openRenameModal(folder ? 'folder' : 'file', row.id, row.name),
      })
    }

    if (canWrite && visibility === 'private' && teams.length > 0 && !isRoot) {
      items.push({
        key: 'share',
        icon: <ShareAltOutlined />,
        label: '分享到团队',
        onClick: () => openShareModal({ kind: folder ? 'folder' : 'file', id: row.id, name: row.name }),
      })
    }

    if (canWrite && !isRoot) {
      items.push({ type: 'divider' })
      items.push({
        key: 'delete',
        icon: <DeleteOutlined />,
        label: '删除',
        danger: true,
        onClick: () => {
          Modal.confirm({
            title: folder ? '确认删除该文件夹及其全部内容？' : '确认删除该文件？',
            okText: '删除',
            okType: 'danger',
            onOk: async () => {
              if (folder) await handleDeleteFolder(row.id)
              else {
                await storageApi.deleteFile(row.id)
                message.success('文件已删除')
                invalidate()
              }
            },
          })
        },
      })
    }

    return { items }
  }

  const handleCreateTeam = async () => {
    const name = newTeamName.trim()
    if (!name) {
      message.warning('请输入团队名称')
      return
    }
    try {
      const created = await storageApi.createTeam({ name, description: newTeamDesc.trim() || undefined })
      message.success('团队已创建')
      setNewTeamName('')
      setNewTeamDesc('')
      setCreateTeamOpen(false)
      setAdminTeamId(created.id)
      refetchTeams()
      invalidate()
    } catch (e: unknown) {
      message.error((e as Error).message || '创建团队失败')
    }
  }

  const handleAddMember = async () => {
    if (!adminTeamId || !addMemberUserId) return
    try {
      await storageApi.addTeamMember(adminTeamId, { user_id: addMemberUserId, role: addMemberRole })
      message.success('成员已添加')
      setAddMemberUserId(undefined)
      refetchMembers()
      refetchTeams()
    } catch (e: unknown) {
      message.error((e as Error).message || '添加成员失败')
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (_: string, row: StorageFile | StorageFolder) => {
        const folder = isFolder(row)
        return (
          <Dropdown menu={buildContextMenu(row)} trigger={['contextMenu']}>
            <div
              className={`storage-row-name${dropTargetId === row.id ? ' is-drop-target' : ''}`}
              draggable={canWrite}
              onDragStart={(e) => setDragData(e, { kind: folder ? 'folder' : 'file', id: row.id })}
              {...(folder ? bindDropZone(row.id) : {})}
            >
              <Space>
                {folder ? getStorageFolderIcon(false) : getStorageFileIcon(row.name, row.content_type)}
                <span className="storage-name-text">{row.name}</span>
              </Space>
            </div>
          </Dropdown>
        )
      },
    },
    {
      title: '大小',
      key: 'size',
      width: 100,
      responsive: ['sm'] as const,
      render: (_: unknown, row: StorageFile | StorageFolder) =>
        isFolder(row) ? <span className="grok-muted">—</span> : formatSize(row.size_bytes),
    },
    {
      title: '类型',
      key: 'type',
      width: 88,
      responsive: ['md'] as const,
      render: (_: unknown, row: StorageFile | StorageFolder) =>
        isFolder(row) ? <GrokChip>文件夹</GrokChip> : <GrokChip>文件</GrokChip>,
    },
  ]

  const toolbar = (
    <>
      <Input
        className="grok-search-input"
        placeholder="搜索当前目录…"
        allowClear
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {isMobile && (
        <GrokToolBtn icon={<FolderOutlined />} onClick={() => setMobileTreeOpen(true)}>
          目录
        </GrokToolBtn>
      )}
      <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetch()}>
        刷新
      </GrokToolBtn>
      {isAdmin && (
        <GrokToolBtn icon={<TeamOutlined />} onClick={() => setTeamDrawerOpen(true)}>
          团队管理
        </GrokToolBtn>
      )}
      {canWrite && (
        <>
          <GrokToolBtn icon={<FolderAddOutlined />} onClick={() => setFolderModalOpen(true)}>
            新建文件夹
          </GrokToolBtn>
          <GrokToolBtn
            primary
            icon={<CloudUploadOutlined />}
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? '上传中…' : '上传'}
          </GrokToolBtn>
        </>
      )}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => handleUploadFiles(e.target.files)}
      />
    </>
  )

  const memberColumns = [
    {
      title: '成员',
      dataIndex: 'user_id',
      key: 'user_id',
      render: (uid: string) => (
        <div>
          <div>{userNameById.get(uid) || uid}</div>
          <div className="grok-muted" style={{ fontSize: 12 }}>
            ID {uid}
          </div>
        </div>
      ),
    },
    {
      title: '权限',
      dataIndex: 'role',
      key: 'role',
      width: 140,
      render: (role: string, row: StorageTeamMember) => (
        <Select
          size="small"
          value={role}
          style={{ width: 120 }}
          onChange={(v) =>
            storageApi.updateTeamMemberRole(adminTeamId!, row.user_id, v).then(() => {
              message.success('权限已更新')
              refetchMembers()
            })
          }
          options={[
            { value: 'owner', label: '所有者' },
            { value: 'member', label: '成员' },
            { value: 'viewer', label: '只读' },
          ]}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 88,
      render: (_: unknown, row: StorageTeamMember) => (
        <Popconfirm
          title="确认移除该成员？"
          onConfirm={() =>
            storageApi.removeTeamMember(adminTeamId!, row.user_id).then(() => {
              message.success('已移除')
              refetchMembers()
              refetchTeams()
            })
          }
        >
          <GrokRowAction danger icon={<DeleteOutlined />}>
            移除
          </GrokRowAction>
        </Popconfirm>
      ),
    },
  ]

  const existingMemberIds = new Set(teamMembers.map((m) => m.user_id))
  const selectedCount = selectedRowKeys.length
  const breadcrumbItems = listing?.breadcrumb || []

  const renderFolderTree = (onNavigate?: () => void) => (
    <Tree
      showIcon
      selectedKeys={folderId ? [folderId] : listing?.folder ? [listing.folder.id] : []}
      onSelect={(keys) => {
        setFolderId(keys[0] as string)
        onNavigate?.()
      }}
      treeData={treeNodes}
      defaultExpandAll
    />
  )

  return (
    <GrokShellLayout title="网盘" subtitle="个人与团队文件存储，支持多选、拖拽与右键操作" toolbar={toolbar}>
      <div className="grok-stat-grid">
        <div className="grok-stat-card">
          <div className="grok-stat-label">当前目录文件夹</div>
          <div className="grok-stat-value">{isLoading ? '—' : storageStats.folders}</div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">当前目录文件</div>
          <div className="grok-stat-value">{isLoading ? '—' : storageStats.files}</div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">当前目录大小</div>
          <div className="grok-stat-value grok-stat-value-sm">
            {isLoading ? '—' : formatSize(storageStats.bytes)}
          </div>
        </div>
        <div className="grok-stat-card">
          <div className="grok-stat-label">可访问团队</div>
          <div className="grok-stat-value">{teams.length}</div>
        </div>
      </div>

      <GrokInfoBar>
        <strong>操作提示</strong>
        <span>双击打开</span>
        <span>·</span>
        <span>右键菜单</span>
        <span>·</span>
        <span>拖放到目录树 / 面包屑 / 文件夹</span>
        {!canWrite && (
          <>
            <span>·</span>
            <GrokChip tone="warn">只读模式</GrokChip>
          </>
        )}
      </GrokInfoBar>

      <div className="grok-storage-scope">
        <div className="grok-storage-space-bar">
          <div className="grok-storage-space-bar-left">
            <div className="grok-segmented" role="tablist" aria-label="网盘空间">
              <button
                type="button"
                role="tab"
                className={`grok-segmented-btn${visibility === 'private' ? ' is-active' : ''}`}
                onClick={() => {
                  setVisibility('private')
                  setFolderId(undefined)
                }}
              >
                我的文件
              </button>
              <button
                type="button"
                role="tab"
                className={`grok-segmented-btn${visibility === 'shared' ? ' is-active' : ''}`}
                onClick={() => {
                  setVisibility('shared')
                  setFolderId(undefined)
                }}
              >
                团队共享
              </button>
            </div>
            {visibility === 'shared' && (
              <Select
                className="grok-storage-team-select"
                placeholder="选择团队"
                value={teamId}
                onChange={(v) => {
                  setTeamId(v)
                  setFolderId(undefined)
                }}
                options={teams.map((t) => ({
                  value: t.id,
                  label: `${t.name}（${t.member_count} 人）`,
                }))}
              />
            )}
            {visibility === 'shared' && teamId && (
              <GrokChip>
                {teams.find((t) => t.id === teamId)?.role === 'viewer' ? '只读成员' : '可编辑'}
              </GrokChip>
            )}
          </div>
        </div>

        {visibility === 'shared' && teams.length === 0 && (
          <div className="grok-notice">
            暂无团队空间。{isAdmin ? '点击工具栏「团队管理」创建团队并添加成员。' : '请联系管理员创建团队。'}
          </div>
        )}

        {(visibility === 'private' || teamId) && (
          <div className="grok-storage-layout">
            <aside
              className={`grok-panel-bordered grok-storage-sidebar grok-storage-tree${dropTargetId === currentFolderId ? ' is-drop-target' : ''}`}
              {...(currentFolderId && canWrite ? bindDropZone(currentFolderId) : {})}
            >
              <div className="grok-panel-head">
                <h3 className="grok-panel-title">文件夹</h3>
              </div>
              <p className="storage-hint">拖放至此可移入当前选中目录</p>
              <div className="grok-storage-sidebar-body">{renderFolderTree()}</div>
            </aside>

            <section className="grok-storage-main-panel">
              <div
                className={`grok-storage-main${dropTargetId === currentFolderId ? ' is-drop-target' : ''}`}
                {...(currentFolderId && canWrite ? bindDropZone(currentFolderId) : {})}
              >
                <div className="grok-storage-main-head">
                  <Breadcrumb
                    className="grok-storage-breadcrumb"
                    items={breadcrumbItems.map((b, index) => {
                      const isLast = index === breadcrumbItems.length - 1
                      return {
                        title: (
                          <span
                            className={`storage-breadcrumb-item${dropTargetId === b.id ? ' is-drop-target' : ''}`}
                            {...(canWrite ? bindDropZone(b.id) : {})}
                          >
                            <button
                              type="button"
                              className={`grok-breadcrumb-link${isLast ? ' is-current' : ''}`}
                              onClick={() => !isLast && setFolderId(b.id)}
                            >
                              {b.name}
                            </button>
                          </span>
                        ),
                      }
                    })}
                  />
                </div>

                {selectedCount > 0 && (
                  <div className="storage-batch-bar">
                    <span>
                      已选 <strong>{selectedCount}</strong> 项
                    </span>
                    <div className="storage-batch-actions">
                      <GrokToolBtn icon={<DownloadOutlined />} onClick={() => void handleBatchDownload()}>
                        下载
                      </GrokToolBtn>
                      {canWrite && (
                        <>
                          <GrokToolBtn
                            icon={<ScissorOutlined />}
                            onClick={() => openTransferModal('move', payloadsFromSelection())}
                          >
                            移动
                          </GrokToolBtn>
                          <GrokToolBtn
                            icon={<CopyOutlined />}
                            onClick={() => {
                              const files = payloadsFromSelection().filter((p) => p.kind === 'file')
                              if (!files.length) {
                                message.warning('请选择要复制的文件')
                                return
                              }
                              openTransferModal('copy', files)
                            }}
                          >
                            复制
                          </GrokToolBtn>
                          <Popconfirm
                            title={`确认删除选中的 ${selectedCount} 项？`}
                            onConfirm={() => void handleBatchDelete()}
                          >
                            <GrokToolBtn className="is-danger" icon={<DeleteOutlined />}>
                              删除
                            </GrokToolBtn>
                          </Popconfirm>
                        </>
                      )}
                      <GrokToolBtn onClick={() => setSelectedRowKeys([])}>取消</GrokToolBtn>
                    </div>
                  </div>
                )}

                <section className="grok-panel grok-panel-flush">
                  {isLoading ? (
                    <div className="grok-page-loading is-compact">
                      <Spin />
                    </div>
                  ) : filteredTableData.length === 0 ? (
                    <div className="storage-empty-hint">
                      {search.trim() ? (
                        <>未找到匹配「{search.trim()}」的项目</>
                      ) : (
                        <>
                          此目录为空
                          <br />
                          上传文件，或拖放到目录树 / 面包屑 / 文件夹行
                        </>
                      )}
                    </div>
                  ) : (
                    <Table
                      className="grok-table storage-file-table"
                      rowKey="key"
                      columns={columns}
                      dataSource={filteredTableData}
                      pagination={false}
                      size={isMobile ? 'small' : 'middle'}
                      rowSelection={
                        canWrite
                          ? {
                              selectedRowKeys,
                              onChange: setSelectedRowKeys,
                              selections: [Table.SELECTION_ALL, Table.SELECTION_INVERT],
                            }
                          : undefined
                      }
                      onRow={(row) => ({
                        onDoubleClick: () => {
                          if (isFolder(row)) setFolderId(row.id)
                          else void openFile(row as StorageFile)
                        },
                      })}
                      locale={{ emptyText: ' ' }}
                    />
                  )}
                </section>
              </div>
            </section>
          </div>
        )}
      </div>

      <Drawer
        title="文件夹"
        placement="left"
        open={mobileTreeOpen}
        onClose={() => setMobileTreeOpen(false)}
        width={Math.min(300, typeof window !== 'undefined' ? window.innerWidth * 0.86 : 300)}
      >
        {renderFolderTree(() => setMobileTreeOpen(false))}
      </Drawer>

      <Modal
        title={transferMode === 'move' ? '移动到' : '复制到'}
        open={transferModalOpen}
        onCancel={() => setTransferModalOpen(false)}
        onOk={() => void confirmTransfer()}
        okText={transferMode === 'move' ? '移动' : '复制'}
      >
        <p className="grok-muted" style={{ marginBottom: 12 }}>
          {transferMode === 'move'
            ? `将 ${transferTargets.length} 项移动到：`
            : `将 ${transferTargets.length} 个文件复制到：`}
        </p>
        <Select
          style={{ width: '100%' }}
          showSearch
          optionFilterProp="label"
          placeholder="选择目标文件夹"
          value={transferTargetFolderId}
          onChange={setTransferTargetFolderId}
          options={folderOptions}
        />
      </Modal>

      <Modal
        title="新建文件夹"
        open={folderModalOpen}
        onCancel={() => setFolderModalOpen(false)}
        onOk={handleCreateFolder}
        okText="创建"
      >
        <Input
          placeholder="文件夹名称"
          value={newFolderName}
          onChange={(e) => setNewFolderName(e.target.value)}
          onPressEnter={handleCreateFolder}
        />
      </Modal>

      <Modal
        title={`分享到团队：${shareTarget?.name || ''}`}
        open={shareModalOpen}
        onCancel={() => setShareModalOpen(false)}
        onOk={handleShare}
        okText="分享"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div className="grok-muted" style={{ marginBottom: 6 }}>
              目标团队
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择团队"
              value={shareTeamId}
              onChange={(v) => {
                setShareTeamId(v)
                setShareTargetFolderId(undefined)
              }}
              options={teams.map((t) => ({ value: t.id, label: t.name }))}
            />
          </div>
          <div>
            <div className="grok-muted" style={{ marginBottom: 6 }}>
              团队内目标文件夹（可选）
            </div>
            <Select
              style={{ width: '100%' }}
              allowClear
              placeholder="默认放入团队根目录"
              value={shareTargetFolderId}
              onChange={setShareTargetFolderId}
              options={shareFolderOptions}
            />
          </div>
        </Space>
      </Modal>

      <Modal
        title="重命名"
        open={renameModalOpen}
        onCancel={() => setRenameModalOpen(false)}
        onOk={handleRename}
        okText="保存"
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onPressEnter={handleRename}
        />
      </Modal>

      <Modal
        title={previewFile?.name || '预览'}
        open={previewOpen}
        onCancel={() => {
          if (previewUrl) URL.revokeObjectURL(previewUrl)
          setPreviewOpen(false)
        }}
        footer={[
          <Button key="open" onClick={() => previewFile && void openWithLocalApp(previewFile)}>
            用本地程序打开
          </Button>,
          <Button key="dl" onClick={() => previewFile && handleDownload(previewFile)}>
            下载
          </Button>,
          <Button
            key="close"
            type="primary"
            onClick={() => {
              if (previewUrl) URL.revokeObjectURL(previewUrl)
              setPreviewOpen(false)
            }}
          >
            关闭
          </Button>,
        ]}
        width={860}
        destroyOnClose
      >
        {previewLoading ? (
          <div className="grok-page-loading is-compact">
            <Spin />
          </div>
        ) : previewText ? (
          <pre className="storage-preview-text">{previewText}</pre>
        ) : previewUrl && (previewFile?.content_type?.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(previewFile?.name || '')) ? (
          <img src={previewUrl} alt={previewFile?.name} className="storage-preview-image" />
        ) : previewUrl && (previewFile?.content_type === 'application/pdf' || /\.pdf$/i.test(previewFile?.name || '')) ? (
          <iframe title="preview" src={previewUrl} className="storage-preview-frame" />
        ) : previewUrl && previewFile?.content_type?.startsWith('video/') ? (
          <video src={previewUrl} controls className="storage-preview-media" />
        ) : previewUrl && previewFile?.content_type?.startsWith('audio/') ? (
          <audio src={previewUrl} controls className="storage-preview-media" />
        ) : (
          <p className="grok-muted">无法内嵌预览，请使用「用本地程序打开」或下载。</p>
        )}
      </Modal>

      <Drawer
        title="团队管理"
        width={isMobile ? '100%' : 720}
        open={teamDrawerOpen}
        onClose={() => setTeamDrawerOpen(false)}
        className="grok-team-drawer"
        destroyOnClose
      >
        <div className="grok-team-drawer-layout">
          <aside className="grok-team-list">
            <div className="grok-team-list-head">
              <h3>团队列表</h3>
              {isAdmin && (
                <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreateTeamOpen(true)}>
                  新建
                </Button>
              )}
            </div>
            <div className="grok-team-list-body">
              {teams.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无团队" />
              ) : (
                teams.map((team: StorageTeam) => (
                  <button
                    key={team.id}
                    type="button"
                    className={`grok-team-card${adminTeamId === team.id ? ' is-active' : ''}`}
                    onClick={() => setAdminTeamId(team.id)}
                  >
                    <div className="grok-team-card-name">{team.name}</div>
                    <div className="grok-team-card-meta">
                      {team.member_count} 名成员
                      {team.description ? ` · ${team.description}` : ''}
                    </div>
                  </button>
                ))
              )}
            </div>
          </aside>

          <div className="grok-team-detail">
            {!adminTeamId || !selectedTeam ? (
              <div className="grok-team-empty">
                <TeamOutlined style={{ fontSize: 28, color: '#cbd5e1' }} />
                <p>请从左侧选择一个团队，管理成员与权限</p>
              </div>
            ) : (
              <>
                <div className="grok-team-detail-head">
                  <h2>{selectedTeam.name}</h2>
                  {selectedTeam.description && (
                    <p className="grok-team-detail-desc">{selectedTeam.description}</p>
                  )}
                  <div className="grok-team-detail-actions">
                    <GrokChip>{selectedTeam.member_count} 名成员</GrokChip>
                    {isAdmin && (
                      <Popconfirm
                        title="确认删除该团队？"
                        description="不会删除已分享的文件对象"
                        onConfirm={() =>
                          storageApi.deleteTeam(adminTeamId).then(() => {
                            message.success('团队已删除')
                            setAdminTeamId(undefined)
                            refetchTeams()
                            refetchMembers()
                          })
                        }
                      >
                        <GrokToolBtn className="is-danger" icon={<DeleteOutlined />}>
                          删除团队
                        </GrokToolBtn>
                      </Popconfirm>
                    )}
                  </div>
                </div>

                <div className="grok-team-detail-body">
                  <div className="grok-panel-head">
                    <h3 className="grok-panel-title">成员与权限</h3>
                  </div>

                  {isAdmin && (
                    <div className="grok-team-add-row">
                      <UserAddOutlined style={{ color: '#94a3b8' }} />
                      <Select
                        style={{ minWidth: 180, flex: 1 }}
                        placeholder="选择用户"
                        value={addMemberUserId}
                        onChange={setAddMemberUserId}
                        showSearch
                        optionFilterProp="label"
                        options={(allUsers as ManagedUser[])
                          .filter((u) => !existingMemberIds.has(String(u.id)))
                          .map((u) => ({ value: String(u.id), label: `${u.username} (#${u.id})` }))}
                      />
                      <Select
                        style={{ width: 110 }}
                        value={addMemberRole}
                        onChange={setAddMemberRole}
                        options={[
                          { value: 'owner', label: '所有者' },
                          { value: 'member', label: '成员' },
                          { value: 'viewer', label: '只读' },
                        ]}
                      />
                      <Button type="primary" icon={<PlusOutlined />} onClick={handleAddMember}>
                        添加
                      </Button>
                    </div>
                  )}

                  <section className="grok-panel grok-panel-flush">
                    <Table
                      className="grok-table"
                      size="small"
                      rowKey="id"
                      columns={memberColumns}
                      dataSource={teamMembers}
                      pagination={false}
                      locale={{ emptyText: '暂无成员，请添加用户' }}
                    />
                  </section>
                </div>
              </>
            )}
          </div>
        </div>
      </Drawer>

      <Modal
        title="新建团队"
        open={createTeamOpen}
        onCancel={() => setCreateTeamOpen(false)}
        onOk={handleCreateTeam}
        okText="创建"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Input
            placeholder="团队名称"
            value={newTeamName}
            onChange={(e) => setNewTeamName(e.target.value)}
            onPressEnter={handleCreateTeam}
          />
          <Input.TextArea
            placeholder="团队描述（可选）"
            value={newTeamDesc}
            onChange={(e) => setNewTeamDesc(e.target.value)}
            rows={3}
          />
        </Space>
      </Modal>
    </GrokShellLayout>
  )
}

export default StoragePage
