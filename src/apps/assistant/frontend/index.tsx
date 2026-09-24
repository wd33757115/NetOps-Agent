import React from 'react'
import { Navigate } from 'react-router-dom'
import type { AppFrontend } from '@netops/apps/types'

function ChatRedirect() {
  return <Navigate to="/chat" replace />
}

export default {
  routes: [{ path: '', component: ChatRedirect }],
} satisfies AppFrontend
