import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const candidates = process.platform === 'win32'
  ? ['venv/Scripts/python.exe', '.venv/Scripts/python.exe']
  : ['.venv/bin/python', 'venv/bin/python']
const python = process.env.NETOPS_PYTHON || candidates.map(p => path.join(root, p)).find(existsSync) || 'python'
execFileSync(python, ['-m', 'src.platform.kernel.cli', 'frontend'], { cwd: root, stdio: 'inherit' })
