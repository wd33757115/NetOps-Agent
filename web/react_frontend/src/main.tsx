// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from 'react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import { AppMessageBridge } from './utils/appMessage'
import '@harmony-font-regular.css'
import '@harmony-font-medium.css'
import '@harmony-font-semibold.css'
import './styles/fonts.css'
import './index.css'

const queryClient = new QueryClient()

const fontFamily =
  "'PingFang SC', 'HarmonyOS Sans SC', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif"

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#0958d9',
            colorSuccess: '#16a34a',
            colorWarning: '#d97706',
            colorError: '#dc2626',
            colorText: '#0f172a',
            colorTextSecondary: '#475569',
            colorBorderSecondary: '#e2e8f0',
            controlHeight: 36,
            controlHeightSM: 28,
            borderRadius: 6,
            borderRadiusLG: 8,
            fontSize: 14,
            fontSizeSM: 13,
            fontFamily,
          },
        }}
      >
        <AntdApp>
          <AppMessageBridge />
          <App />
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
