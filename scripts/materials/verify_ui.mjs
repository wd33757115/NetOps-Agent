// Isolated browser check: synthetic API responses, no Gateway, credentials or model.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const frontend = path.join(root, 'web/react_frontend')
const require = createRequire(path.join(frontend, 'package.json'))
const { createServer } = await import(pathToFileURL(require.resolve('vite')))
const { chromium, expect } = require('@playwright/test')
const { default: AxeBuilder } = await import(pathToFileURL(require.resolve('@axe-core/playwright')))
const { hostDependencies } = await import(pathToFileURL(path.join(frontend, 'scripts/package-resolve.mjs')))
const output = path.join(root, '.runtime/materials-validation')
await mkdir(output, { recursive: true })
const entry = '/__materials_entry.tsx'
const server = await createServer({
  configFile: false, root: frontend, logLevel: 'error',
  optimizeDeps: { entries: [], include: ['react', 'react-dom/client', 'antd', 'axios'] },
  resolve: { alias: { '@netops': path.join(frontend, 'src') }, dedupe: hostDependencies },
  server: { host: '127.0.0.1', port: 0, fs: { allow: [root] } },
  plugins: [{
    name: 'materials-isolated-fixture',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url !== '/__materials_test') return next()
        res.setHeader('Content-Type', 'text/html; charset=utf-8')
        res.end('<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>资料管理验证</title></head><body style="margin:0"><main id="root"></main><script type="module" src="' + entry + '"></script></body></html>')
      })
    },
    resolveId(id) { if (id === entry) return entry },
    load(id) {
      if (id !== entry) return null
      return "import React from 'react'; import {ConfigProvider} from 'antd'; import {createRoot} from 'react-dom/client'; import Page from '/@fs/" +
        path.join(root, 'src/apps/materials/frontend/Page.tsx').replaceAll('\\', '/') +
        "'; createRoot(document.getElementById('root')).render(<ConfigProvider theme={{token:{colorPrimary:'#0958d9',colorLink:'#0958d9',colorTextSecondary:'#475569',fontFamily:'Arial, sans-serif'}}}><Page/></ConfigProvider>);"
    },
  }],
})
let browser
let page
try {
  await server.listen()
  const port = server.httpServer.address().port
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  page = await context.newPage()
  const errors = []
  page.on('pageerror', error => { errors.push(error.message); console.error(error.message) })
  let published = false
  const sections = [
    { id: 'line-1', title_path: ['接口配置', '链路聚合'], start_line: 1, text: '配置前核对设备型号和软件版本。\n执行 display interface 检查接口状态。' },
    { id: 'line-8', title_path: ['路由配置', 'BGP EVPN'], start_line: 8, text: '此处为合成验证内容，不是手册原文。' },
  ]
  await page.route('**/api/apps/materials/**', async route => {
    const url = new URL(route.request().url())
    let result
    if (url.pathname.endsWith('/preview')) result = {
      source_sha256: 'a'.repeat(64), normalized_sha256: 'b'.repeat(64), sections,
      warnings: ['图片、拓扑图和图中文字未提取（2处）', '自动编号未还原，请核对原文中的步骤和条目号'],
    }
    else if (url.pathname.endsWith('/import')) {
      assert.match(route.request().headers()['content-type'], /multipart\/form-data; boundary=/)
      assert.doesNotMatch(route.request().postData(), /"section_ids"/)
      published = true; result = { id: 'new' }
    }
    else result = [{
      id: 'manual', title: '数据中心网络配置手册', revision: 'a'.repeat(64), sections: 24,
      status: 'active', publication_scope: 'whole_file', metadata: { vendor: 'Huawei', product: 'Cloud Stack', software_version: '8.2.1' }, has_original: true,
    }]
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) })
  })
  await page.goto('http://127.0.0.1:' + port + '/__materials_test')
  await page.getByRole('button', { name: '数据中心网络配置手册', exact: true }).waitFor()
  await page.screenshot({ path: path.join(output, 'desktop-list.png') })
  await page.getByRole('button', { name: '导入资料', exact: true }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: '合成手册.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: Buffer.from('synthetic fixture'),
  })
  await page.getByRole('button', { name: '解析并预览' }).click()
  await page.getByText('图片、拓扑图和图中文字未提取（2处）').waitFor()
  await page.getByRole('button', { name: '接口配置 / 链路聚合' }).click()
  await page.getByLabel('已核对提取预览与缺失提示，确认发布整个文件').check()
  await expect(page.getByRole('button', { name: '发布整个文件' })).toBeEnabled()
  await page.screenshot({ path: path.join(output, 'desktop-import.png'), animations: 'disabled' })
  const accessibility = await new AxeBuilder({ page }).analyze()
  if (accessibility.violations.length) console.log(JSON.stringify(accessibility.violations.map(v => ({
    id: v.id, impact: v.impact, nodes: v.nodes.map(n => ({ html: n.html, summary: n.failureSummary })),
  }))))
  assert.deepEqual(accessibility.violations.filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id), [])
  await page.getByRole('button', { name: '发布整个文件' }).click()
  await page.getByText('资料已发布，可在问答中按需检索。').waitFor()
  assert.ok(published)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('input[type="file"]').setInputFiles({
    name: '合成手册.docx', mimeType: 'application/octet-stream', buffer: Buffer.from('fixture'),
  })
  await page.getByRole('button', { name: '解析并预览' }).click()
  await page.getByText('图片、拓扑图和图中文字未提取（2处）').waitFor()
  await page.screenshot({ path: path.join(output, 'mobile-import.png') })
  assert.ok(await page.locator('.materials-app').evaluate(el => el.scrollWidth <= window.innerWidth + 1))
  assert.deepEqual(errors, [])
  console.log('Browser checks passed: multipart preview/publication, whole file, desktop/mobile, no serious accessibility findings.')
} catch (error) {
  await page?.screenshot({ path: path.join(output, 'failure.png') })
  throw error
} finally {
  await browser?.close()
  await server.close()
}
