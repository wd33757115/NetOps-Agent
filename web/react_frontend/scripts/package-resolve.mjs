import { readFileSync } from 'node:fs'
const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
// Package source is outside the Vite root; resolve all host dependencies once.
export const hostDependencies = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies })
