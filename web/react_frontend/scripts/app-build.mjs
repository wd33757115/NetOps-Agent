import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { loadEnv } from 'vite'

export function appBuildMetadata() {
  let outDir
  let apps
  return {
    name: 'netops-app-deployment-metadata',
    apply: 'build',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
      const metadata = JSON.parse(readFileSync(path.join(config.root, 'src/apps/generated.meta.json'), 'utf8'))
      const env = { ...loadEnv(config.mode, config.root, ''), ...process.env }
      apps = Object.fromEntries(Object.entries(metadata.apps).filter(([id]) => !metadata.flags[id] || env[metadata.flags[id]] !== 'false'))
      for (const [name, expected] of Object.entries(metadata.frontendDependencies)) {
        const actual = JSON.parse(readFileSync(path.join(config.root, 'node_modules', name, 'package.json'), 'utf8')).version
        if (actual !== expected) throw new Error(`Package dependency ${name} differs from the lock; run npm ci`)
      }
    },
    closeBundle() {
      const assets = {}
      for (const relative of readdirSync(outDir, { recursive: true })) {
        if (!/\.(js|css|html)$/.test(relative)) continue
        assets[relative.replaceAll('\\', '/')] = createHash('sha256').update(readFileSync(path.join(outDir, relative))).digest('hex')
      }
      writeFileSync(path.join(outDir, 'netops-apps.json'), JSON.stringify({ mode: 'production', apps, assets }))
    },
  }
}
