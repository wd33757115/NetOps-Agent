import { defineConfig } from 'vitest/config'
import { hostDependencies } from './scripts/package-resolve.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const base = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  server: { fs: { allow: [path.resolve(base, '../..')] } },
  resolve: { alias: { '@netops': path.join(base, 'src') }, dedupe: hostDependencies },
  test: {
    // Bound DOM-heavy suites so parallel workers do not exhaust CI memory/CPU.
    maxWorkers: 4,
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', '../../src/apps/*/frontend/**/*.test.{ts,tsx}'],
    coverage: {
      reporter: ['text', 'html'],
    },
  },
})
