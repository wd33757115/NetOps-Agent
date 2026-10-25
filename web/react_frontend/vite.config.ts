// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import path from 'node:path'
import './scripts/generate-apps.mjs'
import { hostDependencies } from './scripts/package-resolve.mjs'
import { appBuildMetadata } from './scripts/app-build.mjs'
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
  // Uploads go to /object-storage/... on this server and on to MinIO unchanged. The target
  // must be the address the gateway signs uploads for (its MINIO_ENDPOINT), because the
  // signature covers the Host header that MinIO receives.
  const objectStorage = {
    target: env.VITE_OBJECT_STORAGE_URL || 'http://127.0.0.1:9000',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/object-storage/, ''),
  }

  return {
    plugins: [
      appBuildMetadata(),
      react({ fastRefresh: false }),
      {
        name: 'strip-crossorigin-for-embedded-browser',
        transformIndexHtml(html) {
          return html.replace(/\s+crossorigin/g, '')
        },
      },
    ],
    resolve: {
      dedupe: hostDependencies,
      alias: {
        '@netops': path.resolve(__dirname, 'src'),
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
      fs: { allow: [path.resolve(__dirname, '../..')] },
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
        },
        '/object-storage': objectStorage
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
        },
        '/object-storage': objectStorage
      }
    }
  }
})
