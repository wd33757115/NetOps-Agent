import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

const roots = ['src', 'public']
const rootFiles = ['index.html']
const extensions = new Set(['.css', '.html', '.js', '.jsx', '.json', '.ts', '.tsx'])
const decoder = new TextDecoder('utf-8', { fatal: true })
const suspiciousText = /\uFFFD|Ã.|Â.|â(?:€|™|œ)|锟斤拷/u
const failures = []

async function inspectFile(filePath) {
  if (!extensions.has(path.extname(filePath))) return
  const bytes = await readFile(filePath)
  let text
  try {
    text = decoder.decode(bytes)
  } catch {
    failures.push(`${filePath}: invalid UTF-8`)
    return
  }
  const lines = text.split(/\r?\n/u)
  lines.forEach((line, index) => {
    if (suspiciousText.test(line)) {
      failures.push(`${filePath}:${index + 1}: suspicious mojibake`)
    }
  })
}

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  await Promise.all(entries.map((entry) => {
    const entryPath = path.join(directory, entry.name)
    return entry.isDirectory() ? walk(entryPath) : inspectFile(entryPath)
  }))
}

await Promise.all([
  ...roots.map(walk),
  ...rootFiles.map(inspectFile),
])

if (failures.length) {
  console.error(failures.join('\n'))
  process.exitCode = 1
} else {
  console.log('UTF-8 source scan passed')
}
