// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { Suspense } from 'react'
import { Spin } from 'antd'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/Layout/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import AdminRoute from './components/AdminRoute'
import BusinessRoute from './components/BusinessRoute'
import { workbenchEnabled, documentsEnabled } from './apps/appRoutes'
import { AuthProvider } from './context/AuthContext'

const ChatPage = React.lazy(() => import('./pages/ChatPage'))
const SkillsPage = React.lazy(() => import('./pages/SkillsPage'))
const KnowledgePage = React.lazy(() => import('./pages/KnowledgePage'))
const StatusPage = React.lazy(() => import('./pages/StatusPage'))
const StoragePage = React.lazy(() => import('./pages/StoragePage'))
const UsersPage = React.lazy(() => import('./pages/UsersPage'))
const LoginPage = React.lazy(() => import('./pages/LoginPage'))
const DevicesPage = React.lazy(() => import('./pages/DevicesPage'))
const WorkbenchPage = React.lazy(() => import('./pages/WorkbenchPage'))
const OfficialDocumentListPage = React.lazy(() => import('./pages/OfficialDocumentListPage'))
const OfficialDocumentCreatePage = React.lazy(() => import('./pages/OfficialDocumentCreatePage'))
const OfficialDocumentEditorPage = React.lazy(() => import('./pages/OfficialDocumentEditorPage'))
const PatrolAppPage = React.lazy(() => import('./pages/PatrolAppPage'))
const PatrolRulesAppPage = React.lazy(() => import('./pages/PatrolRulesAppPage'))
const RuleAssetSourceJobDetailPage = React.lazy(() => import('./pages/RuleAssetSourceJobDetailPage'))

const pageFallback = (
  <div className="grok-page-loading" role="status" aria-label="页面加载中">
    <Spin />
  </div>
)

const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Suspense fallback={pageFallback}>
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
            <Route path="apps/official-documents" element={documentsEnabled ? <OfficialDocumentListPage /> : <Navigate to="/chat" replace />} />
            <Route path="apps/official-documents/new" element={documentsEnabled ? <OfficialDocumentCreatePage /> : <Navigate to="/chat" replace />} />
            <Route path="apps/official-documents/:documentId" element={documentsEnabled ? <OfficialDocumentEditorPage /> : <Navigate to="/chat" replace />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="workbench" element={workbenchEnabled ? <WorkbenchPage /> : <Navigate to="/chat" replace />} />
            <Route path="apps/patrol" element={<BusinessRoute><PatrolAppPage /></BusinessRoute>} />
            <Route path="apps/patrol-rules" element={<BusinessRoute><PatrolRulesAppPage /></BusinessRoute>} />
            <Route path="apps/patrol-rules/source-jobs/:jobId" element={<BusinessRoute><RuleAssetSourceJobDetailPage /></BusinessRoute>} />
            <Route path="skills/parser" element={<BusinessRoute><SkillsPage /></BusinessRoute>} />
            <Route path="skills/parser/source-jobs/:jobId" element={<BusinessRoute><RuleAssetSourceJobDetailPage /></BusinessRoute>} />
            <Route path="skills/:section" element={<SkillsPage />} />
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
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
