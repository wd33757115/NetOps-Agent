// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { Suspense } from 'react'
import { Spin } from 'antd'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppLayout from './components/Layout/AppLayout'
import ProtectedRoute from './components/ProtectedRoute'
import AdminRoute from './components/AdminRoute'
import BusinessRoute from './components/BusinessRoute'
import { workbenchEnabled } from './apps/appRoutes'
import { packagedApps } from './apps/generated'
import PackageRoute from './apps/PackageRoute'
import { AuthProvider } from './context/AuthContext'

const ChatPage = React.lazy(() => import('./pages/ChatPage'))
const SkillsPage = React.lazy(() => import('./pages/SkillsPage'))
const StatusPage = React.lazy(() => import('./pages/StatusPage'))
const StoragePage = React.lazy(() => import('./pages/StoragePage'))
const UsersPage = React.lazy(() => import('./pages/UsersPage'))
const SystemGroupsPage = React.lazy(() => import('./pages/SystemGroupsPage'))
const SystemAppsPage = React.lazy(() => import('./pages/SystemAppsPage'))
const LoginPage = React.lazy(() => import('./pages/LoginPage'))
const DevicesPage = React.lazy(() => import('./pages/DevicesPage'))
const WorkbenchPage = React.lazy(() => import('./pages/WorkbenchPage'))
const PatrolAppPage = React.lazy(() => import('./pages/PatrolAppPage'))
const PatrolRulesAppPage = React.lazy(() => import('./pages/PatrolRulesAppPage'))
const RuleAssetSourceJobDetailPage = React.lazy(() => import('./pages/RuleAssetSourceJobDetailPage'))

const pageFallback = (
  <div className="grok-page-loading" role="status" aria-label="页面加载中">
    <Spin />
  </div>
)

function LegacyParserRedirect() {
  const { pathname, search, hash } = useLocation()
  return <Navigate to={pathname.replace('/skills/parser', '/apps/rule-governance') + search + hash} replace />
}

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
            <Route path="chat" element={<PackageRoute appId="assistant"><ChatPage /></PackageRoute>} />
            {packagedApps.filter(app => app.enabled).flatMap(app => app.frontend.routes.map(({ path, component: Page }) => (
              <Route key={`${app.id}/${path}`} path={`apps/${app.id}${path ? '/' + path : ''}`} element={<PackageRoute appId={app.id}><Page /></PackageRoute>} />
            )))}
            <Route path="skills" element={<SkillsPage />} />
            <Route path="workbench" element={workbenchEnabled ? <WorkbenchPage /> : <Navigate to="/chat" replace />} />
            <Route path="apps/patrol" element={<BusinessRoute><PackageRoute appId="inspection"><PatrolAppPage /></PackageRoute></BusinessRoute>} />
            <Route path="apps/patrol-rules" element={<BusinessRoute><PackageRoute appId="rule-governance"><PatrolRulesAppPage /></PackageRoute></BusinessRoute>} />
            <Route path="apps/patrol-rules/source-jobs/:jobId" element={<BusinessRoute><PackageRoute appId="rule-governance"><RuleAssetSourceJobDetailPage /></PackageRoute></BusinessRoute>} />
            <Route path="skills/parser/*" element={<LegacyParserRedirect />} />
            <Route path="skills/:section" element={<SkillsPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="admin/netops/devices" element={<DevicesPage />} />
            <Route path="apps/rule-governance/devices" element={<DevicesPage />} />
            <Route path="storage" element={<StoragePage />} />
            <Route path="status" element={<StatusPage />} />
            <Route path="users" element={<AdminRoute><Navigate to="/system/users" replace /></AdminRoute>} />
            <Route path="system" element={<AdminRoute appGrants><Navigate to="/system/apps" replace /></AdminRoute>} />
            <Route path="system/users" element={<AdminRoute><UsersPage /></AdminRoute>} />
            <Route path="system/groups" element={<AdminRoute><SystemGroupsPage /></AdminRoute>} />
            <Route path="system/apps" element={<AdminRoute appGrants><SystemAppsPage /></AdminRoute>} />
          </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
