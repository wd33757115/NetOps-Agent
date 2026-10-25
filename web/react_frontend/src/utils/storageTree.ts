// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

export type FolderTreeNode = {
  id: string
  name: string
  parent_id?: string | null
  children?: FolderTreeNode[]
}

export function flattenFolderOptions(
  node: FolderTreeNode,
  depth = 0
): { value: string; label: string }[] {
  const prefix = depth > 0 ? `${'　'.repeat(depth)}└ ` : ''
  const items = [{ value: node.id, label: `${prefix}${node.name}` }]
  for (const child of node.children || []) {
    items.push(...flattenFolderOptions(child, depth + 1))
  }
  return items
}

export function collectFolderSubtreeIds(tree: FolderTreeNode, rootId: string): Set<string> | null {
  const find = (node: FolderTreeNode): FolderTreeNode | null => {
    if (node.id === rootId) return node
    for (const child of node.children || []) {
      const hit = find(child)
      if (hit) return hit
    }
    return null
  }

  const root = find(tree)
  if (!root) return null

  const ids = new Set<string>()
  const walk = (node: FolderTreeNode) => {
    ids.add(node.id)
    for (const child of node.children || []) walk(child)
  }
  walk(root)
  return ids
}

export function isInvalidFolderMoveTarget(
  tree: FolderTreeNode | undefined,
  folderId: string,
  targetId: string
): boolean {
  if (folderId === targetId) return true
  if (!tree) return false
  const subtree = collectFolderSubtreeIds(tree, folderId)
  return subtree?.has(targetId) ?? false
}

export function parseStorageRowKey(key: string): { kind: 'file' | 'folder'; id: string } | null {
  if (key.startsWith('file-')) return { kind: 'file', id: key.slice(5) }
  if (key.startsWith('folder-')) return { kind: 'folder', id: key.slice(7) }
  return null
}
