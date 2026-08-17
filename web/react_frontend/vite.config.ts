// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const harmonyFontDist = path.resolve(__dirname, 'node_modules/harmonyos-sans-sc-webfont-splitted/dist')

const vendorChunk = (id: string) => {
  const moduleId = id.replace(/\\/g, '/')
  if (!moduleId.includes('/node_modules/')) return undefined
  if (/\/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom)\//.test(moduleId)) {
    return 'vendor-react'
  }
  if (moduleId.includes('/node_modules/@tanstack/') || moduleId.includes('/node_modules/axios/')) {
    return 'vendor-data'
  }
  if (moduleId.includes('/node_modules/echarts/') || moduleId.includes('/node_modules/zrender/')) {
    return 'vendor-charts'
  }
  return undefined
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const djangoTarget = env.VITE_DJANGO_BACKEND_URL || 'http://localhost:8001'

  return {
    plugins: [
      react({ fastRefresh: false }),
      {
        name: 'strip-crossorigin-for-embedded-browser',
        transformIndexHtml(html) {
          return html.replace(/\s+crossorigin/g, '')
        },
      },
    ],
    resolve: {
      alias: {
        '@harmony-font-regular.css': path.join(harmonyFontDist, 'Regular.css'),
        '@harmony-font-medium.css': path.join(harmonyFontDist, 'Medium.css'),
        '@harmony-font-semibold.css': path.join(harmonyFontDist, 'Semibold.css'),
      },
    },
    optimizeDeps: {
      needsInterop: ['react', 'react-dom', 'react-dom/client'],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: vendorChunk,
        },
      },
    },
    server: {
      port: 3000,
      strictPort: true,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: djangoTarget,
          changeOrigin: true
        },
        '/ws': {
          target: djangoTarget,
          changeOrigin: true,
          ws: true
        }
      }
    },
    preview: {
      port: 3000,
      strictPort: true,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: djangoTarget,
          changeOrigin: true
        },
        '/ws': {
          target: djangoTarget,
          changeOrigin: true,
          ws: true
        }
      }
    }
  }
})
