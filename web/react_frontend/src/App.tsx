// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/Layout/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import AdminRoute from './components/AdminRoute'
import { AuthProvider } from './context/AuthContext'
import ChatPage from './pages/ChatPage'
import SkillsPage from './pages/SkillsPage'
import KnowledgePage from './pages/KnowledgePage'
import StatusPage from './pages/StatusPage'
import StoragePage from './pages/StoragePage'
import UsersPage from './pages/UsersPage'
import LoginPage from './pages/LoginPage'
import DevicesPage from './pages/DevicesPage'

const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/chat" replace />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="storage" element={<StoragePage />} />
            <Route path="status" element={<StatusPage />} />
            <Route
              path="users"
              element={
                <AdminRoute>
                  <UsersPage />
                </AdminRoute>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
