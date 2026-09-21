import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DocumentActions from './DocumentActions'

const mocks = vi.hoisted(() => ({ patch: vi.fn(), delete: vi.fn() }))
vi.mock('@netops/config/api', () => ({ api: mocks }))
const document = { id: 'doc', title: '手册', revision: 'a'.repeat(64), status: 'active' as const, metadata: { product: 'Cloud' } }
beforeEach(() => { vi.clearAllMocks(); mocks.patch.mockResolvedValue({}); mocks.delete.mockResolvedValue({}) })
afterEach(cleanup)

it('edits metadata as a revision without uploading the body', async () => {
  const changed = vi.fn().mockResolvedValue(undefined)
  render(<DocumentActions document={document} onChanged={changed} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑资料信息' }))
  fireEvent.change(screen.getByLabelText('编辑软件版本'), { target: { value: '8.2.1' } })
  fireEvent.click(screen.getByRole('button', { name: '保存为新修订' }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  expect(mocks.patch).toHaveBeenCalledWith('/apps/materials/documents/doc/metadata', {
    title: '手册', metadata: { product: 'Cloud', software_version: '8.2.1' }, expected_revision: document.revision,
  })
})

it('requires confirmation and guards deletion with the visible revision and status', async () => {
  const changed = vi.fn().mockResolvedValue(undefined)
  render(<DocumentActions document={document} onChanged={changed} />)
  fireEvent.click(screen.getByRole('button', { name: '删除资料' }))
  expect(mocks.delete).not.toHaveBeenCalled()
  expect(await screen.findByText(/历史修订及原文件仍保留/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /^删\s*除$/ }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  expect(mocks.delete).toHaveBeenCalledWith('/apps/materials/documents/doc', { data: { expected_revision: document.revision, expected_status: 'active' } })
})

it('restores inactive documents and surfaces conflicts without claiming success', async () => {
  const changed = vi.fn()
  mocks.patch.mockRejectedValue({ response: { data: { detail: '资料修订或状态已变化，请刷新后重试' } } })
  render(<DocumentActions document={{ ...document, status: 'inactive' }} onChanged={changed} />)
  fireEvent.click(screen.getByRole('button', { name: '恢复资料' }))
  fireEvent.click(await screen.findByRole('button', { name: /^确\s*认$/ }))
  expect(await screen.findByText('资料修订或状态已变化，请刷新后重试')).toBeInTheDocument()
  expect(changed).not.toHaveBeenCalled()
  expect(mocks.patch).toHaveBeenCalledWith('/apps/materials/documents/doc/status', {
    expected_revision: document.revision, expected_status: 'inactive', status: 'active',
  })
})
