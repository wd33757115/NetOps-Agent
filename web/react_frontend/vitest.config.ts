import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // Bound DOM-heavy suites so parallel workers do not exhaust CI memory/CPU.
    maxWorkers: 4,
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      reporter: ['text', 'html'],
    },
  },
})
