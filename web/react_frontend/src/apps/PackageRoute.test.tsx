import React from 'react'
import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import PackageRoute from './PackageRoute'
import { canOpenApp } from './appRegistry'

const auth = vi.hoisted(() => ({ user: { app_ids: [] as string[] }, loading: false }))
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }))

it('blocks direct routes until the server grants the application', () => {
  const { rerender } = render(<PackageRoute appId="official-documents"><p>document editor</p></PackageRoute>)
  expect(screen.queryByText('document editor')).not.toBeInTheDocument()
  auth.user.app_ids = ['official-documents']
  rerender(<PackageRoute appId="official-documents"><p>document editor</p></PackageRoute>)
  expect(screen.getByText('document editor')).toBeInTheDocument()
  auth.user.app_ids = []
  rerender(<PackageRoute appId="official-documents"><p>document editor</p></PackageRoute>)
  expect(screen.queryByText('document editor')).not.toBeInTheDocument()
})

it('keeps the saved desktop alias but does not grant from a legacy role', () => {
  const user = { id: 1, username: 'test', email: '', role: 'admin', app_ids: [] as string[] }
  expect(canOpenApp('official-document', user)).toBe(false)
  user.role = 'viewer'
  user.app_ids = ['official-documents']
  expect(canOpenApp('official-document', user)).toBe(true)
})
